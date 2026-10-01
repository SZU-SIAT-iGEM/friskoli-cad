"""Numerical transactions and real lifecycle/chemical feedback behaviors."""
from copy import deepcopy
import json
import math

import numpy as np
import pytest

from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.engine.chemotaxis_checkpoint import restore_checkpoint
from friskoli_cad.engine.runtime import SimulationError
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import canonical_bytes, canonical_loads


def node(project, nid):
    return next(n for n in project['graph']['nodes'] if n['id'] == nid)


def parameter(project, nid, key, value):
    node(project, nid)['parameters'][key]['value'] = value


@pytest.mark.parametrize('name', ['pts-a', 'pts-b', 'mcp', 'control', 'materials', 'lifecycle'])
def test_real_templates_execute_and_restore_complete_next_steps(name):
    project = make_example('chemotaxis-' + name)
    sim = simulation_from_project(project)
    for _ in range(3):
        sim.step(.1)
    payload = json.loads(json.dumps(sim.checkpoint(), allow_nan=False))
    restored = restore_checkpoint(project, payload)
    assert restored.checkpoint() == payload
    for dt in (.15, .05):
        sim.step(dt)
        restored.step(dt)
        assert restored.checkpoint() == sim.checkpoint()
    assert sim.current.metrics['by_group']['cells']['initial_count'] == 8


@pytest.mark.parametrize('name', ['pts-a', 'pts-b', 'mcp', 'control', 'materials', 'lifecycle'])
def test_canonical_integer_parameters_preserve_complete_numerical_state(name):
    project = make_example('chemotaxis-' + name)
    normalized = canonical_loads(canonical_bytes(project))
    if name == 'materials':
        assert type(node(normalized, 'surface_enzyme')['parameters']['enzyme_copies']['value']) is int
    reference = simulation_from_project(project, seed=0)
    sim = simulation_from_project(normalized, seed=0)
    for _ in range(3):
        reference.step(.01)
        sim.step(.01)
        assert sim.checkpoint() == reference.checkpoint()
    restored = restore_checkpoint(normalized, sim.checkpoint())
    restored.step(.05)
    reference.step(.05)
    assert restored.checkpoint() == reference.checkpoint()


def test_mcp_nutrient_background_is_constant_and_has_explicit_supply():
    project = make_example('chemotaxis-mcp')
    sim = simulation_from_project(project)
    original = sim.current.concentration_fields['nutrient'].copy()
    ligand_initial = math.fsum(sim.fields.concentrations_uM['ligand'])
    for _ in range(10):
        sim.step(.2)
    np.testing.assert_array_equal(original, sim.current.concentration_fields['nutrient'])
    assert sim.supply_totals['nutrient'] == pytest.approx(sim.uptake_totals['accepted_uptake'])
    assert sim.supply_totals['nutrient'] > 0
    assert math.fsum(sim.fields.concentrations_uM['ligand']) == pytest.approx(ligand_initial)
    assert sim.current.cell_frame['cells'][0]['channels']['motor_signal.activity'] >= 0


def test_feedback_controls_next_interval_and_constant_control_is_independent():
    left = make_example('chemotaxis-control')
    right = deepcopy(left)
    parameter(left, 'motor_signal', 'bias', 0.)
    parameter(right, 'motor_signal', 'bias', 1.)
    for project in (left, right):
        parameter(project, 'motility', 'minimum_tumble_rate_s', 0.)
        parameter(project, 'motility', 'maximum_tumble_rate_s', 20.)
    a, b = simulation_from_project(left), simulation_from_project(right)
    a.step(1.)
    b.step(1.)
    assert sum(a.outputs['motility']['turns']) == 0
    assert sum(b.outputs['motility']['turns']) > 30
    assert a.streams.to_dict()['streams'] == []
    assert a.current.cell_frame['cells'][0]['position_um'] != b.current.cell_frame['cells'][0]['position_um']


