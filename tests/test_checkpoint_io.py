"""File publication, strict decoding and standalone spatial continuation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from friskoli_cad.engine import checkpoint_io
from friskoli_cad.engine.checkpoint_io import (
    CheckpointFileError, checkpoint_document, load_checkpoint, save_checkpoint,
)
from friskoli_cad.engine.runtime import SimulationError
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import canonical_bytes


def simulation():
    project = json.loads(files("friskoli_cad").joinpath(
        "examples", "spatial_baseline.project.json").read_text(encoding="utf-8"))
    return simulation_from_project(project)


def reseal(document):
    document["document_sha256"] = hashlib.sha256(canonical_bytes(
        {key: value for key, value in document.items() if key != "document_sha256"})).hexdigest()
    return document


def test_file_contains_frozen_project_and_restores_without_external_project(tmp_path):
    sim = simulation()
    sim.step(.1)
    saved = sim.checkpoint()
    target = tmp_path / "state.friskoli-checkpoint.json"
    receipt = save_checkpoint(sim, target)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert receipt["bytes"] == target.stat().st_size
    assert document["project"] == sim.project
    assert document["checkpoint"] == saved
    restored = load_checkpoint(target)
    assert restored.checkpoint() == saved
    sim.step(.2)
    restored.step(.2)
    assert sim.checkpoint() == restored.checkpoint()
    document["project"]["id"] = "changed-local-copy"
    assert sim.project["id"] != "changed-local-copy"


def test_large_binary64_values_survive_file_json_tokens(tmp_path):
    sim = simulation()
    project = deepcopy(sim.project)
    source = next(n for n in project["graph"]["nodes"] if n["module_id"] == "source.finite_local")
    source["parameters"]["initial_molecules"]["value"] = 1e18
    sim = simulation_from_project(project)
    target = tmp_path / "wide-float.json"
    save_checkpoint(sim, target)
    assert load_checkpoint(target).checkpoint() == sim.checkpoint()


def test_existing_file_preserved_until_explicit_atomic_replacement(tmp_path):
    sim = simulation()
    target = tmp_path / "state.json"
    save_checkpoint(sim, target)
    before = target.read_bytes()
    sim.step(.1)
    with pytest.raises(CheckpointFileError, match="Destination exists") as rejected:
        save_checkpoint(sim, target)
    assert rejected.value.code == "checkpoint.exists"
    assert target.read_bytes() == before
    save_checkpoint(sim, target, overwrite=True)
    assert load_checkpoint(target).frame_index == 1
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_write_leaves_old_file_and_no_temporary_files(tmp_path, monkeypatch, operation):
    sim = simulation()
    target = tmp_path / "state.json"
    save_checkpoint(sim, target)
    before = target.read_bytes()
    sim.step(.1)

    def reject(*args, **kwargs):
        raise OSError("injected storage failure")

    monkeypatch.setattr(checkpoint_io.os, operation, reject)
    with pytest.raises(CheckpointFileError) as error:
        save_checkpoint(sim, target, overwrite=True)
    assert error.value.code == "checkpoint.write"
    assert target.read_bytes() == before
    assert list(tmp_path.iterdir()) == [target]


def test_no_partial_destination_when_publication_fails(tmp_path, monkeypatch):
    target = tmp_path / "new.json"

    def reject(*args):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(checkpoint_io.os, "link", reject)
    with pytest.raises(CheckpointFileError) as error:
        save_checkpoint(simulation(), target)
    assert error.value.code == "checkpoint.write"
    assert list(tmp_path.iterdir()) == []


def test_concurrent_creation_does_not_overwrite_the_first_publisher(tmp_path):
    target = tmp_path / "shared.json"
    first, second = simulation(), simulation()
    second.step(.1)

    def publish(sim):
        try:
            return save_checkpoint(sim, target)
        except CheckpointFileError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, (first, second)))
    receipts = [value for value in results if isinstance(value, dict)]
    failures = [value for value in results if isinstance(value, CheckpointFileError)]
    assert len(receipts) == len(failures) == 1
    assert failures[0].code == "checkpoint.exists"
    assert load_checkpoint(target).frame_index == receipts[0]["frame_index"]
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":1e10000}',
    b'{"x":"\\ud800"}', b'\xff', b'[]', b'{"x":9007199254740993}',
])
def test_bad_json_or_envelope_is_rejected_before_simulation(tmp_path, monkeypatch, raw):
    source = tmp_path / "bad.json"
    source.write_bytes(raw)

    def never_restore(*args):
        pytest.fail("Malformed file reached numerical restoration")

    monkeypatch.setattr("friskoli_cad.engine.spatial_checkpoint.restore_checkpoint", never_restore)
    monkeypatch.setattr("friskoli_cad.engine.chemotaxis_checkpoint.restore_checkpoint", never_restore)
    with pytest.raises(CheckpointFileError):
        load_checkpoint(source)


def test_envelope_and_inner_checksums_are_both_checked(tmp_path):
    document = checkpoint_document(simulation())
    target = tmp_path / "bad.json"
    document["checkpoint"]["time_s"] = 100.
    target.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(CheckpointFileError) as error:
        load_checkpoint(target)
    assert error.value.code == "checkpoint.file_hash"
    target.write_text(json.dumps(reseal(document)), encoding="utf-8")
    with pytest.raises(SimulationError, match="payload hash"):
        load_checkpoint(target)


@pytest.mark.parametrize("change", ["future", "extra", "project"])
def test_file_versions_unknown_fields_and_project_lock(tmp_path, change):
    document = checkpoint_document(simulation())
    if change == "future":
        document["checkpoint_file_version"] = "99.0.0"
    elif change == "extra":
        document["resume_task"] = True
    else:
        document["project"]["random_seed"] += 1
    target = tmp_path / "altered.json"
    target.write_text(json.dumps(reseal(document)), encoding="utf-8")
    with pytest.raises((CheckpointFileError, SimulationError)):
        load_checkpoint(target)


def test_byte_limits_apply_before_parsing_or_publishing(tmp_path, monkeypatch):
    target = tmp_path / "limited.json"
    target.write_bytes(b"preserve")
    with pytest.raises(CheckpointFileError) as error:
        save_checkpoint(simulation(), target, overwrite=True, max_bytes=8)
    assert error.value.code == "checkpoint.too_large"
    assert target.read_bytes() == b"preserve"
    monkeypatch.setattr(checkpoint_io, "strict_json_loads", lambda raw: pytest.fail("Oversized file was parsed"))
    with pytest.raises(CheckpointFileError) as error:
        load_checkpoint(target, max_bytes=7)
    assert error.value.code == "checkpoint.too_large"
    for limit in (0, -1, True, 3.5):
        with pytest.raises(CheckpointFileError) as error:
            load_checkpoint(target, max_bytes=limit)
        assert error.value.code == "checkpoint.limit"


def test_missing_or_unwritable_location_is_a_file_error(tmp_path):
    with pytest.raises(CheckpointFileError) as error:
        load_checkpoint(tmp_path / "missing.json")
    assert error.value.code == "checkpoint.read"
    with pytest.raises(CheckpointFileError) as error:
        save_checkpoint(simulation(), tmp_path / "missing-parent" / "state.json")
    assert error.value.code == "checkpoint.write"


def test_cli_continues_in_a_fresh_process_and_preserves_input(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")

    def run(*args):
        return subprocess.run([sys.executable, "-B", "-m", "friskoli_cad.checkpoint", *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)

    before = tmp_path / "step2.json"
    after = tmp_path / "step4.json"
    result = run("--example", "--steps", "2", "--dt", ".1", "--output", str(before))
    assert result.returncode == 0, result.stderr
    original = before.read_bytes()
    result = run("--resume", str(before), "--steps", "2", "--dt", ".1", "--output", str(after))
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["resumed"] and receipt["start"]["frame_index"] == 2
    assert receipt["checkpoint"]["frame_index"] == 4
    reference = simulation()
    for _ in range(4):
        reference.step(.1)
    assert load_checkpoint(after).checkpoint() == reference.checkpoint()
    assert before.read_bytes() == original
    result = run("--resume", str(before), "--steps", "1", "--dt", ".1", "--output", str(before))
    assert result.returncode == 1
    assert json.loads(result.stderr)["error"]["code"] == "checkpoint.exists"
    assert before.read_bytes() == original
