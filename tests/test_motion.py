from __future__ import annotations

import json
import math
import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, SimulationError, World, default_registry
from friskoli_cad.engine.motion import reflect_in_box
from friskoli_cad.protocol import validate_frame_sequence


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def make_simulation(
    grid: GridDomain, position: tuple[float, float, float],
    orientation: tuple[float, float, float, float] = (0, 0, 0, 1),
    *, diffusivity: float = 0, speed: float = 6, rate_constant: float = 100,
) -> tuple[Simulation, dict]:
    graph = json.loads((EXAMPLES / "moving_uptake.graph.json").read_text(encoding="utf-8"))
    for node in graph["nodes"]:
        if node["id"] == "diffusion":
            node["parameters"]["diffusivity"]["value"] = diffusivity
        elif node["id"] == "motion":
            node["parameters"]["speed_um_s"]["value"] = speed
        elif node["id"] == "uptake":
            node["parameters"]["rate_constant"]["value"] = rate_constant
    run = json.loads((EXAMPLES / "uptake.run.json").read_text(encoding="utf-8"))
    run["graph_id"] = graph["id"]
    group = CellGroup(
        "group_1", ("cell_0",), np.array([position]), np.array([orientation])
    )
    return Simulation(World(grid, {"group_1": group}), graph, run, default_registry()), run


