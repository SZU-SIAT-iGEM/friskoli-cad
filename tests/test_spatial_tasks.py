"""Real spawned workers for the spatial and existing task profiles."""
from copy import deepcopy
from importlib.resources import files
import hashlib
import json
import time
import pytest

from friskoli_cad.engine.profiles import LEGACY_PROFILE, PTS_PROFILE, SPATIAL_PROFILE, registry_for_profile
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import _validator, canonical_loads, sha256
from friskoli_cad.tasks import TaskError, TaskService
from friskoli_cad.tasks.metadata import estimate, provenance


def project(name="spatial_baseline"):
    return json.loads(files("friskoli_cad").joinpath("examples", name + ".project.json").read_text(encoding="utf-8"))


def body(service, doc=None, *, fields=True, seed=93):
    doc = project() if doc is None else doc
    profile = doc.get("execution_profile", LEGACY_PROFILE)
    version = {LEGACY_PROFILE:"0.1.0", PTS_PROFILE:"0.2.0", SPATIAL_PROFILE:"0.3.0"}[profile]
    return {"task_contract_version":version, "request_id":"spatial-request", "edit_revision":"test:1",
        "project":doc, "version_lock":service.version_lock(doc),
        "execution":{"semantics":profile,"backend":"numpy-cpu","dt_s":.01,"steps":2,"seed":seed},
        "output_plan":{"frame_every_steps":1,"observables":list(doc["run"]["channels"]),"include_fields":fields}}


def terminal(service, run_id):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        task = service.get(run_id)
        if task["status"] in {"completed","failed","cancelled","interrupted"}: return task
        time.sleep(.01)
    raise AssertionError("task did not finish")


@pytest.mark.parametrize("name,version,include_fields", [("workspace_3d","0.1.0",False), ("pts_bulk","0.2.0",False), ("spatial_baseline","0.3.0",True)])
def test_three_real_profiles_complete_with_independent_contracts(tmp_path, name, version, include_fields):
    with TaskService(tmp_path / "tasks") as service:
        submission = body(service, project(name), fields=include_fields)
        frozen = deepcopy(submission)
        task, _ = service.submit(submission, "complete")
        task = terminal(service, task["run_id"])
        assert task["status"] == "completed", task["issues"]
        manifest = service.manifest(task["run_id"])
        _validator("Manifest", version).validate(manifest)
        assert sha256(manifest["compiled_plan"]) == task["input_snapshot"]["plan_sha256"]
        assert service.input(task["run_id"]) == frozen
        simulation = simulation_from_project(submission["project"], seed=submission["execution"]["seed"])
        for index, descriptor in enumerate(manifest["chunks"]):
            raw = service.chunk(task["run_id"], descriptor["chunk_id"])
            assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
            chunk = canonical_loads(raw)
            _validator("ChunkBody", version).validate(chunk)
            item = chunk["frames"][0]
            snapshot = simulation.current if index == 0 else simulation.step(.01)
            assert item["frame"] == snapshot.cell_frame
            if version == "0.3.0":
                assert item["object_states"] == dict(snapshot.object_states)
            else:
                assert "object_states" not in item
            if include_fields:
                assert item["concentrations"] == {species:{"unit":"uM","values_zyx":values.tolist()} for species, values in snapshot.concentration_fields.items()}
            else:
                assert "concentrations" not in item
        if include_fields:
            assert manifest["compiled_plan"]["schedule"] == simulation.schedule
            assert manifest["provenance"]["rng"] == {"algorithm":"pcg64-sha256-key-v1","seed":93,"used":True}


def test_spatial_capabilities_and_disabled_fields_preserve_contract(tmp_path):
    with TaskService(tmp_path / "tasks") as service:
        cap = service.capabilities(SPATIAL_PROFILE)
        _validator("TaskCapabilities", "0.3.0").validate(cap)
        assert not any(cap[name] for name in ("pause","resume","checkpoint"))
        assert cap["limits"]["cells"] <= 256 and cap["limits"]["voxels"] <= 10000
        submission = body(service, fields=False)
        task, _ = service.submit(submission, "no-fields")
        task = terminal(service, task["run_id"])
        assert task["status"] == "completed", task["issues"]
        manifest = service.manifest(task["run_id"])
        for descriptor in manifest["chunks"]:
            assert "concentrations" not in canonical_loads(service.chunk(task["run_id"], descriptor["chunk_id"]))["frames"][0]


def test_fields_estimated_and_rejected_before_queue(tmp_path):
    with TaskService(tmp_path / "tasks", limits={"chunk_bytes":1024}) as service:
        submission = body(service)
        registry = registry_for_profile(SPATIAL_PROFILE)
        included = estimate(submission, registry)
        excluded = deepcopy(submission); excluded["output_plan"]["include_fields"] = False
        assert included["output_bytes"] > estimate(excluded, registry)["output_bytes"]
        with pytest.raises(TaskError) as error: service.submit(submission, "too-large")
        assert error.value.status == 413
        assert service._db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("name", ["workspace_3d", "pts_bulk"])
def test_old_contracts_keep_fields_false(tmp_path, name):
    with TaskService(tmp_path / "tasks") as service:
        with pytest.raises(TaskError): service.submit(body(service, project(name), fields=True), "old-fields")


