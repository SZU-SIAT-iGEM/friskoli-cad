"""Source B stochastic cycles and system radial observations, on small grids."""
from copy import deepcopy
import math

import numpy as np
import pytest

from friskoli_cad.engine.chemotaxis_modules import chemotaxis_registry
from friskoli_cad.engine.chemotaxis_templates import make_example, make_n5_acceptance
from friskoli_cad.engine.chemotaxis_checkpoint import restore_checkpoint
from friskoli_cad.engine.observations import initial_observation, advance_observation, observation_metrics, validate_observation_state
from friskoli_cad.engine.runtime import SimulationError
from friskoli_cad.project import simulation_from_project
from friskoli_cad.science.physiology import surface_adder_delta


def node(project, nid):
    return next(n for n in project['graph']['nodes'] if n['id'] == nid)


def put(project, nid, **values):
    for key, value in values.items():
        node(project, nid)['parameters'][key]['value'] = value


def small_v2():
    p = make_example('chemotaxis-lifecycle')
    module = chemotaxis_registry().get('division.area_adder', '2.0.0')
    n = node(p, 'division')
    n['module_version'] = '2.0.0'
    n['parameters'] = {key: {'value': value, 'unit': module.manifest['parameters'][key].get('unit', '1'),
        'provenance': {'kind': 'example', 'reference': 'Constructed small lifecycle event test'}} for key, value in module.default_parameters.items()}
    put(p, 'motility', speed_um_s=0.)
    put(p, 'health', death_max_per_min=0.)
    return p


def test_original_division_template_retains_v1_and_parameters():
    n = node(make_example('chemotaxis-lifecycle'), 'division')
    assert n['module_version'] == '1.0.0'
    assert set(n['parameters']) == {'added_area_um2', 'minimum_fraction', 'maximum_fraction'}


def test_source_folded_threshold_floor_and_negative_normal():
    args = dict(reference_birth_volume_um3=.5, target_volume_um3=1., radius_um=.4, cv=.15, minimum_area_um2=1e-6)
    assert float(surface_adder_delta(**args, normal_deviate=0)) == pytest.approx(2.5)
    assert float(surface_adder_delta(**args, normal_deviate=-10)) == pytest.approx(1.25)
    assert float(surface_adder_delta(**args, normal_deviate=-1/.15)) == 1e-6
    args.update(reference_birth_volume_um3=1.1, cv=0.)
    assert float(surface_adder_delta(**args, normal_deviate=0)) == 1e-6


def test_cycle_threshold_seed_persistence_and_checkpoint():
    p = small_v2()
    sim, other = simulation_from_project(p, seed=1), simulation_from_project(p, seed=2)
    initial = sim.state['division']['required_area'].copy()
    assert np.all(initial >= 1e-6)
    assert not np.array_equal(initial, other.state['division']['required_area'])
    put(p, 'growth', max_growth_per_min=0.)
    sim = simulation_from_project(p, seed=1)
    sim.step(.01)
    np.testing.assert_array_equal(sim.state['division']['required_area'], initial)
    restored = restore_checkpoint(p, sim.checkpoint())
    sim.step(.02)
    restored.step(.02)
    assert restored.checkpoint() == sim.checkpoint()


def test_accepted_division_resamples_both_cycles_and_conserves():
    p = small_v2()
    put(p, 'division', target_volume_um3=.5)
    sim = simulation_from_project(p)
    stock = sum(sim.state['growth']['intracellular_molecules'])
    sim.step(.1)
    assert len(sim.world.groups['cells'].ids) == 16
    events = sim.current.cell_frame['events']
    assert len(events) == 8
    assert set(sim.state['division']) == {'birth_area', 'required_area'}
    assert np.all(sim.state['division']['required_area'] >= 1e-6)
    assert len([s for s in sim.streams.to_dict()['streams'] if s['key'][-1] == 'division_threshold']) == 16
    assert sum(sim.state['growth']['intracellular_molecules']) + sim.physiology_ledger['nutrient']['growth_consumed_molecules'] == pytest.approx(stock + sim.uptake_totals['accepted_uptake'])
    restored = restore_checkpoint(p, sim.checkpoint())
    sim.step(.01)
    restored.step(.01)
    assert restored.checkpoint() == sim.checkpoint()


