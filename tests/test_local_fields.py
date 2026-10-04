"""Reference local-field conservation, isolation and numerical boundaries."""
import copy
import json
import unittest

import numpy as np

from friskoli_cad.engine.local_fields import (
    FieldSpecies, LocalSource, SolidAABB, copy_concentrations,
    local_field_state_from_dict, local_field_state_to_dict,
    make_local_field_state, propose_local_field_step, sample_local_fields,
)
from friskoli_cad.engine.core import GridDomain, SimulationError


class LocalFieldsTests(unittest.TestCase):
    def setUp(self):
        self.grid = GridDomain.thin_layer(5, 3, 1., 1., 1.)

    def state(self, initial=0., d=1., **kwargs):
        return make_local_field_state(self.grid, [FieldSpecies("S", initial, d)], **kwargs)

    def assert_ledger(self, proposal):
        for ledger in proposal.ledgers.values():
            self.assertLessEqual(abs(ledger.conservation_residual_molecules), ledger.conservation_bound_molecules)
            self.assertAlmostEqual(ledger.field_before_molecules + ledger.released_molecules,
                                   ledger.field_after_molecules + ledger.accepted_uptake_molecules, places=8)

    def test_diffusion_positive_conservative_automatic_substeps(self):
        initial = np.zeros(self.grid.shape)
        initial[0, 1, 2] = 1.
        state = self.state(initial)
        p = propose_local_field_step(state, 2.)
        self.assertGreater(p.substeps, 1)
        field = copy_concentrations(p.after)["S"]
        self.assertTrue(np.all(field >= 0))
        self.assertAlmostEqual(field.sum(), 1.)
        self.assertGreater(field[0, 0, 0], 0)
        self.assert_ledger(p)

    def test_anisotropic_3d_and_degenerate_no_diffusion(self):
        grid = GridDomain.volume(3, 4, 2, .5, 2., 3.)
        initial = np.zeros(grid.shape)
        initial[0, 0, 0] = 5.
        state = make_local_field_state(grid, [FieldSpecies("S", initial, 3.)])
        p = propose_local_field_step(state, .5)
        self.assertAlmostEqual(sum(p.after.concentrations_uM["S"]), 5.)
        self.assert_ledger(p)
        single = make_local_field_state(GridDomain.thin_layer(1, 1, 1, 1, 1), [FieldSpecies("S", 1., 10.)])
        self.assertEqual(propose_local_field_step(single, 100).after.concentrations_uM["S"], (1.,))

    def test_finite_source_exhausts_and_empty_cells_are_valid(self):
        source = LocalSource("p", "S", (.5, .5, .5), 0., 12., 10.)
        state = self.state(d=0., sources=[source])
        first = propose_local_field_step(state, 1.)
        second = propose_local_field_step(first.after, 1.)
        third = propose_local_field_step(second.after, 1.)
        self.assertEqual([first.source_released_molecules["p"], second.source_released_molecules["p"], third.source_released_molecules["p"]], [10., 2., 0.])
        self.assertEqual(second.after.sources[0].remaining_molecules, 0.)
        self.assertEqual(first.samples_before_uptake.concentration_uM["S"], ())
        self.assertAlmostEqual(third.ledgers["S"].field_after_molecules, 12.)
        for p in (first, second, third):
            self.assert_ledger(p)

    def test_large_source_cannot_hide_partial_transfer_rounding(self):
        state = self.state(d=0., sources=[LocalSource('p', 'S', (.5,.5,.5), 0., 1e16, 1.5)])
        with self.assertRaisesRegex(SimulationError, 'transfer-relative'):
            propose_local_field_step(state, 1.)
        self.assertEqual(state.sources[0].remaining_molecules, 1e16)

    def test_zero_uptake_does_not_rewrite_concentration_through_units(self):
        for initial in (.1, .123456789, 1e-11, 1e9):
            state = self.state(initial, d=0.)
            proposal = propose_local_field_step(state, .3, cell_ids=['a'],
                positions_um=[(.5,.5,.5)], requested_uptake_molecules_s={'S':[0.]})
            self.assertEqual(proposal.after.concentrations_uM, state.concentrations_uM)

    def test_shared_uptake_exhausts_one_voxel_proportionally(self):
        initial = np.zeros(self.grid.shape)
        initial[0, 0, 0] = 9 / self.grid.molecules_per_uM_voxel
        state = self.state(initial, d=0.)
        p = propose_local_field_step(state, 1., cell_ids=("a", "b"),
            positions_um=((.2, .2, .5), (.8, .8, .5)), requested_uptake_molecules_s={"S": [12., 6.]})
        np.testing.assert_allclose(p.accepted_uptake_molecules["S"], (6., 3.))
        self.assertLess(p.after.concentrations_uM["S"][0], 1e-16)
        self.assert_ledger(p)

    def test_sampling_and_uptake_share_voxel_support_across_voxel_boundary(self):
        initial = np.arange(15., dtype=float).reshape(self.grid.shape)
        state = self.state(initial, d=0.)
        positions = ((.99, .5, .5), (1.01, .5, .5))
        sample = sample_local_fields(state, positions)
        self.assertEqual(sample.support_indices, (0, 1))
        self.assertEqual(sample.concentration_uM["S"], (0., 1.))
        p = propose_local_field_step(state, 1, cell_ids=("a", "b"), positions_um=positions,
                                     requested_uptake_molecules_s={"S": [1000., 1000.]})
        self.assertEqual(p.accepted_uptake_molecules["S"][0], 0.)
        self.assertAlmostEqual(p.accepted_uptake_molecules["S"][1], self.grid.molecules_per_uM_voxel)
        self.assertEqual(p.after.concentrations_uM["S"][2], 2.)

    def test_attractant_without_explicit_request_is_never_consumed(self):
        state = make_local_field_state(self.grid, [FieldSpecies("A", 1., 0.), FieldSpecies("S", 1., 0.)])
        p = propose_local_field_step(state, 1., cell_ids=("a",), positions_um=((.5, .5, .5),),
                                     requested_uptake_molecules_s={"S": [5.]})
        self.assertEqual(p.after.concentrations_uM["A"], state.concentrations_uM["A"])
        self.assertEqual(p.accepted_uptake_molecules["A"], (0.,))
        self.assertEqual(p.accepted_uptake_molecules["S"], (5.,))

    def test_wall_blocks_diffusion_and_gradient_does_not_cross_wall(self):
        initial = np.zeros(self.grid.shape)
        initial[:, :, :2] = 1
        initial[:, :, 3:] = 100
        wall = SolidAABB((2., 0., 0.), (3., 3., 1.))
        state = self.state(initial, obstacles=(wall,))
        p = propose_local_field_step(state, 3.)
        np.testing.assert_allclose(copy_concentrations(p.after)["S"], initial)
        gradient = sample_local_fields(state, ((1.5, 1.5, .5),)).gradient_uM_um["S"][0]
        self.assertEqual(gradient, (0., 0., 0.))
        with self.assertRaisesRegex(SimulationError, "solid obstacle"):
            sample_local_fields(state, ((2.5, 1.5, .5),))

    def test_obstacles_reject_partial_cells_nonzero_stock_and_sources(self):
        with self.assertRaisesRegex(SimulationError, "aligned"):
            self.state(obstacles=(SolidAABB((1.1, 0., 0.), (2., 3., 1.)),))
        wall = SolidAABB((2., 0., 0.), (3., 3., 1.))
        with self.assertRaisesRegex(SimulationError, "zero in obstacles"):
            self.state(np.ones(self.grid.shape), obstacles=(wall,))
        for center, radius in (((2.5, .5, .5), 0.), ((1.5, .5, .5), 1.)):
            with self.assertRaisesRegex(SimulationError, "obstacle"):
                self.state(obstacles=(wall,), sources=(LocalSource("p", "S", center, radius, 1., 1.),))

    def test_source_release_then_diffusion_then_uptake(self):
        state = self.state(d=1., sources=(LocalSource("p", "S", (.5, .5, .5), 0., 100., 100.),))
        p = propose_local_field_step(state, .1, cell_ids=("a",), positions_um=((1.5, .5, .5),),
                                     requested_uptake_molecules_s={"S": [100.]})
        self.assertGreater(p.samples_before_uptake.concentration_uM["S"][0], 0.)
        self.assertGreater(p.accepted_uptake_molecules["S"][0], 0.)
        self.assertEqual(p.after.concentrations_uM["S"][1], 0.)
        self.assert_ledger(p)

    def test_spherical_source_distributes_uniformly_and_grid_resolution_changes_support(self):
        state = self.state(d=0., sources=(LocalSource("p", "S", (2.5, 1.5, .5), 1., 100., 100.),))
        p = propose_local_field_step(state, 1.)
        values = np.asarray(p.after.concentrations_uM["S"])
        self.assertEqual(np.count_nonzero(values), 5)
        self.assertAlmostEqual(p.ledgers["S"].field_after_molecules, 100.)

    def test_release_inventory_is_independent_of_voxel_volume(self):
        masses = []
        for spacing, count in ((1., 4), (.5, 8)):
            grid = GridDomain.thin_layer(count, count, spacing, spacing, 1.)
            state = make_local_field_state(grid, [FieldSpecies("S", 0., 1.)],
                sources=(LocalSource("p", "S", (2., 2., .5), .7, 300., 100.),))
            proposal = propose_local_field_step(state, 1.)
            masses.append(proposal.ledgers["S"].field_after_molecules)
            self.assert_ledger(proposal)
        np.testing.assert_allclose(masses, (100., 100.), rtol=1e-14)

    def test_shared_shortage_is_independent_of_cell_order(self):
        state = self.state(1., d=0.)
        results = []
        for ids, flux in ((("a", "b"), (1000., 2000.)), (("b", "a"), (2000., 1000.))):
            p = propose_local_field_step(state, 1., cell_ids=ids, positions_um=((.5, .5, .5),) * 2,
                                         requested_uptake_molecules_s={"S": flux})
            results.append(dict(zip(ids, p.accepted_uptake_molecules["S"])))
        self.assertEqual(results[0], results[1])

    def test_source_release_saturates_before_multiplication_overflow(self):
        state = self.state(d=0., sources=(LocalSource("p", "S", (.5, .5, .5), 0., 1., 1e308),))
        p = propose_local_field_step(state, 2.)
        self.assertEqual(p.source_released_molecules["p"], 1.)
        self.assertEqual(propose_local_field_step(p.after, 2.).source_released_molecules["p"], 0.)

    def test_unrepresentable_source_transfer_rejects_without_mutating_stock(self):
        state = self.state(1e20, d=0., sources=(LocalSource("p", "S", (.5, .5, .5), 0., 1., 1.),))
        with self.assertRaisesRegex(SimulationError, "cannot represent source release"):
            propose_local_field_step(state, 1.)
        self.assertEqual(state.sources[0].remaining_molecules, 1.)

    def test_point_source_outside_domain_is_rejected_even_when_exhausted(self):
        with self.assertRaises(SimulationError):
            self.state(sources=(LocalSource("p", "S", (5., .5, .5), 0., 0., 0.),))

    def test_budget_errors_and_invalid_inputs(self):
        with self.assertRaisesRegex(SimulationError, "max_voxels"):
            make_local_field_state(self.grid, [], max_voxels=1)
        with self.assertRaisesRegex(SimulationError, "max_values"):
            make_local_field_state(self.grid, [FieldSpecies("S", 0., 1.)], max_values=1)
        state = self.state()
        with self.assertRaisesRegex(SimulationError, "max_substeps"):
            propose_local_field_step(state, 100., max_substeps=1)
        with self.assertRaisesRegex(SimulationError, "max_work_items"):
            propose_local_field_step(state, .1, max_work_items=1)
        for initial in (-1., float("nan"), np.zeros((1, 2, 3))):
            with self.assertRaises(SimulationError):
                self.state(initial)
        with self.assertRaises(SimulationError):
            self.state(d=-1.)
        with self.assertRaises(SimulationError):
            propose_local_field_step(state, 0.)
        with self.assertRaises(SimulationError):
            propose_local_field_step(state, 1., requested_uptake_molecules_s={"unknown": []})
        with self.assertRaises(SimulationError):
            propose_local_field_step(state, 1., cell_ids=("a", "a"), positions_um=((.5, .5, .5),) * 2)

    def test_proposals_and_copy_helper_do_not_modify_inputs(self):
        initial = np.ones(self.grid.shape)
        state = self.state(initial)
        original = local_field_state_to_dict(state)
        initial[:] = 500
        writable = copy_concentrations(state)
        writable["S"][:] = 0
        first = propose_local_field_step(state, .1)
        second = propose_local_field_step(state, .1)
        self.assertEqual(local_field_state_to_dict(first.after), local_field_state_to_dict(second.after))
        self.assertEqual(local_field_state_to_dict(state), original)
        with self.assertRaises(TypeError):
            state.concentrations_uM["S"] = (1.,)
        with self.assertRaises(TypeError):
            state.concentrations_uM["S"][0] = 1.
        with self.assertRaises(SimulationError):
            propose_local_field_step(state, 1., cell_ids=("a",), positions_um=((999, 0, 0),))
        self.assertEqual(local_field_state_to_dict(state), original)

    def test_checkpoint_roundtrip_and_validation(self):
        state = self.state(2., sources=(LocalSource("p", "S", (.5, .5, .5), 0., 10., 2.),),
                           obstacles=(SolidAABB((2., 0., 0.), (3., 3., 1.)),))
        payload = json.loads(json.dumps(local_field_state_to_dict(state)))
        restored = local_field_state_from_dict(payload)
        self.assertEqual(local_field_state_to_dict(restored), payload)
        for mutate in (
            lambda x: x["concentrations_uM"]["S"].pop(),
            lambda x: x["concentrations_uM"]["S"].__setitem__(0, float("nan")),
            lambda x: x["blocked"].__setitem__(0, 1),
            lambda x: x["sources"][0].__setitem__("center_um", [2.5, .5, .5]),
        ):
            invalid = copy.deepcopy(payload)
            mutate(invalid)
            with self.assertRaises(SimulationError):
                local_field_state_from_dict(invalid)


if __name__ == "__main__":
    unittest.main()
