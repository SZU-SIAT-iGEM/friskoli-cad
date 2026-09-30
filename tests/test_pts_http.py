"""N3 profile negotiation and real HTTP/spawn execution, with temporary storage."""

from copy import deepcopy
import hashlib
import json
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from friskoli_cad.engine.profiles import LEGACY_PROFILE, PTS_PROFILE
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import _validator, sha256
from friskoli_cad.replay_service import ReplayServer


@pytest.fixture
def http_service(tmp_path):
    server = ReplayServer(("127.0.0.1", 0), task_directory=tmp_path / "tasks")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"

    def request(path, value=None, *, raw=None, key=None):
        headers = {}
        if value is not None:
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if raw is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        try:
            response = urlopen(Request(root + path, data=raw, headers=headers), timeout=15)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, dict(response.headers), json.loads(body), body

    try:
        yield request
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def submission(request, *, dt=0.01, steps=3):
    status, _, project, _ = request("/api/examples/pts-bulk")
    assert status == 200
    status, _, capabilities, _ = request("/api/capabilities")
    assert status == 200
    caps = capabilities["task_profiles"][PTS_PROFILE]
    return {
        "task_contract_version": "0.2.0", "request_id": "pts-http-request", "edit_revision": "pts-http-edit",
        "project": project, "version_lock": caps["version_lock"],
        "execution": {"semantics": PTS_PROFILE, "backend": "numpy-cpu", "seed": 0, "dt_s": dt, "steps": steps},
        "output_plan": {"frame_every_steps": 1, "observables": list(project["run"]["channels"]), "include_fields": False},
    }


