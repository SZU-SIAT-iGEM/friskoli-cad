from __future__ import annotations

import json
import threading
import unittest
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

from friskoli_cad.replay_service import (EXAMPLE_PROJECT, MAX_STEPS, MAX_REPLAY_BYTES,
    MAX_REPLAY_CELL_FRAMES, ReplayHandler, ReplayRequestError, build_replay,
    prepare_project, STATIC_FILES, _json_size, _replay_envelope)


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class ReplayServiceTests(unittest.TestCase):
    def test_validation_does_not_advance_or_change_the_submitted_project(self):
        project = load("adder_division.project.json")
        before = deepcopy(project)
        simulation = prepare_project(project, dt_s=.5, steps=8)
        self.assertEqual(simulation.current.cell_frame["time_s"], 0)
        self.assertEqual(project, before)

    def test_limits_reject_huge_grid_before_world_allocation(self):
        project = load("adder_division.project.json")
        project["domain"]["counts_xyz"] = [100000000, 100000000, 1]
        with self.assertRaises(ReplayRequestError):
            prepare_project(project, dt_s=.5, steps=8)
    def test_real_adder_frames_keep_protocol_and_division_ids(self):
        project = load("adder_division.project.json")
        replay = build_replay(project, dt_s=0.5, steps=8)
        self.assertEqual(replay["replay_format_version"], "0.1.0")
        self.assertEqual(replay["run"], project["run"])
        self.assertEqual([len(item["frame"]["cells"]) for item in replay["snapshots"]],
                         [1, 1, 1, 1, 2, 2, 2, 2, 4])
        first = replay["snapshots"][4]["frame"]
        self.assertEqual(first["frame_version"], "0.2.0")
        self.assertEqual(first["events"], [{
            "type": "division", "time_s": 2.0, "parent_id": "cell_0", "child_id": "cell_0~1",
        }])
        self.assertEqual(set(replay["snapshots"][0]["concentrations"]), set())

    def test_running_fields_are_serialized_without_activating_registered_only_species(self):
        project = load("scheduled_inputs.project.json")
        replay = build_replay(project, dt_s=0.5, steps=3)
        self.assertEqual(replay["snapshots"][0]["frame"]["protocol_version"], "0.1.0")
        self.assertNotIn("frame_version", replay["snapshots"][0]["frame"])
        self.assertEqual(set(replay["snapshots"][0]["concentrations"]), {"substrate", "oxygen"})
        field = replay["snapshots"][1]["concentrations"]["substrate"]
        self.assertEqual(field["unit"], "uM")
        self.assertEqual(len(field["values_zyx"]), 1)
        self.assertEqual(len(field["values_zyx"][0]), 2)
        self.assertEqual(len(field["values_zyx"][0][0]), 4)

    def test_invalid_controls_and_oversized_view_are_rejected(self):
        project = load("adder_division.project.json")
        with self.assertRaisesRegex(ReplayRequestError, "steps"):
            build_replay(project, dt_s=0.5, steps=True)
        with self.assertRaisesRegex(ReplayRequestError, "dt_s"):
            build_replay(project, dt_s=float("nan"), steps=1)
        large = deepcopy(project)
        large["domain"]["counts_xyz"] = [5000, 2, 1]
        with self.assertRaisesRegex(ReplayRequestError, "local viewer"):
            build_replay(large, dt_s=0.5, steps=1)

    def test_long_replay_accepts_1900_steps_within_viewer_budget(self):
        project = load("scheduled_inputs.project.json")
        before = deepcopy(project)
        replay = build_replay(project, dt_s=0.5, steps=1900)
        self.assertEqual(len(replay["snapshots"]), 1901)
        self.assertEqual([item["frame"]["time_s"] for item in replay["snapshots"]],
                         [index * .5 for index in range(1901)])
        self.assertEqual(project, before)
        self.assertLessEqual(1900, MAX_STEPS)

    def test_small_project_admits_10000_steps(self):
        simulation = prepare_project(load("scheduled_inputs.project.json"), dt_s=.5, steps=10000)
        self.assertEqual(simulation.current.cell_frame["time_s"], 0)

    def test_dense_long_replay_rejected_before_world_allocation(self):
        project = load("scheduled_inputs.project.json")
        group = project["groups"]["group_1"]
        group["ids"] = [f"cell_{index}" for index in range(2000)]
        group["positions_um"] = [group["positions_um"][0][:] for _ in range(2000)]
        group["orientation_xyzw"] = [group["orientation_xyzw"][0][:] for _ in range(2000)]
        with patch("friskoli_cad.replay_service.simulation_from_project") as construct:
            with self.assertRaises(ReplayRequestError) as rejected:
                build_replay(project, dt_s=.5, steps=10000)
            self.assertEqual(rejected.exception.code, "replay.cell_frames")
            self.assertEqual(rejected.exception.status, 413)
            construct.assert_not_called()

    def test_default_space_1900_still_requires_more_than_sync_field_budget(self):
        project = json.loads(EXAMPLE_PROJECT.read_text(encoding="utf-8"))
        with patch("friskoli_cad.replay_service.simulation_from_project") as construct:
            with self.assertRaises(ReplayRequestError) as rejected:
                build_replay(project, dt_s=.5, steps=1900)
            self.assertEqual(rejected.exception.code, "replay.size")
            self.assertEqual(rejected.exception.status, 413)
            construct.assert_not_called()
        # Async validation checks initial allocation, not synchronous output size.
        simulation = prepare_project(project, dt_s=.5, steps=1900, validation_only=True)
        self.assertEqual(simulation.current.cell_frame["time_s"], 0)

    def test_nonfinite_duration_rejected_before_world_allocation(self):
        with patch("friskoli_cad.replay_service.simulation_from_project") as construct:
            for initial_only in (False, True):
                with self.assertRaises(ReplayRequestError) as rejected:
                    prepare_project(load("scheduled_inputs.project.json"), dt_s=1e308,
                                    steps=2, validation_only=initial_only)
                self.assertEqual(rejected.exception.code, "replay.duration")
            construct.assert_not_called()

    def test_growing_population_stops_at_cumulative_cell_frame_budget(self):
        project = load("adder_division.project.json")
        with patch("friskoli_cad.replay_service.MAX_REPLAY_CELL_FRAMES", 10):
            # Initial admission is 9 cell-frames; division exceeds it at runtime.
            with self.assertRaises(ReplayRequestError) as rejected:
                build_replay(project, dt_s=.5, steps=8)
            self.assertEqual(rejected.exception.code, "replay.cell_frames")
            self.assertEqual(rejected.exception.status, 413)

    def test_result_byte_estimate_rejects_before_advancing_solver(self):
        project = load("scheduled_inputs.project.json")
        simulation = prepare_project(project, dt_s=.5, steps=2)
        with patch("friskoli_cad.replay_service.MAX_REPLAY_BYTES", 1), \
             patch("friskoli_cad.replay_service.simulation_from_project", return_value=simulation), \
             patch.object(type(simulation), "step") as step:
            with self.assertRaises(ReplayRequestError) as rejected:
                build_replay(project, dt_s=.5, steps=2)
            self.assertEqual(rejected.exception.code, "replay.bytes")
            step.assert_not_called()

    def test_result_byte_budget_also_checks_growing_frames(self):
        project = load("adder_division.project.json")
        baseline = build_replay(project, dt_s=.5, steps=8)
        estimate = _json_size(_replay_envelope(project)) + 1024
        estimate += (_json_size(baseline["snapshots"][0]) + 2) * 9
        self.assertGreater(_json_size(baseline) + 1024, estimate)
        with patch("friskoli_cad.replay_service.MAX_REPLAY_BYTES", estimate):
            with self.assertRaises(ReplayRequestError) as rejected:
                build_replay(project, dt_s=.5, steps=8)
            self.assertEqual(rejected.exception.code, "replay.bytes")
            self.assertEqual(rejected.exception.status, 413)

    def test_new_population_can_run_with_registered_static_module(self):
        project = json.loads(EXAMPLE_PROJECT.read_text(encoding="utf-8"))
        project["groups"]["population_1"] = {
            "ids": ["population_1:cell_0"],
            "positions_um": [[8, 8, 6]],
            "orientation_xyzw": [[0, 0, 0, 1]],
            "initial_geometry": [{"shape": "capsule", "length_um": 3, "diameter_um": 1.2,
                                  "provenance": {"kind": "estimated", "reference": "design scatter"}}],
        }
        project["graph"]["nodes"].append({
            "id": "population_1_static", "module_id": "population.static", "module_version": "1.0.0",
            "owner": {"kind": "population", "id": "population_1"}, "parameters": {},
        })
        project["run"]["groups"].append("population_1")
        replay = build_replay(project, dt_s=0.5, steps=4)
        first = next(cell for cell in replay["snapshots"][0]["frame"]["cells"]
                     if cell["id"] == "population_1:cell_0")
        last = next(cell for cell in replay["snapshots"][-1]["frame"]["cells"]
                    if cell["id"] == "population_1:cell_0")
        self.assertEqual(first["position_um"], last["position_um"])
        self.assertEqual(first["geometry"], last["geometry"])

    def test_http_serves_default_project_and_real_replay(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReplayHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            root = f"http://127.0.0.1:{server.server_port}"
            with urlopen(root + "/") as response:
                self.assertIn(b"<html lang=\"en\">", response.read())
            with urlopen(root + "/api/example-project") as response:
                project = json.load(response)
            self.assertEqual(project["domain"]["geometry"], "volume")
            with urlopen(root + "/api/modules") as response:
                catalog = json.load(response)
            with urlopen(root + "/api/capabilities") as response:
                capabilities = json.load(response)
            self.assertEqual(capabilities["api_version"], "0.2.0")
            self.assertEqual(capabilities["limits"]["steps"], MAX_STEPS)
            self.assertEqual(capabilities["limits"]["replay_bytes"], MAX_REPLAY_BYTES)
            self.assertEqual(capabilities["limits"]["replay_cell_frames"], MAX_REPLAY_CELL_FRAMES)
            self.assertIn("0.3.0", capabilities["workspace_versions"])
            self.assertEqual(capabilities["catalog_versions"], ["0.1.0", "0.2.0", "0.3.0", "0.4.0"])
            with urlopen(root + "/api/catalog") as response:
                registry = json.load(response)
            self.assertEqual(registry["modules"], catalog["modules"])
            self.assertEqual(registry["objects"][0]["id"], "core.population")
            with urlopen(root + "/api/examples/registry-readout") as response:
                example = json.load(response)
            replay_example = build_replay(example, dt_s=.1, steps=2)
            self.assertEqual(len(replay_example["snapshots"]), 3)
            self.assertFalse(capabilities["execution"]["pause"])
            for path in STATIC_FILES:
                with urlopen(root + path) as response:
                    self.assertTrue(response.read(), path)
            validation = Request(root + "/api/validate", method="POST",
                                 data=json.dumps({"project": project, "dt_s": .5, "steps": 4}).encode())
            with urlopen(validation) as response:
                self.assertTrue(json.load(response)["valid"])
            long_validation = Request(root + "/api/validate", method="POST",
                data=json.dumps({"project": project, "dt_s": .5, "steps": 1900}).encode())
            with self.assertRaises(HTTPError) as rejected:
                urlopen(long_validation)
            self.assertEqual(rejected.exception.code, 413)
            self.assertEqual(json.load(rejected.exception)["error"]["code"], "replay.size")
            server.task_service = object()
            try:
                with urlopen(long_validation) as response:
                    self.assertTrue(json.load(response)["valid"])
            finally:
                del server.task_service
            invalid = deepcopy(project)
            invalid["graph"]["edges"] = []
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(root + "/api/validate", method="POST", data=json.dumps({"project": invalid, "dt_s": .5, "steps": 4}).encode()))
            error = json.load(rejected.exception)["error"]
            self.assertIn("path", error)
            self.assertEqual(rejected.exception.code, 422)
            self.assertTrue(any(item["id"] == "growth.linear_elongation" for item in catalog["modules"]))
            self.assertTrue(any(item["id"] == "population.static" for item in catalog["modules"]))
            request = Request(root + "/api/replay", method="POST",
                              data=json.dumps({"project": project, "dt_s": 0.5, "steps": 4, "request_id": "test-run"}).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                replay = json.load(response)
            self.assertEqual(replay["snapshots"][4]["frame"]["events"][0]["type"], "division")
            self.assertEqual(replay["execution"]["request_id"], "test-run")
            self.assertEqual(len(replay["execution"]["project_sha256"]), 64)
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