def test_growth_split_preserves_available_nutrient_copies_and_volume():
    project = make_example('chemotaxis-lifecycle')
    parameter(project, 'motility', 'speed_um_s', 0.)
    parameter(project, 'division', 'added_area_um2', 0.)
    parameter(project, 'growth', 'max_growth_per_min', 0.)
    parameter(project, 'health', 'death_max_per_min', 0.)
    parameter(project, 'surface_enzyme', 'turnover_per_min', 0.)
    parameter(project, 'surface_enzyme', 'synthesis_copies_min', 0.)
    sim = simulation_from_project(project)
    volume, stock, copies = (sum(sim.state[key][port]) for key, port in
        [('growth', 'volume'), ('growth', 'intracellular_molecules'), ('surface_enzyme', 'enzyme_copies')])
    sim.step(.1)
    assert len(sim.current.cell_frame['cells']) == 16
    assert len(sim.current.cell_frame['events']) == 8
    assert sum(sim.state['growth']['volume']) == pytest.approx(volume)
    assert sum(sim.state['growth']['intracellular_molecules']) == pytest.approx(stock + sim.uptake_totals['accepted_uptake'])
    assert sum(sim.state['surface_enzyme']['enzyme_copies']) == pytest.approx(copies)
    assert len(set(sim.world.groups['cells'].ids)) == 16
    restored = restore_checkpoint(project, json.loads(json.dumps(sim.checkpoint())))
    sim.step(.1)
    restored.step(.1)
    assert sim.checkpoint() == restored.checkpoint()


def test_real_death_keeps_residual_accounting_and_all_empty_population_continues():
    project = make_example('chemotaxis-lifecycle')
    parameter(project, 'health', 'initial_health', 0.)
    parameter(project, 'health', 'repair_per_min', 0.)
    parameter(project, 'health', 'death_max_per_min', 1e6)
    sim = simulation_from_project(project)
    sim.step(.1)
    assert sim.world.groups['cells'].ids == ()
    assert len(sim.current.cell_frame['events']) == 8
    assert len(sim.dead_material) == 8
    details = sim.current.lifecycle_details['deaths']
    assert len(details) == 8
    assert all(d['node_id'] == 'health' and d['time_s'] == .1 and d['group_id'] == 'cells'
               and d['random_draw'] < d['probability'] for d in details)
    residual = sum(v['residual_molecules']['nutrient'] for v in sim.dead_material.values())
    assert sim.physiology_ledger['nutrient']['removed_residual_molecules'] == pytest.approx(residual)
    residence = sim.current.metrics['by_group']['cells']['mean_residence_s']
    restored = restore_checkpoint(project, json.loads(json.dumps(sim.checkpoint())))
    sim.step(.2)
    restored.step(.2)
    assert sim.checkpoint() == restored.checkpoint()
    assert sim.current.metrics['by_group']['cells']['mean_residence_s'] == residence


def test_late_failure_rolls_back_rng_physio_ids_inventory_and_observations(monkeypatch):
    project = make_example('chemotaxis-lifecycle')
    parameter(project, 'division', 'added_area_um2', 0.)
    sim, reference = simulation_from_project(project), simulation_from_project(project)
    before = sim.checkpoint()
    method = sim._with_metrics
    def fail(*args):
        raise ValueError('injected after candidate frame validation')
    monkeypatch.setattr(sim, '_with_metrics', fail)
    with pytest.raises(SimulationError, match='injected'):
        sim.step(.2)
    assert sim.checkpoint() == before
    monkeypatch.setattr(sim, '_with_metrics', method)
    sim.step(.2)
    reference.step(.2)
    assert sim.checkpoint() == reference.checkpoint()


def test_changed_motor_rate_uses_existing_unit_hazard():
    from friskoli_cad.engine.hazard_walk import HazardWalkState
    project = make_example('chemotaxis-control')
    parameter(project, 'motility', 'minimum_tumble_rate_s', 0.)
    parameter(project, 'motility', 'maximum_tumble_rate_s', 4.)
    parameter(project, 'motor_signal', 'bias', .5)
    sim = simulation_from_project(project)
    # Directly install a candidate clock to verify runtime rate wiring exactly.
    sim.walks = {cid: HazardWalkState(w.heading, remaining_hazard=1.) for cid, w in sim.walks.items()}
    sim.step(.25)
    assert all(w.remaining_hazard == .5 for w in sim.walks.values())


