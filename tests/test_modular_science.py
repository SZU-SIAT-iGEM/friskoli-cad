from copy import deepcopy
from importlib.resources import files
import json
import numpy as np
import pytest

from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.science_extensions import modular_registry
from friskoli_cad.engine.modular_checkpoint import restore_checkpoint, export_checkpoint


def project():
    p = json.loads(files('friskoli_cad').joinpath('examples/foundation_control.project.json').read_text(encoding='utf8'))
    p.update(project_version='0.6.0', execution_profile='modular-spatial-v1')
    p['graph']['protocol_version'] = '0.2.0'
    return p


def add(p, identifier, values, *, nid=None, population=False):
    nid = nid or identifier.replace('.', '_')
    m = modular_registry().get(identifier, '1.0.0').manifest
    parameters = {k: {'value': v, **({'unit': m['parameters'][k]['unit']} if 'unit' in m['parameters'][k] else {}),
                      'provenance': {'kind': 'example', 'reference': 'Constructed test, not biological calibration'}} for k, v in values.items()}
    p['graph']['nodes'].append({'id': nid, 'module_id': identifier, 'module_version': '1.0.0',
        'owner': {'kind': 'population' if population else 'environment', 'id': 'cells' if population else nid}, 'parameters': parameters})
    return nid


def edge(p, source, output, target, input, timing='same_step'):
    p['graph']['edges'].append({'id': f'{source}_{output}_to_{target}_{input}', 'from': {'node': source, 'port': output}, 'to': {'node': target, 'port': input}, 'timing': timing})


def test_registered_foundation_checkpoint_roundtrip_and_atomic_rejection():
    p = project(); sim = simulation_from_project(p, seed=17)
    sim.step(.01); saved = sim.checkpoint(); resumed = restore_checkpoint(p, saved)
    sim.step(.01); resumed.step(.01)
    assert sim.current.cell_frame == resumed.current.cell_frame
    np.testing.assert_array_equal(sim.fields.concentrations_uM['nutrient'], resumed.fields.concentrations_uM['nutrient'])
    before = sim.checkpoint()
    with pytest.raises(ValueError): sim.step(-1)
    assert sim.checkpoint() == before


def test_schedule_tzero_pulse_restore_and_net_input():
    p = project()
    nid = add(p, 'source.spatial_schedule', {'species': 'nutrient', 'lower_um': [0., 0., 0.], 'upper_um': [200., 100., 2.],
        'events': [{'kind': 'pulse', 'time_s': 0., 'amount_molecules': 100.}, {'kind': 'pulse', 'time_s': .02, 'amount_molecules': 250.}]})
    sim = simulation_from_project(p)
    assert sim.state[nid]['cumulative_input'] == 100
    sim.step(.01); resumed = restore_checkpoint(p, sim.checkpoint())
    sim.step(.01); resumed.step(.01)
    assert sim.state[nid]['cumulative_input'] == resumed.state[nid]['cumulative_input'] == 350
    np.testing.assert_array_equal(sim.fields.concentrations_uM['nutrient'], resumed.fields.concentrations_uM['nutrient'])


def test_initial_array_viscosity_oxygen_advection_are_runtime_modules():
    p = project()
    initial = np.zeros((1, 10, 20)); initial[0, 4, 5] = 1.
    add(p, 'field.initial_array', {'species': 'nutrient', 'values_um': initial.tolist()})
    vis = add(p, 'medium.viscosity_diffusion', {'species': 'nutrient', 'reference_diffusivity_um2_s': 1.,
        'temperature_k': 300., 'reference_temperature_k': 300., 'viscosity_pa_s': .002, 'reference_viscosity_pa_s': .001})
    oxygen = add(p, 'reaction.oxygen_consumption', {'species': 'nutrient', 'maximum_rate_um_s': 1., 'half_saturation_um': .1})
    add(p, 'field.advection_upwind', {'species': 'nutrient', 'velocity_xyz_um_s': [1., 0., 0.], 'velocity_source': 'constructed test', 'periodic': True})
    sim = simulation_from_project(p)
    assert sim.fields.diffusivities_um2_s['nutrient'] == .5
    sim.step(.1)
    assert sim.state[oxygen]['cumulative_consumed'] > 0
    restored = restore_checkpoint(p, sim.checkpoint())
    assert restored.fields.diffusivities_um2_s['nutrient'] == .5


