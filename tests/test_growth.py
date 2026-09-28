from __future__ import annotations

import json
import math
import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from friskoli_cad.engine import (
    CapsuleGeometry, CellGroup, GridDomain, Simulation, SimulationError, World, default_registry,
)
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import ProtocolError, validate_frame_sequence


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


class GrowthTests(unittest.TestCase):
    def test_project_growth_is_visible_in_versioned_frames(self):
        document = load("linear_growth.project.json")
        simulation = simulation_from_project(document)
        frames = [simulation.current.cell_frame]
        self.assertEqual(set(simulation.current.concentration_fields), set())
        for _ in range(3):
            frames.append(simulation.step(0.5).cell_frame)
        validate_frame_sequence(frames, document["run"])
        self.assertEqual([frame["frame_version"] for frame in frames], ["0.2.0"] * 4)
        self.assertEqual([frame["cells"][0]["id"] for frame in frames], ["cell_0"] * 4)
        np.testing.assert_allclose(
            [frame["cells"][0]["geometry"]["length_um"] for frame in frames],
            [2.0, 2.1, 2.2, 2.3], atol=1e-12,
        )
        self.assertEqual(
            [frame["cells"][0]["geometry"]["diameter_um"] for frame in frames],
            [0.8] * 4,
        )

    def test_old_project_still_emits_original_frame_format(self):
        document = load("scheduled_inputs.project.json")
        simulation = simulation_from_project(document)
        frames = [simulation.current.cell_frame, simulation.step(0.25).cell_frame]
        validate_frame_sequence(frames, document["run"])
        self.assertNotIn("frame_version", frames[0])
        self.assertNotIn("geometry", frames[0]["cells"][0])

    def test_new_project_without_growth_reports_static_or_unknown_geometry(self):
        document = load("scheduled_inputs.project.json")
        document["project_version"] = "0.2.0"
        unknown = simulation_from_project(document)
        self.assertEqual(unknown.current.cell_frame["frame_version"], "0.2.0")
        self.assertIsNone(unknown.current.cell_frame["cells"][0]["geometry"])
        known_document = deepcopy(document)
        known_document["groups"]["group_1"]["initial_geometry"] = [{
            "shape": "capsule", "length_um": 2.0, "diameter_um": 0.8,
            "provenance": {"kind": "example", "reference": "static geometry check"},
        }]
        known = simulation_from_project(known_document)
        frames = [known.current.cell_frame, known.step(0.25).cell_frame]
        validate_frame_sequence(frames, known_document["run"])
        self.assertEqual(
            [frame["cells"][0]["geometry"]["length_um"] for frame in frames], [2.0, 2.0]
        )

    def test_growth_requires_known_initial_geometry(self):
        document = load("linear_growth.project.json")
        document["groups"]["group_1"]["initial_geometry"] = [None]
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(document)
        self.assertEqual(caught.exception.code, "growth.geometry")

    def test_overgrowth_rejects_step_without_changing_previous_geometry(self):
        document = load("linear_growth.project.json")
        group = document["groups"]["group_1"]
        group["initial_geometry"][0]["length_um"] = 1.0
        group["initial_geometry"][0]["diameter_um"] = 0.6
        group["orientation_xyzw"] = [[0, math.sin(math.pi / 12), 0, math.cos(math.pi / 12)]]
        document["graph"]["nodes"][0]["parameters"]["elongation_rate"]["value"] = 1.2
        simulation = simulation_from_project(document)
        with self.assertRaises(SimulationError) as caught:
            simulation.step(0.5)
        self.assertEqual(caught.exception.code, "cell.geometry")
        self.assertEqual((simulation.time_s, simulation.frame_index), (0, 0))
        self.assertEqual(simulation.world.groups["group_1"].geometry[0], CapsuleGeometry(1.0, 0.6))
        self.assertEqual(simulation.current.cell_frame["cells"][0]["geometry"]["length_um"], 1.0)

    def test_two_cells_keep_individual_sizes(self):
        document = load("linear_growth.project.json")
        group = document["groups"]["group_1"]
        group["ids"].append("cell_1")
        group["positions_um"].append([3.5, 2.5, 0.5])
        group["orientation_xyzw"].append([0, 0, 0, 1])
        other = deepcopy(group["initial_geometry"][0])
        other["length_um"] = 3.0
        group["initial_geometry"].append(other)
        simulation = simulation_from_project(document)
        frame = simulation.step(0.5).cell_frame
        self.assertEqual([cell["id"] for cell in frame["cells"]], ["cell_0", "cell_1"])
        np.testing.assert_allclose(
            [cell["geometry"]["length_um"] for cell in frame["cells"]], [2.1, 3.1]
        )

    def test_two_geometry_providers_are_rejected(self):
        document = load("linear_growth.project.json")
        other = deepcopy(document["graph"]["nodes"][0])
        other["id"] = "second_elongation"
        document["graph"]["nodes"].append(other)
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(document)
        self.assertEqual(caught.exception.code, "growth.geometry")

    def test_frame_validator_rejects_bad_geometry_and_version_mixing(self):
        document = load("linear_growth.project.json")
        simulation = simulation_from_project(document)
        valid = [simulation.current.cell_frame, simulation.step(0.5).cell_frame]
        bad_size = deepcopy(valid)
        bad_size[1]["cells"][0]["geometry"]["length_um"] = 0.7
        with self.assertRaises(ProtocolError) as caught:
            validate_frame_sequence(bad_size, document["run"])
        self.assertEqual(caught.exception.code, "cell.geometry")
        missing = deepcopy(valid)
        del missing[1]["cells"][0]["geometry"]
        with self.assertRaises(ProtocolError) as caught:
            validate_frame_sequence(missing, document["run"])
        self.assertEqual(caught.exception.code, "schema.invalid")
        mixed = deepcopy(valid)
        del mixed[1]["frame_version"]
        del mixed[1]["cells"][0]["geometry"]
        with self.assertRaises(ProtocolError) as caught:
            validate_frame_sequence(mixed, document["run"])
        self.assertEqual(caught.exception.code, "frame.version")

    def test_growth_and_motion_keep_existing_uptake_results(self):
        graph = load("moving_uptake.graph.json")
        run = load("uptake.run.json")
        run["graph_id"] = graph["id"]
        group = CellGroup(
            "group_1", ("cell_0",), np.array([[2.5, 2.5, 0.5]]),
            np.array([[0, 0, 0, 1]]), (CapsuleGeometry(2.0, 0.8),),
        )
        world = World(GridDomain.thin_layer(3, 3, 5, 5, 1), {group.id: group})
        baseline = Simulation(world, graph, run, default_registry())
        growing_graph = deepcopy(graph)
        growing_graph["nodes"].append(deepcopy(load("linear_growth.project.json")["graph"]["nodes"][0]))
        growing = Simulation(world, growing_graph, run, default_registry())
        for index in range(2):
            previous = baseline.step(0.25)
            current = growing.step(0.25)
            np.testing.assert_array_equal(
                current.concentration_fields["substrate"], previous.concentration_fields["substrate"]
            )
            self.assertEqual(current.cell_frame["cells"][0]["position_um"],
                             previous.cell_frame["cells"][0]["position_um"])
            self.assertEqual(current.cell_frame["cells"][0]["channels"],
                             previous.cell_frame["cells"][0]["channels"])
            self.assertAlmostEqual(current.cell_frame["cells"][0]["geometry"]["length_um"],
                                   2.0 + 0.05 * (index + 1))


if __name__ == "__main__":
    unittest.main()
