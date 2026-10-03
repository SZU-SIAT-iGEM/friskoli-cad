import numpy as np
import pytest

from friskoli_cad.science.processes import (allocate_inventory, cellulose_hydrolysis,
    exchange, lowpass, matched_trilinear_weights, mesh_contains, mesh_geometry,
    schedule_amount, stokes_einstein_scale, upwind_advection, validate_property_catalog)


def test_shared_inventory_has_single_source_and_explicit_destinations():
    remaining, maintenance, growth, unmet = allocate_inventory([10., 0.], [5., 1.], 2., 2., [8., 8.])
    np.testing.assert_allclose(remaining + maintenance + growth, [15., 1.])
    np.testing.assert_allclose(remaining, [3., 0.])
    np.testing.assert_allclose(unmet, [0., 1.5])


def test_exact_filters_and_signed_exchange():
    assert lowpass(0., 1., 2., 2.) == pytest.approx(1 - np.exp(-1))
    after, delta = exchange(np.array([1., 3.]), 2., 1., 1.)
    np.testing.assert_allclose(after - delta, [1., 3.])
    assert delta[0] > 0 and delta[1] < 0
    assert stokes_einstein_scale(5., 300., 300., .002, .001) == 2.5


def test_schedule_boundary_and_partition_invariance():
    events = [{'kind': 'pulse', 'time_s': 0., 'amount_molecules': 2.},
              {'kind': 'pulse', 'time_s': 1., 'amount_molecules': 3.},
              {'kind': 'rate', 'start_s': .25, 'end_s': 1.25, 'rate_molecules_s': 8.}]
    assert schedule_amount(events, 0., 0., initialize=True) == 2.
    assert schedule_amount(events, 0., 2.) == 11.
    assert sum(schedule_amount(events, t, .5) for t in (0., .5, 1., 1.5)) == 11.
    assert schedule_amount(events, 1., 1.) == 2.


@pytest.mark.parametrize('periodic', [True, False])
def test_advection_constant_velocity_conserves_mass_and_positivity(periodic):
    field = np.zeros((3, 4, 7)); field[1, 2, 3] = 100.
    moved = upwind_advection(field, (3., -2., 1.), (1., 2., 1.), 3., periodic=periodic)
    assert moved.sum() == pytest.approx(field.sum(), rel=2e-14)
    assert moved.min() >= 0
    assert not np.array_equal(field, moved)


def test_advection_cannot_cross_solid():
    field = np.zeros((2, 2, 5)); field[:, :, 1] = 1
    blocked = np.zeros(field.shape, bool); blocked[:, :, 2] = True
    moved = upwind_advection(field, (1., 0., 0.), (1., 1., 1.), 2., blocked=blocked)
    assert moved[:, :, 2:].sum() == 0
    assert moved.sum() == pytest.approx(field.sum())


def test_closed_oriented_tetrahedron_and_winding():
    vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
    faces = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
    v, f, volume = mesh_geometry(vertices, faces)
    assert volume == pytest.approx(1 / 6)
    assert mesh_contains([[.1, .1, .1], [1., 1., 1.]], v, f).tolist() == [True, False]
    with pytest.raises(ValueError, match='closed'):
        mesh_geometry(vertices, faces[:-1])
    with pytest.raises(ValueError, match='outward'):
        mesh_geometry(vertices, np.array(faces)[:, ::-1])


def test_matching_sample_deposit_weights_are_partition_of_unity():
    weights = matched_trilinear_weights([[0., 0., 0.], [1.2, 2.3, 1.1]], (3, 4, 5), (1., 1., 1.))
    field = np.arange(60.)
    for row in weights:
        assert sum(row.values()) == pytest.approx(1.)
        deposited = np.zeros(60)
        for index, weight in row.items():
            deposited[index] += 4 * weight
        assert deposited.sum() == pytest.approx(4.)
        assert field @ deposited == pytest.approx(4 * sum(field[i] * w for i, w in row.items()))


def test_cellulose_water_and_carbon_stoichiometry():
    for route, carbon_count in [('glucose', 6), ('cellobiose', 12)]:
        reaction = cellulose_hydrolysis(100, 10, 2, 1, route=route)
        assert reaction['remaining_agu'] == 80
        assert reaction['consumed_agu'] * 6 == reaction['product_molecules'] * carbon_count
        assert reaction['water_consumed_molecules'] == reaction['product_molecules']


def test_properties_distinguish_unknown_from_zero():
    item = {'id': 'viscosity', 'quantity': 'dynamic_viscosity', 'unit': 'Pa*s',
            'value': None, 'conditions': {'temperature_K': 300}, 'source': 'unmeasured', 'uncertainty': None}
    assert validate_property_catalog([item])[0]['value'] is None
    with pytest.raises(ValueError, match='conditions'):
        validate_property_catalog([{**item, 'conditions': {}}])