def test_rng_used_reflects_positive_rate_and_nonempty_motion():
    doc = project()
    assert provenance(12, {}, doc)["rng"]["used"]
    for node in doc["graph"]["nodes"]:
        if node["module_id"] == "motion.unbiased_run_tumble": node["parameters"]["tumble_rate_s"]["value"] = 0
    assert not provenance(12, {}, doc)["rng"]["used"]


def test_http_spatial_discovery_sync_and_async_fields(tmp_path):
    import threading
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from friskoli_cad.replay_service import ReplayServer
    server = ReplayServer(("127.0.0.1", 0), task_directory=tmp_path / "http-tasks")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(path, value=None, key=None):
        data = None if value is None else json.dumps(value).encode()
        headers = {} if data is None else {"Content-Type":"application/json"}
        if key is not None: headers["Idempotency-Key"] = key
        try: response = urlopen(Request(f"http://127.0.0.1:{server.server_port}" + path, data=data, headers=headers), timeout=15)
        except HTTPError as error: response = error
        with response:
            raw = response.read()
            return response.status, json.loads(raw), raw
    try:
        status, doc, _ = request("/api/examples/spatial-baseline")
        assert status == 200 and doc == project()
        status, catalog, _ = request("/api/catalog?execution_profile=" + SPATIAL_PROFILE)
        assert status == 200 and catalog["catalog_version"] == "0.3.0"
        status, caps, _ = request("/api/capabilities")
        assert status == 200
        for profile, version in ((PTS_PROFILE,"0.2.0"),(SPATIAL_PROFILE,"0.3.0")):
            _validator("TaskCapabilities",version).validate(caps["task_profiles"][profile])
        _validator("TaskCapabilities").validate(caps["task"])
        assert "0.4.0" in caps["project_versions"]
        status, replay, _ = request("/api/replay", {"project":doc,"dt_s":.01,"steps":2})
        assert status == 200, replay
        submission = body(server.task_service, doc, seed=doc["random_seed"])
        status, task, _ = request("/api/runs", submission, "spatial-http")
        assert status == 202, task
        task = terminal(server.task_service, task["run_id"])
        assert task["status"] == "completed", task["issues"]
        status, manifest, _ = request(task["result"]["href"])
        assert status == 200
        _validator("Manifest","0.3.0").validate(manifest)
        for index, descriptor in enumerate(manifest["chunks"]):
            status, chunk, raw = request(descriptor["href"])
            assert status == 200 and hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
            _validator("ChunkBody","0.3.0").validate(chunk)
            item = chunk["frames"][0]
            assert item["frame"] == replay["snapshots"][index]["frame"]
            assert item["concentrations"] == replay["snapshots"][index]["concentrations"]
            assert item["object_states"] == replay["snapshots"][index]["object_states"]
        submission["project"]["random_seed"] = -1
        status, error, _ = request("/api/runs", submission, "invalid-spatial-http")
        assert status == 422 and error["task_contract_version"] == "0.1.0"
        _validator("CommonError","0.3.0").validate(error)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_task_publishes_real_exhausted_material_even_without_fields(tmp_path):
    doc=project()
    materials=[node for node in doc["graph"]["nodes"] if node["module_id"] == "material.degradable_box"]
    assert materials
    for node in materials: node["parameters"]["initial_molecules"]["value"] = .01
    with TaskService(tmp_path / "tasks") as service:
        submission=body(service,doc,fields=False)
        task,_=service.submit(submission,"material-exhaustion")
        task=terminal(service,task["run_id"])
        assert task["status"] == "completed",task["issues"]
        manifest=service.manifest(task["run_id"])
        snapshots=[canonical_loads(service.chunk(task["run_id"],desc["chunk_id"]))["frames"][0] for desc in manifest["chunks"]]
        for node in materials:
            assert snapshots[0]["object_states"][node["id"]]["remaining_molecules"] == .01
            assert snapshots[-1]["object_states"][node["id"]]["remaining_molecules"] == 0
        assert all("concentrations" not in snapshot for snapshot in snapshots)
        for snapshot in snapshots: _validator("FrameEnvelope","0.3.0").validate(snapshot)


def test_http_placeable_capabilities_match_each_profile_catalog():
    """Discovery must enable only the object initializers in the active catalog."""
    import threading
    from urllib.request import urlopen
    from friskoli_cad.replay_service import ReplayServer
    server = ReplayServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def get(path):
        with urlopen(f"http://127.0.0.1:{server.server_port}" + path, timeout=10) as response:
            assert response.status == 200
            return json.load(response)
    try:
        caps = get("/api/capabilities")
        assert set(caps["placeable_profiles"]) == set(caps["execution_profiles"])
        for profile in caps["execution_profiles"]:
            catalog = get("/api/catalog?execution_profile=" + profile)
            assert caps["placeable_profiles"][profile] == [
                item["initializer"]["module"] for item in catalog["objects"]]
        legacy = get("/api/catalog")
        assert caps["placeables"] == [{"kind": item["kind"], "module": item["initializer"]["module"]}
                                     for item in legacy["objects"]]
        assert "material.degradable_box@1.0.0" in caps["placeable_profiles"][SPATIAL_PROFILE]
        assert "space.axis_aligned_obstacle@1.0.0" in caps["placeable_profiles"][SPATIAL_PROFILE]
        assert "source.finite_local@1.0.0" in caps["placeable_profiles"][SPATIAL_PROFILE]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