def test_empty_initial_population_and_unused_species_have_no_fake_cells():
    project = make_example('chemotaxis-mcp')
    for key in ('ids', 'positions_um', 'orientation_xyzw', 'initial_geometry'):
        project['groups']['cells'][key] = []
    sim = simulation_from_project(project)
    sim.step(.1)
    assert sim.current.cell_frame['cells'] == []
    assert sim.current.metrics['by_group']['cells']['region_fraction'] is None
    assert restore_checkpoint(project, sim.checkpoint()).checkpoint() == sim.checkpoint()


def test_blocked_growth_does_not_consume_its_nutrient_or_stop_time():
    project = make_example('chemotaxis-lifecycle')
    parameter(project, 'motility', 'speed_um_s', 0.)
    parameter(project, 'growth', 'max_growth_per_min', 1e6)
    parameter(project, 'growth', 'initial_molecules', 1e5)
    parameter(project, 'growth', 'volume_yield_um3_molecule', 1.)
    parameter(project, 'health', 'death_max_per_min', 0.)
    sim = simulation_from_project(project)
    volume = sim.state['growth']['volume'].copy()
    sim.step(1.)
    assert sim.time_s == 1.
    assert np.all(sim.outputs['growth']['blocked'] == 1)
    assert np.all(sim.outputs['growth']['used_molecules'] == 0)
    np.testing.assert_array_equal(sim.state['growth']['volume'], volume)


def test_rebuilt_growth_expression_and_health_execute_the_declared_bindings():
    project = make_example('chemotaxis-lifecycle')
    rebuilt = make_example('chemotaxis-pts-a')
    project['graph']['nodes'] = [deepcopy(node(rebuilt, 'capacity')) if n['id'] == 'capacity' else n for n in project['graph']['nodes']]
    growth = node(project, 'growth')
    growth['module_id'] = 'growth.nutrient_monod'
    growth['parameters']['half_saturation_um'] = {'value': 1., 'unit': 'uM', 'provenance': {'kind': 'example', 'reference': 'numerical test'}}
    parameter(project, 'surface_enzyme', 'policy', 'rebuilt')
    parameter(project, 'surface_enzyme', 'synthesis_copies_min', 100.)
    parameter(project, 'health', 'policy', 'rebuilt')
    sim = simulation_from_project(project)
    before = sim.state['surface_enzyme']['enzyme_copies'].copy()
    sim.step(.1)
    assert np.all(sim.state['surface_enzyme']['enzyme_copies'] > before)
    assert np.all(sim.outputs['growth']['growth_rate'] > 0)
    assert np.all((sim.outputs['health']['health'] >= 0) & (sim.outputs['health']['health'] <= 1))
    assert restore_checkpoint(project, sim.checkpoint()).checkpoint() == sim.checkpoint()


def test_reservoir_supplies_newly_exposed_voxels_when_another_material_exhausts():
    project = make_example('chemotaxis-mcp')
    material = make_example('chemotaxis-materials')
    for nid in ('substrate', 'degradation', 'surface_enzyme'):
        project['graph']['nodes'].append(deepcopy(node(material, nid)))
    parameter(project, 'substrate', 'species', 'ligand')
    parameter(project, 'substrate', 'initial_molecules', 100.)
    parameter(project, 'degradation', 'kcat_s', 1e6)
    parameter(project, 'motility', 'speed_um_s', 0.)
    parameter(project, 'uptake_request', 'turnover_s', 0.)
    project['groups']['cells']['positions_um'][3] = [79., 45., 1.]
    sim = simulation_from_project(project)
    before = math.fsum(sim.fields.concentrations_uM['nutrient']) * sim.world.grid.molecules_per_uM_voxel
    sim.step(.1)
    assert sim.materials['substrate'].remaining_molecules == 0
    assert not any(sim.fields.blocked)
    assert set(sim.fields.concentrations_uM['nutrient']) == {1.}
    after = math.fsum(sim.fields.concentrations_uM['nutrient']) * sim.world.grid.molecules_per_uM_voxel
    assert sim.supply_totals['nutrient'] == pytest.approx(after - before)
    assert restore_checkpoint(project, sim.checkpoint()).checkpoint() == sim.checkpoint()
