"""M4 acceptance across JSON restoration, late rejection and spawned workers.

All inputs are constructed numerical fixtures, not calibrated biological data.
Stable-ID permutation is already covered by test_spatial_runtime.
"""
from copy import deepcopy
from importlib.resources import files
import json
import time

import numpy as np
import pytest

from friskoli_cad.engine.runtime import SimulationError
from friskoli_cad.engine.spatial_runtime import SpatialSimulation
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import FrameSequenceValidator
from friskoli_cad.protocol.task_validation import canonical_loads
from friskoli_cad.tasks import TaskService


def project():
    return json.loads(files("friskoli_cad").joinpath(
        "examples", "spatial_baseline.project.json").read_text(encoding="utf-8"))


def set_parameter(doc, node_id, name, value):
    node = next(n for n in doc["graph"]["nodes"] if n["id"] == node_id)
    node["parameters"][name]["value"] = value


def assert_complete_state_equal(left, right):
    # The exported payload covers fields, inventories, outputs, memories, RNG,
    # residual tumble waits and ledgers. Also compare live diagnostic owners,
    # so omissions from the serializer cannot silently weaken this acceptance.
    assert left.checkpoint() == right.checkpoint()
    assert vars(left.frame_validator) == vars(right.frame_validator)
    assert left.motion_contacts == right.motion_contacts
    assert left.obstacles == right.obstacles
    assert left.schedule == right.schedule
    assert left.current.cell_frame == right.current.cell_frame
    assert left.current.object_states == right.current.object_states
    for species in left.current.concentration_fields:
        np.testing.assert_array_equal(left.current.concentration_fields[species],
                                      right.current.concentration_fields[species])


def test_fifty_steps_equal_twenty_json_restore_and_thirty_steps():
    doc = project()
    uninterrupted = simulation_from_project(doc, seed=93)
    segmented = simulation_from_project(doc, seed=93)
    for _ in range(20):
        uninterrupted.step(.1)
        segmented.step(.1)
        assert_complete_state_equal(uninterrupted, segmented)
    payload = json.loads(json.dumps(segmented.checkpoint(), allow_nan=False))
    # A real JSON boundary, independent newly constructed runtime and no reseed.
    resumed = SpatialSimulation.from_checkpoint(doc, payload)
    assert_complete_state_equal(uninterrupted, resumed)
    assert any(w.remaining_wait_s is not None for w in resumed.walks.values())
    for _ in range(30):
        uninterrupted.step(.1)
        resumed.step(.1)
        assert_complete_state_equal(uninterrupted, resumed)
    assert resumed.frame_index == 50
    assert resumed.fields.revision == 50
    assert resumed.frame_validator.next_index == 51


