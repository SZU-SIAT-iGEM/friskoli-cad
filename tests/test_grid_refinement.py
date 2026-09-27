from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def make_simulation(geometry: str, spacing_um: float) -> Simulation:
    nx, ny = round(20 / spacing_um), round(10 / spacing_um)
    if geometry == "thin_layer":
        grid = GridDomain.thin_layer(nx, ny, spacing_um, spacing_um, 1)
        z_um = 0.5
    else:
        grid = GridDomain.volume(
            nx, ny, round(10 / spacing_um), spacing_um, spacing_um, spacing_um
        )
        z_um = 3.7
    group = CellGroup(
        "group_1", ("cell_0",), np.array([[6.2, 3.7, z_um]]),
        np.array([[0, 0, 0, 1]]),
    )
    graph = json.loads((EXAMPLES / "uptake.graph.json").read_text(encoding="utf-8"))
    run = json.loads((EXAMPLES / "uptake.run.json").read_text(encoding="utf-8"))
    return Simulation(World(grid, {"group_1": group}), graph, run, default_registry())


class GridRefinementTests(unittest.TestCase):
    def test_fixed_extent_preserves_initial_inventory_and_uptake_balance(self):
        for geometry in ("thin_layer", "volume"):
            with self.subTest(geometry=geometry):
                initial_amounts = []
                for spacing_um in (5.0, 2.5, 1.25):
                    simulation = make_simulation(geometry, spacing_um)
                    grid = simulation.world.grid
                    initial = simulation.current.concentration_fields["substrate"]
                    initial_amount = initial.sum() * grid.molecules_per_uM_voxel
                    initial_amounts.append(initial_amount)

                    snapshot = simulation.step(0.25)
                    field = snapshot.concentration_fields["substrate"]
                    cell_position = simulation.world.groups["group_1"].positions_um
                    cell_index = grid.flat_indices(cell_position)[0]
                    uptake = snapshot.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
                    self.assertAlmostEqual(uptake, 250)
                    self.assertAlmostEqual(
                        10 - field.ravel()[cell_index],
                        uptake / grid.molecules_per_uM_voxel,
                        places=12,
                    )
                    self.assertAlmostEqual(
                        initial_amount - field.sum() * grid.molecules_per_uM_voxel,
                        uptake,
                        delta=1e-6,
                    )
                for amount in initial_amounts[1:]:
                    self.assertAlmostEqual(amount, initial_amounts[0], delta=1e-6)

    def test_time_refinement_approaches_point_sink_solution(self):
        results = []
        for dt_s in (1.0, 0.5, 0.25):
            simulation = make_simulation("thin_layer", 1.25)
            grid = simulation.world.grid
            for _ in range(round(4 / dt_s)):
                snapshot = simulation.step(dt_s)
            cell_position = simulation.world.groups["group_1"].positions_um
            cell_index = grid.flat_indices(cell_position)[0]
            results.append(snapshot.concentration_fields["substrate"].ravel()[cell_index])
        exact = 10 * math.exp(-100 * 4 / grid.molecules_per_uM_voxel)
        errors = [abs(value - exact) for value in results]
        self.assertGreater(errors[0], errors[1])
        self.assertGreater(errors[1], errors[2])


if __name__ == "__main__":
    unittest.main()