def test_shared_maintenance_growth_accounts_accepted_once():
    p = project()
    old = next(n for n in p['graph']['nodes'] if n['module_id'] == 'metabolism.reserve_balance')
    old_id = old['id']; p['graph']['nodes'].remove(old)
    nid = add(p, 'metabolism.shared_inventory', {'species': 'nutrient', 'initial_molecules': 100.,
        'maintenance_molecules_s': 1., 'max_growth_per_min': 1., 'volume_yield_um3_molecule': .0001}, nid=old_id, population=True)
    p['run']['channels'][old_id + '.used_molecules']['quantity'] = 'consumed_amount'
    sim = simulation_from_project(p); sim.step(.1)
    amount = np.asarray(sim.outputs['accepted_uptake']['accepted_amount'])
    out = sim.outputs[nid]
    np.testing.assert_allclose(out['intracellular_molecules'] + out['used_molecules'] + out['maintenance_used'], 100. + amount, atol=1e-12)
    assert np.sum(out['used_molecules']) > 0
    restore_checkpoint(p, sim.checkpoint())


def test_seeded_distribution_persists_through_steps():
    p = project()
    nid = add(p, 'control.seeded_distribution', {'distribution': 'normal', 'seed': 17, 'mean': 1., 'spread': .1, 'minimum': .5, 'maximum': 1.5}, population=True)
    sim = simulation_from_project(p); initial = np.asarray(sim.outputs[nid]['value']).copy(); sim.step(.01)
    np.testing.assert_array_equal(sim.outputs[nid]['value'], initial)
    other = simulation_from_project(p); np.testing.assert_array_equal(other.outputs[nid]['value'], initial)


def test_cellulose_secreted_enzyme_real_field_transfer_and_lineage_observer():
    p = project()
    enzyme = add(p, 'enzyme.secreted', {'population_id': 'cells', 'initial_copies': 10., 'secretion_copies_cell_s': 0., 'turnover_s': 0.})
    reaction = add(p, 'reaction.cellulose_hydrolysis', {'species': 'nutrient', 'initial_agu_units': 100., 'turnover_s': 2., 'route': 'glucose', 'release_position_um': [100., 50., 1.]})
    edge(p, enzyme, 'enzyme_copies', reaction, 'enzyme_copies')
    observe = add(p, 'observer.conversion_lineage', {'initial_substrate_molecules': 100., 'external_substrate_molecules': 0., 'window_start_s': 0., 'window_end_s': 10.})
    edge(p, reaction, 'converted_amount', observe, 'converted_amount')
    sim = simulation_from_project(p); sim.step(.1)
    assert sim.outputs[reaction]['released_amount'] == pytest.approx(2.)
    assert sim.state[reaction]['agu_units'] == pytest.approx(98.)
    assert sim.metrics[observe]['conversion_fraction'] == pytest.approx(.02)
    restore_checkpoint(p, sim.checkpoint())


def test_death_residue_release_and_porous_body_lifecycle():
    p = project()
    # Explicit synthetic high-hazard fixture; never a production preset.
    add(p, 'life.background_hazard', {'hazard_s': 1e6}, population=True)
    residue = add(p, 'life.residue_release', {'species': 'nutrient', 'release_rate_s': 1., 'removal_threshold_molecules': .01})
    sim = simulation_from_project(p); sim.step(.01)
    assert len(sim.world.groups['cells'].ids) == 0
    residual = sum(record['residual_molecules']['nutrient'] for record in sim.dead_material.values())
    sim.step(.1)
    assert sim.outputs[residue]['released_amount'] == pytest.approx(residual * -np.expm1(-.1))
    assert len(sim.geometry_state[residue]['bodies']) == len(sim.dead_material)
    restored = restore_checkpoint(p, sim.checkpoint()); restored.step(.1); sim.step(.1)
    np.testing.assert_array_equal(restored.fields.concentrations_uM['nutrient'], sim.fields.concentrations_uM['nutrient'])


