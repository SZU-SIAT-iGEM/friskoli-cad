from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, SimulationError, World, default_registry
from friskoli_cad.protocol import validate_frame_sequence


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def make_simulation(graph_name: str, geometry: str = "thin_layer", rate: float = 100) -> Simulation:
    if geometry == "thin_layer":
        grid = GridDomain.thin_layer(4, 2, 5, 5, 1)
        positions = np.array([[2.5, 2.5, 0.5], [12.5, 2.5, 0.5]])
    else:
        grid = GridDomain.volume(4, 2, 2, 5, 5, 5)
        positions = np.array([[2.5, 2.5, 2.5], [2.5, 2.5, 7.5]])
    group = CellGroup(
        "group_1", ("cell_0", "cell_1"), positions,
        np.array([[0, 0, 0, 1], [0, 0, 0, 1]]),
    )
    graph = load(graph_name)
    graph["nodes"][2]["parameters"]["rate_constant"]["value"] = rate
    run = load("uptake.run.json")
    run["graph_id"] = graph["id"]
    run["run_id"] = f"{graph['id']}-run"
    return Simulation(World(grid, {"group_1": group}), graph, run, default_registry())


class EnvironmentSwapTests(unittest.TestCase):
    def test_only_environment_node_changes_and_downstream_runs(self):
        inventory_graph = load("uptake.graph.json")
        reservoir_graph = load("reservoir.graph.json")
        self.assertEqual(inventory_graph["nodes"][1:], reservoir_graph["nodes"][1:])
        self.assertEqual(inventory_graph["edges"], reservoir_graph["edges"])
        self.assertEqual(
            inventory_graph["nodes"][0]["parameters"],
            reservoir_graph["nodes"][0]["parameters"],
        )

        inventory = make_simulation("uptake.graph.json")
        reservoir = make_simulation("reservoir.graph.json")
        self.assertEqual(
            inventory.current.cell_frame["cells"][0]["channels"]["uptake.flux"],
            reservoir.current.cell_frame["cells"][0]["channels"]["uptake.flux"],
        )
        inventory_step = inventory.step(1)
        reservoir_step = reservoir.step(1)
        self.assertLess(inventory_step.concentration_fields["substrate"][0, 0, 0], 10)
        self.assertTrue(np.all(reservoir_step.concentration_fields["substrate"] == 10))
        self.assertLess(
            inventory_step.cell_frame["cells"][0]["channels"]["uptake.flux"],
            reservoir_step.cell_frame["cells"][0]["channels"]["uptake.flux"],
        )
        self.assertEqual(
            [cell["id"] for cell in inventory_step.cell_frame["cells"]],
            [cell["id"] for cell in reservoir_step.cell_frame["cells"]],
        )

    def test_reservoir_supply_accounts_for_uptake_in_both_geometries(self):
        for geometry in ("thin_layer", "volume"):
            with self.subTest(geometry=geometry):
                simulation = make_simulation("reservoir.graph.json", geometry)
                initial = simulation.current
                initial_molecules = (
                    initial.concentration_fields["substrate"].sum()
                    * simulation.world.grid.molecules_per_uM_voxel
                )
                frames = [initial.cell_frame]
                for step in range(1, 5):
                    snapshot = simulation.step(1)
                    frames.append(snapshot.cell_frame)
                    field = snapshot.concentration_fields["substrate"]
                    supply = snapshot.environment_fields["field"]["cumulative_supply"]
                    supply_flux = snapshot.environment_fields["field"]["supply_flux"]
                    uptake = sum(
                        cell["channels"]["uptake.cumulative"]
                        for cell in snapshot.cell_frame["cells"]
                    )
                    self.assertEqual(supply.unit, "molecule")
                    self.assertEqual(supply.species, "substrate")
                    self.assertEqual(supply_flux.unit, "molecule/s")
                    self.assertTrue(np.all(field == 10))
                    self.assertAlmostEqual(supply.values.sum(), uptake, places=8)
                    self.assertAlmostEqual(supply_flux.values.sum(), 2000, places=8)
                    self.assertAlmostEqual(
                        field.sum() * simulation.world.grid.molecules_per_uM_voxel
                        + uptake - supply.values.sum(),
                        initial_molecules,
                        places=7,
                    )
                    self.assertEqual(snapshot.cell_frame["frame_index"], step)
                validate_frame_sequence(frames, simulation.run)

    def test_reservoir_is_explicitly_unlimited(self):
        inventory = make_simulation("uptake.graph.json", rate=1e7)
        reservoir = make_simulation("reservoir.graph.json", rate=1e7)
        with self.assertRaises(SimulationError) as caught:
            inventory.step(1)
        self.assertEqual(caught.exception.code, "field.depleted")
        snapshot = reservoir.step(1)
        self.assertTrue(np.all(snapshot.concentration_fields["substrate"] == 10))
        self.assertAlmostEqual(
            snapshot.environment_fields["field"]["cumulative_supply"].values.sum(),
            2e8,
        )


if __name__ == "__main__":
    unittest.main()