def test_v2_late_failure_preserves_all_rng_and_cycle_state(monkeypatch):
    p = small_v2()
    put(p, 'division', target_volume_um3=.5)
    sim = simulation_from_project(p)
    before = sim.checkpoint()
    def fail(*args):
        raise ValueError('late radial observation failure')
    monkeypatch.setattr(sim, '_with_metrics', fail)
    with pytest.raises(SimulationError, match='late radial'):
        sim.step(.1)
    assert sim.checkpoint() == before


def test_zero_cv_and_sd_are_deterministic_and_do_not_draw():
    p = small_v2()
    put(p, 'division', area_cv=0., split_sd=0., target_volume_um3=.5)
    sim = simulation_from_project(p)
    sim.step(.1)
    assert not any(s['key'][0] == 'division' for s in sim.streams.to_dict()['streams'])


def test_split_normal_is_clipped_before_geometric_partition(monkeypatch):
    p = small_v2()
    put(p, 'division', area_cv=0., target_volume_um3=.5)
    monkeypatch.setattr('friskoli_cad.engine.chemotaxis_runtime._normal_draw', lambda stream: -100.)
    sim = simulation_from_project(p)
    sim.step(.1)
    group = sim.world.groups['cells']
    volumes = dict(zip(group.ids, sim.state['growth']['volume']))
    for event in sim.current.cell_frame['events']:
        first, second = volumes[event['parent_id']], volumes[event['child_id']]
        assert first / (first + second) == pytest.approx(.35)


def test_checkpoint_rejects_missing_threshold_rng_and_zero_threshold():
    from friskoli_cad.engine.spatial_checkpoint import _hash
    p = small_v2()
    sim = simulation_from_project(p)
    sim.step(.01)
    for mutation in ('rng', 'threshold'):
        payload = sim.checkpoint()
        if mutation == 'rng':
            payload['random_streams']['streams'] = []
        else:
            payload['state']['division']['required_area'][0] = 0.
            payload['outputs']['division']['required_area'][0] = 0.
        payload['payload_sha256'] = _hash({key: value for key, value in payload.items() if key != 'payload_sha256'})
        with pytest.raises(SimulationError, match='threshold|Cycle'):
            restore_checkpoint(p, payload)


def test_acceptance_is_plain_graph_with_source_b_values_and_matched_radial_initialization():
    p = make_n5_acceptance()
    from friskoli_cad.project import validate_project
    registry = chemotaxis_registry()
    validate_project(p, registry.manifests, registry=registry)
    assert p['domain'] == {'geometry': 'volume', 'counts_xyz': [256]*3, 'spacing_um_xyz': [.5]*3}
    assert [len(g['ids']) for g in p['groups'].values()] == [100, 100]
    a, b = (np.asarray(p['groups'][g]['positions_um']) for g in ('pts', 'control'))
    np.testing.assert_allclose(a + b, 128., rtol=0, atol=0)
    for prefix in ('', 'control_'):
        assert node(p, prefix+'growth')['parameters']['volume_yield_um3_molecule']['value'] == 1.1672551805315156e-9
        assert node(p, prefix+'growth')['parameters']['max_growth_per_min']['value'] == .005
        assert node(p, prefix+'division')['module_version'] == '2.0.0'
        assert node(p, prefix+'capacity')['parameters']['reference_pts_copies']['value'] == 500
    assert node(p, 'control_fixed_bias')['parameters']['bias']['value'] == pytest.approx(.13571263629578523)
    assert next(e for e in p['graph']['edges'] if e['to'] == {'node': 'control_motility', 'port': 'motor_bias'})['from']['node'] == 'control_fixed_bias'


def test_full_b_graph_runs_with_radial_checkpoint_on_small_spatial_fixture():
    p = make_n5_acceptance()
    # This is a reduced numerical integration fixture, not the 256^3 acceptance run.
    p['domain'].update(counts_xyz=[8]*3, spacing_um_xyz=[16.]*3)
    for group in p['groups'].values():
        for key in ('ids', 'positions_um', 'orientation_xyzw', 'initial_geometry'):
            group[key] = group[key][:2]
    sim = simulation_from_project(p)
    sim.step(.01)
    for gid in ('pts', 'control'):
        assert sim.current.metrics['by_group'][gid]['radial']['live_founder_count'] == 2
    restored = restore_checkpoint(p, sim.checkpoint())
    sim.step(.01)
    restored.step(.01)
    assert restored.checkpoint() == sim.checkpoint()