def test_validator_rejection_rolls_back_exhaustion_randomness_and_retry(monkeypatch):
    doc = project()
    set_parameter(doc, "material", "initial_molecules", 3.)
    set_parameter(doc, "attractant_source", "initial_molecules", 1.)
    set_parameter(doc, "attractant_source", "release_rate", 10.)
    doc["species"]["nutrient"]["initial_concentration"]["value"] = 1.
    for gid in doc["groups"]:
        set_parameter(doc, gid + "_motion", "speed_um_s", 0.)
        set_parameter(doc, gid + "_motion", "tumble_rate_s", 100.)
    actual = simulation_from_project(doc, seed=93)
    reference = simulation_from_project(doc, seed=93)
    before = actual.checkpoint()
    owners = {key: getattr(actual, key) for key in (
        "world", "fields", "materials", "material_ledger", "obstacles", "walks",
        "streams", "outputs", "state", "ledger", "current", "frame_validator")}
    accepted_candidates = []
    candidate_streams = []
    real_accept, real_move = FrameSequenceValidator.accept, SpatialSimulation._move

    def record_move(self, dt, streams):
        result = real_move(self, dt, streams)
        candidate_streams.append(streams.to_dict())
        return result

    def accept_then_reject(self, frame, **kwargs):
        real_accept(self, frame, **kwargs)
        accepted_candidates.append((deepcopy(vars(self)), deepcopy(frame)))
        raise SimulationError("test.late_rejection", "Reject after candidate validator advances")

    with monkeypatch.context() as patch:
        patch.setattr(SpatialSimulation, "_move", record_move)
        patch.setattr(FrameSequenceValidator, "accept", accept_then_reject)
        with pytest.raises(SimulationError, match="test.late_rejection"):
            actual.step(.1)
    assert candidate_streams and candidate_streams[0] != before["random_streams"]
    validator, rejected_frame = accepted_candidates[0]
    assert validator["next_index"] == 2 and validator["previous_time"] == .1
    assert any(cell["channels"][cell["group_id"] + "_motion.turns"] > 0
               for cell in rejected_frame["cells"])
    assert actual.checkpoint() == before
    for key, owner in owners.items():
        assert getattr(actual, key) is owner
    assert_complete_state_equal(actual, reference)

    actual.step(.1)
    reference.step(.1)
    assert_complete_state_equal(actual, reference)
    assert actual.materials["material"].remaining_molecules == 0.
    assert actual.fields.sources[0].remaining_molecules == 0.
    assert actual.fields.blocked != owners["fields"].blocked
    assert actual.obstacles != owners["obstacles"]
    assert actual.streams.to_dict() == candidate_streams[0]
    assert sum(actual.outputs[gid + "_settle"]["cumulative_uptake"].sum()
               for gid in doc["groups"]) > 0.
    for _ in range(4):
        actual.step(.1)
        reference.step(.1)
        assert_complete_state_equal(actual, reference)


def terminal(service, run_id):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        task = service.get(run_id)
        if task["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(.01)
    raise AssertionError("Spawned M4 worker did not finish within 30 seconds")


def test_real_task_output_cadence_preserves_common_frames_and_final_state(tmp_path):
    doc = project()
    with TaskService(tmp_path / "m4-tasks") as service:
        by_cadence = {}
        for every in (1, 3):
            submission = {
                "task_contract_version": "0.3.0", "request_id": "m4-cadence",
                "edit_revision": "constructed:m4", "project": doc,
                "version_lock": service.version_lock(doc),
                "execution": {"semantics": doc["execution_profile"], "backend": "numpy-cpu",
                              "dt_s": .1, "steps": 7, "seed": 93},
                "output_plan": {"frame_every_steps": every,
                                "observables": list(doc["run"]["channels"]), "include_fields": True},
            }
            task, _ = service.submit(submission, "m4-every-" + str(every))
            task = terminal(service, task["run_id"])
            assert task["status"] == "completed", task["issues"]
            manifest = service.manifest(task["run_id"])
            envelopes = [envelope for descriptor in manifest["chunks"]
                         for envelope in canonical_loads(service.chunk(
                             task["run_id"], descriptor["chunk_id"]))["frames"]]
            by_cadence[every] = {envelope["step_index"]: envelope for envelope in envelopes}
        dense, sparse = by_cadence[1], by_cadence[3]
        assert set(dense) == set(range(8))
        assert set(sparse) == {0, 3, 6, 7}  # The unscheduled final step must be published.
        for step, envelope in sparse.items():
            assert {key: value for key, value in envelope.items() if key != "sequence"} == {
                key: value for key, value in dense[step].items() if key != "sequence"}

        # Verify the final published fields, all cell channels and finite stocks
        # against the same unsampled numerical evolution outside TaskService.
        reference = simulation_from_project(doc, seed=93)
        for _ in range(7):
            reference.step(.1)
        final = sparse[7]
        assert final["frame"] == reference.current.cell_frame
        assert final["object_states"] == reference.current.object_states
        assert final["concentrations"] == {
            species: {"unit": "uM", "values_zyx": values.tolist()}
            for species, values in reference.current.concentration_fields.items()}