def test_mesh_voxelization_is_committed_and_checkpoint_validated():
    p = project()
    p['species']['nutrient']['initial_concentration']['value'] = 1.
    nid = add(p, 'geometry.triangle_mesh', {'vertices_xyz': [[1., 1., .2], [2., 1., .2], [1., 2., .2], [1., 1., 1.]],
        'faces': [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], 'scale_um': 1.})
    sim = simulation_from_project(p)
    assert np.count_nonzero(sim.fields.blocked) == 1
    assert sim.outputs[nid]['volume'] > 0
    sim.step(.01); restored = restore_checkpoint(p, sim.checkpoint())
    np.testing.assert_array_equal(restored.fields.blocked, sim.fields.blocked)


def test_transport_competing_with_uptake_never_creates_external_matter():
    p = project()
    p['species']['nutrient']['initial_concentration']['value'] = .00001
    for node in p['graph']['nodes']:
        if node['module_id'] == 'source.finite_local': node['parameters']['release_rate']['value'] = 0.
        if node['module_id'] == 'uptake.saturating_request': node['parameters']['maximum_flux_molecules_s']['value'] = 1e12
    add(p, 'field.advection_upwind', {'species': 'nutrient', 'velocity_xyz_um_s': [100., 0., 0.], 'velocity_source': 'constructed test', 'periodic': True})
    sim = simulation_from_project(p); initial = sum(sim.fields.concentrations_uM['nutrient']) * sim.world.grid.molecules_per_uM_voxel
    sim.step(.1)
    final = sum(sim.fields.concentrations_uM['nutrient']) * sim.world.grid.molecules_per_uM_voxel
    assert final + sim.ledger['nutrient']['consumed'] == pytest.approx(initial, rel=1e-12)
    assert sim.ledger['nutrient']['external_net'] == 0.


def test_growth_division_records_membrane_assumption_and_splits_inventory():
    p = project()
    old = next(n for n in p['graph']['nodes'] if n['module_id'] == 'metabolism.reserve_balance')
    old_id = old['id']; p['graph']['nodes'].remove(old)
    add(p, 'metabolism.shared_inventory', {'species': 'nutrient', 'initial_molecules': 1e6,
        'maintenance_molecules_s': 1., 'max_growth_per_min': 60., 'volume_yield_um3_molecule': .001}, nid=old_id, population=True)
    p['run']['channels'][old_id + '.used_molecules']['quantity'] = 'consumed_amount'
    division = add(p, 'division.volume_adder', {'added_volume_um3': .01, 'minimum_volume_um3': 0., 'daughter_fraction': .5}, population=True)
    edge(p, old_id, 'volume', division, 'volume')
    sim = simulation_from_project(p); initial_count = len(sim.world.groups['cells'].ids); sim.step(.2)
    assert len(sim.world.groups['cells'].ids) > initial_count
    assert sim.current.cell_frame['events'][0]['type'] == 'division'
    assert sim.metrics['system.division_geometry'][0]['added_membrane_area_um2'] > 0
    restored = restore_checkpoint(p, sim.checkpoint()); sim.step(.01); restored.step(.01)
    assert sim.current.cell_frame == restored.current.cell_frame


def test_material_adapter_graph_and_checkpoint():
    p = json.loads(files('friskoli_cad').joinpath('examples/foundation_materials.project.json').read_text(encoding='utf8'))
    p.update(project_version='0.6.0', execution_profile='modular-spatial-v1')
    p['graph']['protocol_version'] = '0.2.0'
    sim = simulation_from_project(p); sim.step(.01)
    resumed = restore_checkpoint(p, sim.checkpoint())
    sim.step(.01); resumed.step(.01)
    assert sim.current.cell_frame == resumed.current.cell_frame


def test_unknown_world_read_fails_preflight():
    from friskoli_cad.engine.modular_runtime import validate_modular_project
    registry = modular_registry()
    registry.get('signal.constant_bias', '1.0.0').execution_contract['reads'] = ['unknown_world_resource']
    with pytest.raises(Exception, match='Unknown world resource'):
        validate_modular_project(project(), registry.manifests, registry)


