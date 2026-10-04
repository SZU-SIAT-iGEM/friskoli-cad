"""Dense field storage and execution preserve the physical transaction contract."""
from dataclasses import replace
from fractions import Fraction
import math

import numpy as np
import pytest

from friskoli_cad.engine.field_backend import CPU, CUDA, available_backends
from friskoli_cad.engine.local_fields import (
    FieldSpecies, LocalSource, SolidAABB, FrozenGridArray, _mass, _voxel_uptake,
    local_field_state_from_dict, local_field_state_to_dict,
    make_local_field_state, propose_local_field_step,
)
from friskoli_cad.engine.module_api import freeze as _freeze
from friskoli_cad.engine.core import GridDomain


def test_dense_fields_own_immutable_storage_and_survive_checkpoint():
    grid = GridDomain.volume(24, 24, 24, .5, .5, .5)
    initial = np.ones(grid.shape)
    state = make_local_field_state(grid, [FieldSpecies('S', initial, 0.)])
    initial[:] = 9
    values = state.concentrations_uM['S']
    assert isinstance(values, FrozenGridArray)
    assert values.nbytes == grid.voxel_count * 8
    with pytest.raises(ValueError):
        values.setflags(write=True)
    view = np.asarray(values).reshape(grid.shape)
    frozen = _freeze({'field': {'concentration': view}})['field']['concentration']
    assert np.shares_memory(values, frozen)
    with pytest.raises(ValueError):
        frozen.setflags(write=True)
    proposal = propose_local_field_step(state, .1, cell_ids=['cell'],
        positions_um=[(.75, .75, .75)], requested_uptake_molecules_s={'S': [10.]})
    assert np.all(values == 1.)
    assert proposal.ledgers['S'].accepted_uptake_molecules == 1.
    payload = local_field_state_to_dict(proposal.after)
    assert local_field_state_to_dict(local_field_state_from_dict(payload)) == payload


def test_dense_mass_agrees_with_compensated_reference():
    rng = np.random.default_rng(14)
    values = np.exp(rng.uniform(-40, 40, 29_117))
    factor = 75.2767595
    reference = math.fsum(float(v)*factor for v in values)
    assert abs(_mass(values, factor)-reference) <= 2*math.ulp(reference)


def test_source_support_handles_radius_larger_than_domain_without_integer_overflow():
    grid = GridDomain.volume(3, 4, 5, .5, 1., 2.)
    state = make_local_field_state(grid, [FieldSpecies('S', 0., 0.)],
        sources=[LocalSource('source', 'S', (.75, 2., 5.), 1e308, 60., 60.)])
    with np.errstate(all='raise'):
        result = propose_local_field_step(state, 1.)
    assert np.all(np.asarray(result.after.concentrations_uM['S']) > 0)
    assert math.isclose(result.ledgers['S'].field_after_molecules, 60., rel_tol=1e-14)


def test_micro_uptake_uses_actual_debit_and_keeps_unfulfilled_request_in_field():
    # Captured from step 4 of the full 128 um case: old-field requests can be
    # extremely small relative to a newly diffused voxel's inventory.
    before, factor, requested = 1.1748584437223487e-14, 75.2767595, 8.710688988731363e-22
    after, accepted, shortfall = _voxel_uptake(before, factor, [requested], 1.)
    actual = (Fraction(before)-Fraction(after))*Fraction(factor)
    assert Fraction(0) <= actual <= Fraction(requested)
    assert 0 < accepted[0] <= requested
    assert shortfall > 0
    assert abs(actual-Fraction(accepted[0])) <= Fraction(8*math.ulp(accepted[0]))
    assert math.isclose(accepted[0]+shortfall, requested, rel_tol=1e-15)


def test_unrepresentable_debit_never_creates_phantom_cell_nutrient():
    after, accepted, shortfall = _voxel_uptake(1., 75., [1e-30, 3e-30], 1.)
    assert after == 1.
    assert accepted == (0., 0.)
    assert math.isclose(shortfall, 4e-30, rel_tol=1e-15)


def test_micro_uptake_remains_proportional_and_independent_of_cell_order():
    before, factor = 1.1748584437223487e-14, 75.2767595
    fluxes = [2e-22, 6e-22]
    after, accepted, shortfall = _voxel_uptake(before, factor, fluxes, 1.)
    reversed_after, reversed_accepted, reversed_shortfall = _voxel_uptake(before, factor, fluxes[::-1], 1.)
    assert (after, shortfall) == (reversed_after, reversed_shortfall)
    assert accepted == reversed_accepted[::-1]
    assert all(0 <= a <= r for a, r in zip(accepted, fluxes))
    assert math.isclose(accepted[1]/accepted[0], 3., rel_tol=1e-15)


@pytest.mark.skipif(CUDA not in available_backends(), reason='CUDA float64 kernel unavailable')
@pytest.mark.parametrize('grid', [GridDomain.thin_layer(7, 5, .5, 1., 2.),
                                 GridDomain.volume(7, 5, 3, .5, 1., 2.),
                                 GridDomain.volume(24, 24, 24, .5, .5, .5)])
def test_cuda_matches_cpu_release_diffusion_shared_uptake_and_solid_wall(grid):
    # An impermeable slab separates two initial concentrations. Add a finite
    # source and two competing cells on one side; nothing may cross the slab.
    initial = np.ones(grid.shape)
    initial[:, :, 3] = 0
    initial[:, :, 4:] = 7
    wall = SolidAABB((3*grid.dx_um, 0., 0.), (4*grid.dx_um, grid.extent_um[1], grid.extent_um[2]))
    state = make_local_field_state(grid, [FieldSpecies('S', initial, 4.)],
        sources=[LocalSource('source', 'S', (grid.dx_um/2, grid.dy_um/2, grid.dz_um/2), 0., 300., 100.)],
        obstacles=[wall])
    gpu = replace(state, backend=CUDA)
    positions = [(grid.dx_um/2, grid.dy_um/2, grid.dz_um/2)]*2
    for _ in range(3):
        kwargs = dict(cell_ids=['a','b'], positions_um=positions, requested_uptake_molecules_s={'S':[23.,11.]})
        cpu_result = propose_local_field_step(state, .2, **kwargs)
        gpu_result = propose_local_field_step(gpu, .2, **kwargs)
        np.testing.assert_array_equal(cpu_result.after.concentrations_uM['S'], gpu_result.after.concentrations_uM['S'])
        assert cpu_result.ledgers == gpu_result.ledgers
        assert cpu_result.accepted_uptake_molecules == gpu_result.accepted_uptake_molecules
        actual = np.asarray(gpu_result.after.concentrations_uM['S']).reshape(grid.shape)
        assert np.all(actual[:, :, 3] == 0.)
        assert np.all(actual[:, :, 4:] == 7.)
        state, gpu = cpu_result.after, gpu_result.after
