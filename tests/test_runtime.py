from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from friskoli_cad.engine import (
    CellGroup, GridDomain, Simulation, SimulationError, World, default_registry,
)
from friskoli_cad.protocol import ProtocolError, validate_frame_sequence


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def load(name: str):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def make_simulation(positions=((2.5, 2.5, 0.5), (12.5, 2.5, 0.5)), rate=100) -> Simulation:
    ids = tuple(f"cell_{index}" for index in range(len(positions)))
    group = CellGroup(
        "group_1", ids, np.array(positions, dtype=float),
        np.tile([0.0, 0.0, 0.0, 1.0], (len(ids), 1)),
    )
    world = World(GridDomain(nx=4, ny=2, dx_um=5, depth_um=1), {"group_1": group})
    graph = load("uptake.graph.json")
    graph["nodes"][2]["parameters"]["rate_constant"]["value"] = rate
    return Simulation(world, graph, load("uptake.run.json"), default_registry())


class RuntimeTests(unittest.TestCase):
    def test_local_field_change_and_single_cell_history(self):
        sim = make_simulation()
        initial = sim.current
        before = initial.concentration_fields["substrate"]
        self.assertTrue(np.all(before == 10))
        self.assertEqual(initial.concentration_units["substrate"], "uM")
        self.assertEqual([cell["id"] for cell in initial.cell_frame["cells"]], ["cell_0", "cell_1"])
        self.assertEqual(initial.cell_frame["cells"][0]["channels"]["uptake.flux"], 1000)

        after = sim.step(1)
        field = after.concentration_fields["substrate"]
        change = 1000 / sim.world.grid.molecules_per_uM_voxel
        self.assertAlmostEqual(field[0, 0], 10 - change)
        self.assertAlmostEqual(field[0, 2], 10 - change)
        self.assertEqual(field[0, 1], 10)
        self.assertEqual(after.cell_frame["frame_index"], 1)
        self.assertEqual([cell["id"] for cell in after.cell_frame["cells"]], ["cell_0", "cell_1"])
        self.assertEqual(
            [cell["channels"]["uptake.cumulative"] for cell in after.cell_frame["cells"]],
            [1000, 1000],
        )
        validate_frame_sequence([initial.cell_frame, after.cell_frame], load("uptake.run.json"))

    def test_molecule_balance_across_steps(self):
        sim = make_simulation()
        initial_molecules = sim.current.concentration_fields["substrate"].sum() * sim.world.grid.molecules_per_uM_voxel
        frames = [sim.current.cell_frame]
        for _ in range(4):
            snapshot = sim.step(1)
            frames.append(snapshot.cell_frame)
            field_molecules = snapshot.concentration_fields["substrate"].sum() * sim.world.grid.molecules_per_uM_voxel
            uptake = sum(cell["channels"]["uptake.cumulative"] for cell in snapshot.cell_frame["cells"])
            self.assertAlmostEqual(initial_molecules - field_molecules, uptake, places=8)
        self.assertLess(snapshot.concentration_fields["substrate"][0, 0], 10)
        validate_frame_sequence(frames, load("uptake.run.json"))

    def test_multiple_cells_in_one_voxel_are_aggregated(self):
        sim = make_simulation(positions=((2.5, 2.5, 0.5), (2.6, 2.6, 0.5)))
        after = sim.step(1)
        change = 2000 / sim.world.grid.molecules_per_uM_voxel
        self.assertAlmostEqual(after.concentration_fields["substrate"][0, 0], 10 - change)
        self.assertEqual(after.concentration_fields["substrate"][0, 1], 10)

    def test_depletion_rejects_step_without_advancing_state(self):
        sim = make_simulation(rate=1e7)
        with self.assertRaises(SimulationError) as caught:
            sim.step(1)
        self.assertEqual(caught.exception.code, "field.depleted")
        self.assertEqual(sim.time_s, 0)
        self.assertEqual(sim.frame_index, 0)
        self.assertTrue(np.all(sim.current.concentration_fields["substrate"] == 10))

    def test_invalid_geometry_and_time_step_are_rejected(self):
        with self.assertRaises(SimulationError) as caught:
            CellGroup(
                "group_1", (1,), np.array([[2.5, 2.5, 0.5]]),
                np.array([[0, 0, 0, 1]]),
            )
        self.assertEqual(caught.exception.code, "cell.identity")
        with self.assertRaises(SimulationError) as caught:
            make_simulation(positions=((20, 2.5, 0.5),))
        self.assertEqual(caught.exception.code, "cell.position")
        sim = make_simulation()
        with self.assertRaises(SimulationError) as caught:
            sim.step(0)
        self.assertEqual(caught.exception.code, "time.step")

    def test_malformed_run_is_reported_by_protocol_validation(self):
        group = CellGroup(
            "group_1", ("cell_0",), np.array([[2.5, 2.5, 0.5]]),
            np.array([[0, 0, 0, 1]]),
        )
        world = World(GridDomain(4, 2, 5, 1), {"group_1": group})
        with self.assertRaises(ProtocolError) as caught:
            Simulation(world, load("uptake.graph.json"), {}, default_registry())
        self.assertEqual(caught.exception.code, "schema.invalid")


if __name__ == "__main__":
    unittest.main()