def test_shared_stock_retains_sub_ulp_arrival():
    from friskoli_cad.engine.science_advanced import shared_inventory
    from friskoli_cad.engine.module_api import StepContext
    p = {'initial_molecules': 1e20, 'maintenance_molecules_s': 0., 'max_growth_per_min': 0., 'volume_yield_um3_molecule': 1., 'species': 'nutrient'}
    world = {'length_um': np.array([2.]), 'diameter_um': np.array([1.])}
    initial = shared_inventory(StepContext(0., 0., 0, 'cells', ('c',), world, parameters=p), True)
    proposed = shared_inventory(StepContext(0., 1., 0, 'cells', ('c',), world, {'accepted_amount': np.array([1.])}, p, initial.state), False)
    assert proposed.state['intracellular_molecules'][0] == 1e20
    assert proposed.state['reserve_correction_molecules'][0] == 1.


def test_division_resource_limit_rejects_transaction_without_biological_block():
    p = project()
    old = next(n for n in p['graph']['nodes'] if n['module_id'] == 'metabolism.reserve_balance')
    old_id = old['id']; p['graph']['nodes'].remove(old)
    add(p, 'metabolism.shared_inventory', {'species': 'nutrient', 'initial_molecules': 1e6,
        'maintenance_molecules_s': 1., 'max_growth_per_min': 60., 'volume_yield_um3_molecule': .001}, nid=old_id, population=True)
    p['run']['channels'][old_id + '.used_molecules']['quantity'] = 'consumed_amount'
    division = add(p, 'division.volume_adder', {'added_volume_um3': .01, 'minimum_volume_um3': 0., 'daughter_fraction': .5}, population=True)
    edge(p, old_id, 'volume', division, 'volume')
    p['system_limits'] = {'max_cells': len(p['groups']['cells']['ids'])}
    sim = simulation_from_project(p); before = sim.checkpoint()
    with pytest.raises(Exception, match='system_limits.max_cells') as captured:
        sim.step(.2)
    assert captured.value.code == 'resource.cell_limit'
    assert sim.checkpoint() == before


def test_modular_maintenance_zero_arrival_rounding_can_continue():
    from friskoli_cad.engine.science_extensions import make_modular_example
    p = make_modular_example('modular-foundation')
    sim = simulation_from_project(p)
    for _ in range(30): sim.step(.01)
    resumed = restore_checkpoint(p, sim.checkpoint())
    sim.step(.01); resumed.step(.01)
    assert sim.current.cell_frame == resumed.current.cell_frame


def test_catalytic_output_cannot_be_spent_twice_but_observer_is_allowed():
    from friskoli_cad.engine.science_extensions import make_modular_example, ScientificModule
    from friskoli_cad.engine.modular_runtime import validate_modular_project
    from friskoli_cad.engine.runtime import ModuleRegistry
    from friskoli_cad.engine.module_api import ModuleProposal
    p = make_modular_example('modular-material')
    reaction = next(n for n in p['graph']['nodes'] if n['module_id'] == 'reaction.cellulose_hydrolysis')
    duplicate = deepcopy(reaction); duplicate['id'] = 'another_reaction'; duplicate['owner']['id'] = 'another_reaction'
    p['graph']['nodes'].append(duplicate)
    edge(p, 'enzyme_secreted', 'enzyme_copies', duplicate['id'], 'enzyme_copies')
    registry = modular_registry()
    with pytest.raises(Exception, match='catalytic capacity'):
        validate_modular_project(p, registry.manifests, registry)
    p['graph']['nodes'].pop(); p['graph']['edges'].pop()
    original = registry.get('reaction.cellulose_hydrolysis', '1.0.0')
    observer = ScientificModule('test.catalyst_observer', 'Catalyst observer', 'observation', 'environment',
        deepcopy(original.manifest['inputs']), {}, {}, {}, 'E', 'Read-only observer', lambda c, initial: ModuleProposal({}, {}))
    registry = ModuleRegistry([*registry._modules.values(), observer], 'modular-spatial-v1')
    p['graph']['nodes'].append({'id': 'observe_catalyst', 'module_id': 'test.catalyst_observer', 'module_version': '1.0.0',
        'owner': {'kind': 'environment', 'id': 'observe_catalyst'}, 'parameters': {}})
    edge(p, 'enzyme_secreted', 'enzyme_copies', 'observe_catalyst', 'enzyme_copies')
    validate_modular_project(p, registry.manifests, registry)


