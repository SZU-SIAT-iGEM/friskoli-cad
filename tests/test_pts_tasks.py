"""Profile-specific task locks and real spawned PTS worker integration."""

from copy import deepcopy
from importlib.resources import files
import json
import time

import pytest

from friskoli_cad.engine.profiles import LEGACY_PROFILE, PTS_PROFILE, registry_for_profile
from friskoli_cad.protocol.task_validation import _validator, canonical_loads, sha256
from friskoli_cad.tasks import TaskError, TaskService
from friskoli_cad.tasks.metadata import estimate, source_hashes


def pts_project():
    return json.loads(files("friskoli_cad").joinpath("examples", "pts_bulk.project.json").read_text(encoding="utf-8"))


def body(service, *, steps=3, every=1):
    project = pts_project()
    return {
        "task_contract_version": "0.2.0", "request_id": "pts-request", "edit_revision": "pts-edit",
        "project": project, "version_lock": service.version_lock(project),
        "execution": {"semantics": PTS_PROFILE, "backend": "numpy-cpu", "dt_s": 0.01, "steps": steps, "seed": 0},
        "output_plan": {"frame_every_steps": every, "observables": list(project["run"]["channels"]), "include_fields": False},
    }


def terminal(service, run_id):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        task = service.get(run_id)
        if task["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.01)
    raise AssertionError("PTS task did not complete within 20 seconds")


def test_both_capabilities_are_separate_and_schema_valid(tmp_path):
    with TaskService(tmp_path / "service") as service:
        legacy = service.capabilities()
        pts = service.capabilities(PTS_PROFILE)
        _validator("TaskCapabilities").validate(legacy)
        _validator("TaskCapabilities", "0.2.0").validate(pts)
        assert legacy["task_contract_version"] == "0.1.0"
        assert legacy["execution"]["semantics"] == LEGACY_PROFILE
        assert pts["task_contract_version"] == "0.2.0"
        assert pts["execution"]["semantics"] == PTS_PROFILE
        assert legacy["version_lock"]["registry_sha256"] != pts["version_lock"]["registry_sha256"]
        # Callers cannot mutate the stored lock through capability dictionaries.
        pts["version_lock"]["implementations"].clear()
        assert service.capabilities(PTS_PROFILE)["version_lock"]["implementations"]
        with pytest.raises(TaskError, match="Unsupported"):
            service.capabilities("unknown")
        with pytest.raises(TaskError, match="Unsupported"):
            service.version_lock({"execution_profile": "unknown"})


def test_real_pts_task_preserves_profile_versions_and_source_evidence(tmp_path):
    with TaskService(tmp_path / "service") as service:
        submission = body(service)
        task, created = service.submit(submission, "pts-complete")
        assert created
        _validator("Task", "0.2.0").validate(task)
        task = terminal(service, task["run_id"])
        assert task["status"] == "completed", task["issues"]
        manifest = service.manifest(task["run_id"])
        _validator("Manifest", "0.2.0").validate(manifest)
        assert manifest["task_contract_version"] == "0.2.0"
        assert manifest["compiled_plan"]["execution_semantics"] == PTS_PROFILE
        assert manifest["compiled_plan"]["plan_version"] == "0.2.0"
        assert sha256(manifest["compiled_plan"]) == task["input_snapshot"]["plan_sha256"]
        assert task["input_snapshot"]["registry_sha256"] == service.version_lock(submission["project"])["registry_sha256"]
        sources = manifest["provenance"]["source_sha256"]
        assert "science/pts.py" in sources
        assert "science/data/source_lock.json" in sources
        assert "science/data/evidence.json" in sources
        assert "engine/settlement.py" in sources
        assert len(manifest["chunks"]) == 4
        for step, descriptor in enumerate(manifest["chunks"]):
            chunk = canonical_loads(service.chunk(task["run_id"], descriptor["chunk_id"]))
            _validator("ChunkBody", "0.2.0").validate(chunk)
            assert chunk["task_contract_version"] == "0.2.0"
            assert chunk["frames"][0]["step_index"] == step
        again, created = service.submit(submission, "pts-complete")
        assert not created and again["run_id"] == task["run_id"]


@pytest.mark.parametrize("change", ["task-version", "semantics", "registry-lock", "implementation-lock"])
def test_cross_profile_or_wrong_locks_rejected_before_queue(tmp_path, change):
    with TaskService(tmp_path / "service") as service:
        submission = body(service)
        if change == "task-version":
            submission["task_contract_version"] = "0.1.0"
        elif change == "semantics":
            submission["execution"]["semantics"] = LEGACY_PROFILE
        elif change == "registry-lock":
            submission["version_lock"] = service.version_lock()
        else:
            submission["version_lock"]["implementations"][0]["sha256"] = "0" * 64
        with pytest.raises(TaskError) as caught:
            service.submit(submission, "invalid-profile")
        assert caught.value.status == 422
        assert service._db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_bulk_estimate_does_not_allocate_cell_times_voxels(tmp_path):
    with TaskService(tmp_path / "service") as service:
        submission = body(service)
        registry = registry_for_profile(PTS_PROFILE)
        small = estimate(submission, registry)
        large_submission = deepcopy(submission)
        large_submission["project"]["domain"]["counts_xyz"] = [100, 100, 100]
        large = estimate(large_submission, registry)
        assert large["voxels"] == 1_000_000
        assert large["memory_bytes"] == small["memory_bytes"]
        assert large["output_bytes"] == small["output_bytes"]


def test_science_sources_and_data_are_hashed_by_content():
    import hashlib
    sources = source_hashes()
    package = files("friskoli_cad")
    for name in ("science/pts.py", "science/data/source_lock.json", "science/data/evidence.json", "science/data/fixtures.json"):
        assert sources[name] == hashlib.sha256(package.joinpath(*name.split("/")).read_bytes()).hexdigest()