def terminal(request, run_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, _, task, _ = request(f"/api/runs/{run_id}")
        assert status == 200
        _validator("Task", "0.2.0").validate(task)
        if task["status"] not in ("queued", "running"):
            return task
        time.sleep(0.02)
    raise AssertionError("HTTP PTS task did not reach a terminal state")


def test_profile_discovery_and_validation_preserve_legacy_default(http_service):
    request = http_service
    status, _, caps, _ = request("/api/capabilities")
    assert status == 200
    _validator("TaskCapabilities").validate(caps["task"])
    _validator("TaskCapabilities", "0.2.0").validate(caps["task_profiles"][PTS_PROFILE])
    assert caps["task"]["task_contract_version"] == "0.1.0"
    assert caps["task"]["execution"]["semantics"] == LEGACY_PROFILE
    assert caps["task_profiles"][PTS_PROFILE]["task_contract_version"] == "0.2.0"
    assert "0.3.0" in caps["project_versions"]
    assert "0.2.0" in caps["catalog_versions"]
    status, _, legacy_catalog, _ = request("/api/catalog")
    assert status == 200 and legacy_catalog["catalog_version"] == "0.1.0"
    status, _, pts_catalog, _ = request(f"/api/catalog?execution_profile={PTS_PROFILE}")
    assert status == 200 and pts_catalog["catalog_version"] == "0.2.0"
    assert pts_catalog["execution_semantics"] == PTS_PROFILE
    body = submission(request)
    assert body["project"]["project_version"] == "0.3.0"
    status, _, validation, _ = request("/api/validate", {
        "project": body["project"], "dt_s": 0.01, "steps": 3,
    })
    assert status == 200, validation
    assert validation["valid"] and validation["issues"] == []
    assert validation["cells"] == 3
    assert validation["voxels"] == 2


@pytest.mark.parametrize("query", [
    "execution_profile=unknown", "unexpected=1", "execution_profile=",
    f"execution_profile={PTS_PROFILE}&execution_profile={PTS_PROFILE}",
    f"execution_profile={PTS_PROFILE}&unexpected=1",
])
def test_unknown_empty_or_ambiguous_catalog_query_is_rejected(http_service, query):
    status, _, error, _ = http_service("/api/catalog?" + query)
    assert status == 422, error
    assert error["error"]["code"] == "catalog.profile"


def test_http_pts_task_publishes_versioned_locked_verified_results(http_service):
    request = http_service
    body = submission(request)
    frozen = deepcopy(body)
    status, headers, task, _ = request("/api/runs", body, key="pts-http-complete")
    assert status == 202, task
    _validator("Task", "0.2.0").validate(task)
    run_id = task["run_id"]
    assert headers["Location"] == f"/api/runs/{run_id}"
    body["project"]["id"] = "changed-locally"
    status, _, stored, _ = request(f"/api/runs/{run_id}/input")
    assert status == 200 and stored == frozen
    _validator("Submission", "0.2.0").validate(stored)
    task = terminal(request, run_id)
    assert task["status"] == "completed", task["issues"]
    assert task["progress"]["committed_step"] == 3
    status, _, manifest, _ = request(f"/api/runs/{run_id}/result")
    assert status == 200
    _validator("Manifest", "0.2.0").validate(manifest)
    assert manifest["compiled_plan"]["execution_semantics"] == PTS_PROFILE
    assert manifest["compiled_plan"]["plan_version"] == "0.2.0"
    assert sha256(manifest["compiled_plan"]) == task["input_snapshot"]["plan_sha256"]
    assert manifest["input_snapshot"]["registry_sha256"] == frozen["version_lock"]["registry_sha256"]
    # Check the persisted bindings against the actual runner's compiled graph,
    # and the transported frames against a direct run of the same snapshot.
    direct = simulation_from_project(frozen["project"])
    assert manifest["compiled_plan"]["schedule"] == direct.schedule
    assert manifest["compiled_plan"]["schedule"]["read_boundary"] == "step_start"
    assert manifest["compiled_plan"]["schedule"]["commit"] == "atomic"
    plan = manifest["compiled_plan"]["nodes"]
    assert [node["id"] for node in plan] == [node.id for node in direct.plan.nodes]
    for recorded, actual in zip(plan, direct.plan.nodes, strict=True):
        assert recorded["module_id"] == actual.module_id
        for name, binding in actual.inputs.items():
            assert recorded["inputs"][name] == {
                "source_node": binding.source_node, "source_port": binding.source_port, "timing": binding.timing,
            }
    assert len(manifest["chunks"]) == 4
    for step, descriptor in enumerate(manifest["chunks"]):
        status, _, chunk, raw = request(descriptor["href"])
        assert status == 200
        assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        assert len(raw) == descriptor["bytes"]
        _validator("ChunkBody", "0.2.0").validate(chunk)
        expected = direct.current if step == 0 else direct.step(0.01)
        assert chunk["frames"][0]["frame"] == expected.cell_frame
    status, _, page, _ = request(f"/api/runs/{run_id}/events")
    assert status == 200
    _validator("EventPage", "0.2.0").validate(page)
    assert page["events"][-1]["task"] == task
    assert all(event["task"]["task_contract_version"] == "0.2.0" for event in page["events"])
    status, _, same, _ = request("/api/runs", frozen, key="pts-http-complete")
    assert status == 200 and same["run_id"] == run_id


@pytest.mark.parametrize("change", ["task-version", "project-version", "execution-profile", "legacy-project", "legacy-lock"])
def test_http_mixed_versions_profiles_and_locks_are_rejected(http_service, change):
    request = http_service
    body = submission(request)
    if change == "task-version":
        body["task_contract_version"] = "0.1.0"
    elif change == "project-version":
        body["project"]["project_version"] = "0.2.0"
    elif change == "execution-profile":
        body["execution"]["semantics"] = LEGACY_PROFILE
    elif change == "legacy-project":
        body["project"] = request("/api/example-project")[2]
    else:
        body["version_lock"] = request("/api/capabilities")[2]["task"]["version_lock"]
    status, _, error, _ = request("/api/runs", body, key="mixed-http")
    assert status == 422, error
    # Error envelopes intentionally share the frozen 0.1 contract, even for
    # 0.2 submissions and requests rejected before their profile is known.
    assert error["task_contract_version"] == "0.1.0"
    _validator("Error").validate(error)
    _validator("Error", "0.2.0").validate(error)


def test_unparseable_http_request_uses_shared_error_schema(http_service):
    status, _, error, _ = http_service("/api/runs", raw=b'{"task_contract_version":"0.2.0","x":NaN}', key="bad-json")
    assert status == 400
    assert error["task_contract_version"] == "0.1.0"
    _validator("Error", "0.2.0").validate(error)


def test_numerical_failure_keeps_initial_http_frame_and_specific_issue(http_service):
    request = http_service
    body = submission(request, dt=500, steps=1)
    bulk = next(node for node in body["project"]["graph"]["nodes"] if node["module_id"] == "bulk.finite_uniform")
    bulk["parameters"]["allocation_policy"]["value"] = "strict"
    status, _, task, _ = request("/api/runs", body, key="strict-shortage")
    assert status == 202
    task = terminal(request, task["run_id"])
    assert task["status"] == "failed"
    assert task["progress"] == {"committed_step": 0, "simulation_time_s": 0}
    assert task["issues"][0]["code"] == "profile.step_rejected"
    assert "insufficient inventory" in task["issues"][0]["message"]
    status, _, manifest, _ = request(task["result"]["href"])
    assert status == 200 and manifest["completeness"] == "partial"
    _validator("Manifest", "0.2.0").validate(manifest)
    assert len(manifest["chunks"]) == 1
    status, _, chunk, _ = request(manifest["chunks"][0]["href"])
    assert status == 200
    assert chunk["frames"][0]["step_index"] == 0