def test_surface_enzyme_owner_is_unique_per_population():
    from friskoli_cad.engine.modular_runtime import validate_modular_project
    p = json.loads(files('friskoli_cad').joinpath('examples/foundation_materials.project.json').read_text(encoding='utf8'))
    p.update(project_version='0.6.0', execution_profile='modular-spatial-v1'); p['graph']['protocol_version'] = '0.2.0'
    enzyme = next(n for n in p['graph']['nodes'] if n['module_id'] == 'surface.enzyme_activity')
    second = deepcopy(enzyme); second['id'] = 'second_surface_enzyme'; p['graph']['nodes'].append(second)
    registry = modular_registry()
    with pytest.raises(Exception, match='Duplicate enzyme.surface'):
        validate_modular_project(p, registry.manifests, registry)


def test_distinct_contact_providers_cannot_double_spend_same_population_pool():
    from friskoli_cad.engine.science_extensions import make_modular_example
    from friskoli_cad.engine.modular_runtime import validate_modular_project
    p = make_modular_example('modular-material')
    reaction = next(n for n in p['graph']['nodes'] if n['module_id'] == 'reaction.cellulose_hydrolysis')
    p['graph']['edges'] = [e for e in p['graph']['edges'] if e['to'] != {'node': reaction['id'], 'port': 'enzyme_copies'}]
    values = {'lower_um': [1., 1., 0.], 'upper_um': [2., 2., 2.], 'copies_per_cell': 10., 'contact_range_um': 1.}
    first = add(p, 'enzyme.contact_provider', values, nid='first_provider', population=True)
    second = add(p, 'enzyme.contact_provider', values, nid='second_provider', population=True)
    edge(p, first, 'enzyme_copies', reaction['id'], 'enzyme_copies')
    duplicate = deepcopy(reaction); duplicate['id'] = 'another_reaction'; duplicate['owner']['id'] = 'another_reaction'
    p['graph']['nodes'].append(duplicate); edge(p, second, 'enzyme_copies', duplicate['id'], 'enzyme_copies')
    registry = modular_registry()
    with pytest.raises(Exception, match='catalytic capacity'):
        validate_modular_project(p, registry.manifests, registry)


def test_material_and_contact_hydrolysis_share_surface_catalytic_pool():
    from friskoli_cad.engine.modular_runtime import validate_modular_project
    p = json.loads(files('friskoli_cad').joinpath('examples/foundation_materials.project.json').read_text(encoding='utf8'))
    p.update(project_version='0.6.0', execution_profile='modular-spatial-v1'); p['graph']['protocol_version'] = '0.2.0'
    gid = next(n['owner']['id'] for n in p['graph']['nodes'] if n['module_id'] == 'surface.enzyme_activity')
    provider = add(p, 'enzyme.contact_provider', {'lower_um': [1., 1., 0.], 'upper_um': [2., 2., 2.], 'copies_per_cell': 10., 'contact_range_um': 1.}, population=True)
    p['graph']['nodes'][-1]['owner']['id'] = gid
    reaction = add(p, 'reaction.cellulose_hydrolysis', {'species': 'nutrient', 'initial_agu_units': 100., 'turnover_s': 1., 'route': 'glucose', 'release_position_um': [1., 1., 1.]})
    edge(p, provider, 'enzyme_copies', reaction, 'enzyme_copies')
    registry = modular_registry()
    with pytest.raises(Exception, match='Surface catalyst pool'):
        validate_modular_project(p, registry.manifests, registry)