def test_radial_history_death_descendants_and_strict_restore():
    p = {'domain': {'counts_xyz': [20]*3, 'spacing_um_xyz': [1.]*3},
        'groups': {'a': {'ids': ['a'], 'positions_um': [[15.,10.,10.]]}},
        'observation': {'id':'center','label':'center','axis':0,'region_lower_um':[0.]*3,'region_upper_um':[20.]*3,
                        'radial_center_um':[10.]*3,'radial_radii_um':[2.,6.]}}
    def frame(t, positions):
        return {'time_s': t, 'cells': [{'id':cid,'group_id':'a','position_um':pos} for cid,pos in positions]}
    state = initial_observation(p, frame(0., [('a',[15.,10.,10.])]))
    now = frame(1., [('a',[11.,10.,10.]), ('child',[10.,10.,10.])])
    state = advance_observation(state, now)
    radial = observation_metrics(state, now)['by_group']['a']['radial']
    assert radial['mean_inward_displacement_um'] == 4.
    assert radial['live_founder_count'] == radial['live_descendant_count'] == 1
    assert radial['shells'][0]['founder_mean_first_arrival_s'] == 1.
    assert radial['shells'][0]['founder_mean_residence_s'] == 0.
    now = frame(2., [('child',[10.,10.,10.])])
    state = advance_observation(state, now)
    assert validate_observation_state(state, p, now) == state
    radial = observation_metrics(state, now)['by_group']['a']['radial']
    assert radial['mean_inward_displacement_um'] is None
    assert radial['shells'][0]['founder_mean_residence_s'] == 1.
    assert radial['shells'][1]['founder_mean_residence_s'] == 2.
    assert radial['shells'][0]['volume_enrichment'] == pytest.approx(8000/(4*math.pi*8/3))
    bad = deepcopy(state)
    bad['cohort']['a']['radial_first_arrival_s'][0] = None
    with pytest.raises(ValueError, match='radial'):
        validate_observation_state(bad, p, now)


def test_realized_growth_keeps_sub_resolution_nutrient_and_zero_actual_rate():
    from friskoli_cad.engine.growth_system import realize_capsule_growth
    nutrient = 8.710688988731363e-22
    result, lengths = realize_capsule_growth(nutrient, 1.2613850609910124, .8, nutrient, .01, 1.1672551805315156e-9)
    assert float(result.volume_um3) == .5
    assert float(result.used_molecules) == float(result.actual_growth_per_min) == 0.
    assert float(result.intracellular_molecules) == nutrient
    assert float(lengths) == 1.2613850609910124


def test_realized_growth_never_exceeds_exact_nutrient_or_geometry_budget():
    from fractions import Fraction
    from friskoli_cad.engine.growth_system import realize_capsule_growth
    from friskoli_cad.science.physiology import capsule_volume_um3
    lengths = np.full(80, 1.2613850609910124)
    nutrient = np.logspace(-25, 5, len(lengths))
    yield_v = 1.1672551805315156e-9
    result, actual_lengths = realize_capsule_growth(nutrient, lengths, .8, nutrient, .01, yield_v)
    np.testing.assert_array_equal(result.volume_um3, capsule_volume_um3(actual_lengths, .8))
    assert np.all(result.used_molecules <= nutrient)
    assert np.all(result.intracellular_molecules >= 0)
    np.testing.assert_allclose(result.intracellular_molecules + result.used_molecules, nutrient, rtol=2e-16)
    np.testing.assert_array_equal(result.actual_growth_per_min, (result.volume_um3 - .5) / .5 / (.01/60))
    for n, volume, used in zip(nutrient, result.volume_um3, result.used_molecules):
        increment = Fraction(float(volume)) - Fraction(.5)
        assert 0 <= increment <= Fraction(float(n)) * Fraction(yield_v)
        if used:
            assert abs(float(increment) - used * yield_v) <= 1e-14 * float(increment)


def test_sub_resolution_nutrient_accumulates_until_geometry_can_grow():
    from friskoli_cad.engine.growth_system import realize_capsule_growth
    stock, length, consumed = 0., 1.2613850609910124, 0.
    for _ in range(100):
        stock += 1e-8
        result, lengths = realize_capsule_growth(stock, length, .8, stock, .01, 1.1672551805315156e-9)
        stock, length = float(result.intracellular_molecules), float(lengths)
        consumed += float(result.used_molecules)
    assert length > 1.2613850609910124
    assert stock + consumed == pytest.approx(1e-6, rel=1e-14)