class MotionTests(unittest.TestCase):
    def test_box_reflection_handles_repeated_hits_corners_and_exact_faces(self):
        positions = np.array([[1.0, 9.0, 4.0], [9.0, 9.0, 9.0], [9.0, 5.0, 5.0]])
        headings = np.array([[1.0, 0.0, 0.0], [2**-0.5, 2**-0.5, 0.0], [1.0, 0.0, 0.0]])
        moved, reflected = reflect_in_box(positions, headings, 27, (10, 10, 10), thin_layer=False)
        np.testing.assert_allclose(moved[0], [8, 9, 4])
        np.testing.assert_allclose(reflected[0], [1, 0, 0])
        self.assertTrue(np.all((moved >= 0) & (moved < 10)))

        corner, direction = reflect_in_box(
            positions[1:2], headings[1:2], math.sqrt(2), (10, 10, 10), thin_layer=False
        )
        self.assertTrue(np.all(corner < 10))
        np.testing.assert_allclose(direction[0], [-2**-0.5, -2**-0.5, 0])

        face, direction = reflect_in_box(
            positions[2:3], headings[2:3], 1, (10, 10, 10), thin_layer=False
        )
        self.assertLess(face[0, 0], 10)
        np.testing.assert_allclose(direction[0], [-1, 0, 0])

    def test_movement_uses_old_flux_then_samples_at_new_position(self):
        grid = GridDomain.thin_layer(3, 3, 5, 5, 1)
        simulation, run = make_simulation(grid, (2.5, 2.5, 0.5))
        initial_molecules = (
            simulation.current.concentration_fields["substrate"].sum()
            * grid.molecules_per_uM_voxel
        )
        frames = [simulation.current.cell_frame]
        first = simulation.step(0.5)
        frames.append(first.cell_frame)
        self.assertEqual(first.cell_frame["cells"][0]["id"], "cell_0")
        self.assertAlmostEqual(first.cell_frame["cells"][0]["position_um"][0], 5.5)
        field = first.concentration_fields["substrate"]
        self.assertLess(field[0, 0, 0], 10)
        self.assertAlmostEqual(field[0, 0, 1], 10)
        self.assertLess(first.cell_frame["cells"][0]["channels"]["uptake.flux"], 1000)

        second = simulation.step(0.5)
        frames.append(second.cell_frame)
        self.assertLess(second.concentration_fields["substrate"][0, 0, 1], 10)
        for _ in range(4):
            frames.append(simulation.step(0.25).cell_frame)
        latest = simulation.current
        uptake = latest.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
        remaining = latest.concentration_fields["substrate"].sum() * grid.molecules_per_uM_voxel
        self.assertAlmostEqual(initial_molecules - remaining, uptake, delta=1e-6)
        validate_frame_sequence(frames, run)

    def test_turn_command_changes_the_following_interval_and_wall_reflects(self):
        simulation, _ = make_simulation(
            GridDomain.thin_layer(3, 3, 5, 5, 1), (12.5, 7.5, 0.5)
        )
        positions = []
        for _ in range(5):
            frame = simulation.step(0.25).cell_frame
            positions.append(frame["cells"][0]["position_um"])
        self.assertAlmostEqual(positions[0][0], 14)
        self.assertAlmostEqual(positions[1][0], 14.5)
        self.assertAlmostEqual(positions[3][0], 11.5)
        self.assertAlmostEqual(positions[4][0], 11.5)
        self.assertAlmostEqual(positions[4][1], 6)
        np.testing.assert_allclose(simulation.outputs["motion"]["heading"][0], [0, -1, 0], atol=1e-12)

    def test_volume_reflects_in_z_but_thin_layer_keeps_z_fixed(self):
        angle = 2**-0.5
        orientation = (0, -angle, 0, angle)
        volume, _ = make_simulation(
            GridDomain.volume(3, 3, 3, 5, 5, 5), (7.5, 7.5, 14), orientation
        )
        after = volume.step(0.5)
        self.assertAlmostEqual(after.cell_frame["cells"][0]["position_um"][2], 13)
        self.assertAlmostEqual(volume.outputs["motion"]["heading"][0, 2], -1)

        with self.assertRaises(SimulationError) as caught:
            make_simulation(GridDomain.thin_layer(3, 3, 5, 5, 1), (7.5, 7.5, 0.5), orientation)
        self.assertEqual(caught.exception.code, "motion.plane")

    def test_cells_in_one_group_keep_independent_headings_and_ids(self):
        graph = json.loads((EXAMPLES / "moving_uptake.graph.json").read_text(encoding="utf-8"))
        run = json.loads((EXAMPLES / "uptake.run.json").read_text(encoding="utf-8"))
        run["graph_id"] = graph["id"]
        group = CellGroup(
            "group_1", ("left", "right"),
            np.array([[2.5, 7.5, 0.5], [12.5, 7.5, 0.5]]),
            np.array([[0, 0, 0, 1], [0, 0, 1, 0]]),
        )
        simulation = Simulation(
            World(GridDomain.thin_layer(3, 3, 5, 5, 1), {"group_1": group}),
            graph, run, default_registry(),
        )
        cells = simulation.step(0.25).cell_frame["cells"]
        self.assertEqual([cell["id"] for cell in cells], ["left", "right"])
        self.assertAlmostEqual(cells[0]["position_um"][0], 4)
        self.assertAlmostEqual(cells[1]["position_um"][0], 11)
        self.assertEqual(len({cell["id"] for cell in cells}), 2)

    def test_straight_motion_preserves_body_roll(self):
        half_angle = math.pi / 6
        orientation = (math.sin(half_angle), 0, 0, math.cos(half_angle))
        simulation, _ = make_simulation(
            GridDomain.thin_layer(3, 3, 5, 5, 1), (2.5, 7.5, 0.5), orientation
        )
        frame = simulation.step(0.25).cell_frame
        np.testing.assert_allclose(
            frame["cells"][0]["orientation_xyzw"], orientation, atol=1e-12
        )

    def test_failed_step_does_not_commit_a_moved_pose(self):
        simulation, _ = make_simulation(
            GridDomain.thin_layer(3, 3, 5, 5, 1), (2.5, 2.5, 0.5),
            rate_constant=1e9,
        )
        with self.assertRaises(SimulationError) as caught:
            simulation.step(0.25)
        self.assertEqual(caught.exception.code, "field.depleted")
        self.assertEqual(simulation.frame_index, 0)
        self.assertEqual(simulation.time_s, 0)
        np.testing.assert_allclose(simulation.world.groups["group_1"].positions_um, [[2.5, 2.5, 0.5]])

    def test_one_group_cannot_have_two_pose_providers(self):
        graph = json.loads((EXAMPLES / "moving_uptake.graph.json").read_text(encoding="utf-8"))
        second = deepcopy(next(node for node in graph["nodes"] if node["id"] == "motion"))
        second["id"] = "other_motion"
        graph["nodes"].append(second)
        graph["edges"].append({
            "id": "turn_to_other_motion",
            "from": {"node": "turn", "port": "turn_angle"},
            "to": {"node": "other_motion", "port": "turn_angle"},
            "timing": "previous_step",
        })
        run = json.loads((EXAMPLES / "uptake.run.json").read_text(encoding="utf-8"))
        run["graph_id"] = graph["id"]
        grid = GridDomain.thin_layer(3, 3, 5, 5, 1)
        group = CellGroup(
            "group_1", ("cell_0",), np.array([[2.5, 2.5, 0.5]]), np.array([[0, 0, 0, 1]])
        )
        with self.assertRaises(SimulationError) as caught:
            Simulation(World(grid, {"group_1": group}), graph, run, default_registry())
        self.assertEqual(caught.exception.code, "motion.pose")


if __name__ == "__main__":
    unittest.main()
