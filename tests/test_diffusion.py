from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, SimulationError, World, default_registry
from friskoli_cad.engine.diffusion import explicit_no_flux_limit, no_flux_diffusion_rate


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def make_simulation(geometry: str, diffusivity: float, *, old_inventory: bool = False) -> Simulation:
    if geometry == "thin_layer":
        grid = GridDomain.thin_layer(3, 3, 5, 5, 1)
        z_um = 0.5
    else:
        grid = GridDomain.volume(3, 3, 3, 5, 5, 5)
        z_um = 7.5
    group = CellGroup(
        "group_1", ("cell_0",), np.array([[7.5, 7.5, z_um]]),
        np.array([[0, 0, 0, 1]]),
    )
    graph_name = "box_uptake.graph.json" if old_inventory else "diffusion_uptake.graph.json"
    graph = json.loads((EXAMPLES / graph_name).read_text(encoding="utf-8"))
    if not old_inventory:
        diffusion = next(node for node in graph["nodes"] if node["id"] == "diffusion")
        diffusion["parameters"]["diffusivity"]["value"] = diffusivity
    run = json.loads((EXAMPLES / "uptake.run.json").read_text(encoding="utf-8"))
    run["graph_id"] = graph["id"]
    return Simulation(World(grid, {"group_1": group}), graph, run, default_registry())


class DiffusionTests(unittest.TestCase):
    def test_face_transfers_spread_a_pulse_without_changing_total(self):
        thin = GridDomain.thin_layer(3, 1, 1, 1, 1)
        pulse = np.array([[[0.0, 10.0, 0.0]]])
        rate = no_flux_diffusion_rate(pulse, thin, 1)
        np.testing.assert_allclose(rate, [[[10.0, -20.0, 10.0]]])
        np.testing.assert_allclose(pulse + 0.25 * rate, [[[2.5, 5.0, 2.5]]])
        self.assertAlmostEqual(rate.sum(), 0)

        volume = GridDomain.volume(2, 2, 2, 1, 1, 1)
        pulse_3d = np.zeros(volume.shape)
        pulse_3d[0, 0, 0] = 10
        rate_3d = no_flux_diffusion_rate(pulse_3d, volume, 1)
        self.assertAlmostEqual(rate_3d[0, 0, 0], -30)
        for neighbor in ((0, 0, 1), (0, 1, 0), (1, 0, 0)):
            self.assertAlmostEqual(rate_3d[neighbor], 10)
        self.assertAlmostEqual(rate_3d.sum(), 0)
        self.assertAlmostEqual(explicit_no_flux_limit(volume, 1), 1 / 6)

    def test_uniform_field_and_zero_diffusivity_have_zero_rate(self):
        for grid in (GridDomain.thin_layer(3, 2, 2, 3, 1), GridDomain.volume(3, 2, 2, 2, 3, 4)):
            np.testing.assert_array_equal(no_flux_diffusion_rate(np.full(grid.shape, 10), grid, 5), 0)
            np.testing.assert_array_equal(no_flux_diffusion_rate(np.ones(grid.shape), grid, 0), 0)
        with self.assertRaises(SimulationError) as caught:
            explicit_no_flux_limit(GridDomain.thin_layer(2, 2, 1, 1, 1), -1)
        self.assertEqual(caught.exception.code, "diffusion.coefficient")

    def test_anisotropic_3d_faces_use_their_own_spacing(self):
        grid = GridDomain.volume(2, 2, 2, 1, 2, 4)
        pulse = np.zeros(grid.shape)
        pulse[0, 0, 0] = 10
        rate = no_flux_diffusion_rate(pulse, grid, 2)
        self.assertAlmostEqual(rate[0, 0, 0], -26.25)
        self.assertAlmostEqual(rate[0, 0, 1], 20)
        self.assertAlmostEqual(rate[0, 1, 0], 5)
        self.assertAlmostEqual(rate[1, 0, 0], 1.25)
        self.assertAlmostEqual(rate.sum(), 0)
        self.assertAlmostEqual(explicit_no_flux_limit(grid, 2), 1 / 5.25)

    def test_diffusion_replenishes_local_depletion_and_preserves_uptake_balance(self):
        for geometry in ("thin_layer", "volume"):
            with self.subTest(geometry=geometry):
                simulation = make_simulation(geometry, 5)
                old = make_simulation(geometry, 0, old_inventory=True)
                initial_molecules = (
                    simulation.current.concentration_fields["substrate"].sum()
                    * simulation.world.grid.molecules_per_uM_voxel
                )
                for _ in range(6):
                    after = simulation.step(0.25)
                    old_after = old.step(0.25)
                field = after.concentration_fields["substrate"]
                old_field = old_after.concentration_fields["substrate"]
                center = (0, 1, 1) if geometry == "thin_layer" else (1, 1, 1)
                neighbor = (center[0], 1, 2)
                self.assertGreater(field[center], old_field[center])
                self.assertLess(field[neighbor], 10)
                uptake = after.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
                remaining = field.sum() * simulation.world.grid.molecules_per_uM_voxel
                self.assertAlmostEqual(initial_molecules - remaining, uptake, delta=1e-6)

    def test_zero_diffusion_uses_new_inventory_version_without_changing_old_results(self):
        for geometry in ("thin_layer", "volume"):
            with self.subTest(geometry=geometry):
                new = make_simulation(geometry, 0)
                old = make_simulation(geometry, 0, old_inventory=True)
                for _ in range(4):
                    new_after = new.step(0.25)
                    old_after = old.step(0.25)
                np.testing.assert_allclose(
                    new_after.concentration_fields["substrate"],
                    old_after.concentration_fields["substrate"],
                    rtol=0, atol=1e-14,
                )
                self.assertEqual(new_after.cell_frame["cells"], old_after.cell_frame["cells"])

    def test_unstable_step_is_rejected_before_state_changes(self):
        simulation = make_simulation("volume", 5)
        limit = explicit_no_flux_limit(simulation.world.grid, 5)
        with self.assertRaises(SimulationError) as caught:
            simulation.step(limit * 1.01)
        self.assertEqual(caught.exception.code, "diffusion.stability")
        self.assertEqual(simulation.time_s, 0)
        self.assertEqual(simulation.frame_index, 0)
        self.assertTrue(np.all(simulation.current.concentration_fields["substrate"] == 10))
        accepted = simulation.step(limit)
        self.assertAlmostEqual(accepted.cell_frame["time_s"], limit)


if __name__ == "__main__":
    unittest.main()