def test_linear_elongation_outputs_match_real_daughters_after_division():
    p = project()
    growth = add(p, 'growth.linear_elongation', {'elongation_rate': 1.}, population=True)
    geometry = add(p, 'geometry.capsule_derived', {}, population=True)
    division = add(p, 'division.volume_adder', {'added_volume_um3': .01, 'minimum_volume_um3': 0., 'daughter_fraction': .5}, population=True)
    edge(p, geometry, 'volume', division, 'volume')
    sim = simulation_from_project(p); sim.step(.2); sim.step(.2)
    assert any(e['type'] == 'division' for e in sim.events)
    np.testing.assert_array_equal(sim.outputs[growth]['length'], [g.length_um for g in sim.world.groups['cells'].geometry])
    np.testing.assert_array_equal(sim.outputs[growth]['diameter'], [g.diameter_um for g in sim.world.groups['cells'].geometry])
    restored = restore_checkpoint(p, sim.checkpoint())
    np.testing.assert_array_equal(restored.outputs[growth]['length'], sim.outputs[growth]['length'])


def test_same_step_death_and_division_preserve_inventory_accounts():
    p = project()
    old = next(n for n in p['graph']['nodes'] if n['module_id'] == 'metabolism.reserve_balance')
    old_id = old['id']; p['graph']['nodes'].remove(old)
    add(p, 'metabolism.shared_inventory', {'species': 'nutrient', 'initial_molecules': 1e6,
        'maintenance_molecules_s': 1., 'max_growth_per_min': 60., 'volume_yield_um3_molecule': .001}, nid=old_id, population=True)
    p['run']['channels'][old_id + '.used_molecules']['quantity'] = 'consumed_amount'
    division = add(p, 'division.volume_adder', {'added_volume_um3': .01, 'minimum_volume_um3': 0., 'daughter_fraction': .5}, population=True)
    edge(p, old_id, 'volume', division, 'volume')
    add(p, 'life.background_hazard', {'hazard_s': 2.}, population=True)
    sim = simulation_from_project(p, seed=17); sim.step(.2)
    assert any(e['type'] == 'death' for e in sim.events)
    assert any(e['type'] == 'division' for e in sim.events)
    state = sim.state[old_id]
    live = sum(state['intracellular_molecules']) + sum(state['reserve_correction_molecules'])
    dead = sum(v['residual_molecules']['nutrient'] for v in sim.dead_material.values())
    ledger = sim.ledger['nutrient']
    assert live + dead + ledger['growth'] + ledger['maintenance'] == pytest.approx(8e6 + ledger['consumed'], rel=1e-13)
    np.testing.assert_array_equal(sim.outputs[old_id]['intracellular_molecules'], state['intracellular_molecules'])
    restore_checkpoint(p, sim.checkpoint())


def test_geometry_blocked_growth_does_not_trigger_volume_division():
    p = project()
    old = next(n for n in p['graph']['nodes'] if n['module_id'] == 'metabolism.reserve_balance')
    old_id = old['id']; p['graph']['nodes'].remove(old)
    add(p, 'metabolism.shared_inventory', {'species': 'nutrient', 'initial_molecules': 1e6,
        'maintenance_molecules_s': 1., 'max_growth_per_min': 60., 'volume_yield_um3_molecule': .001}, nid=old_id, population=True)
    p['run']['channels'][old_id + '.used_molecules']['quantity'] = 'consumed_amount'
    division = add(p, 'division.volume_adder', {'added_volume_um3': .01, 'minimum_volume_um3': 0., 'daughter_fraction': .5}, population=True)
    edge(p, old_id, 'volume', division, 'volume')
    p['groups']['cells']['positions_um'][0][0] = 1.
    for node in p['graph']['nodes']:
        if node['module_id'] == 'motion.hazard_run_tumble': node['parameters']['speed_um_s']['value'] = 0.
    sim = simulation_from_project(p, seed=17)
    original_volume = sim.outputs[old_id]['volume'][0]
    sim.step(.2)
    assert sim.outputs[old_id]['blocked'][0] == 1.
    assert sim.outputs[old_id]['used_molecules'][0] == 0.
    assert sim.outputs[old_id]['volume'][0] == original_volume
    assert not any(e['type'] == 'division' and e['parent_id'] == 'cell-000' for e in sim.events)
    assert any(e['type'] == 'division' for e in sim.events)
    assert sim.outputs[division]['divide'][0] == 0.
    assert sim.outputs[old_id]['intracellular_molecules'][0] == pytest.approx(1e6 - .2)
