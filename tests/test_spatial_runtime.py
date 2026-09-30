"""Spatial end-to-end behavior, including rejected-step state isolation."""
from copy import deepcopy
from importlib.resources import files
import json
import math

import numpy as np
import pytest

from friskoli_cad.engine import SimulationError
from friskoli_cad.engine.collision import capsule_gap, capsule_box_gap, capsule_wall_gap
from friskoli_cad.engine.spatial_modules import spatial_registry
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.engine.degradation import propose_contact_degradation


def half_rate_degradation(material, capsules, enzyme_copies, parameters, dt_s):
    return propose_contact_degradation(material, capsules, enzyme_copies,
        kcat_s=parameters['kcat_s'] / 2, contact_range_um=parameters['contact_range_um'], dt_s=dt_s)


def example():
    return json.loads(files('friskoli_cad').joinpath('examples', 'spatial_baseline.project.json').read_text(encoding='utf-8'))


def node(project, nid):
    return next(n for n in project['graph']['nodes'] if n['id'] == nid)


def set_parameter(project, nid, key, value):
    node(project, nid)['parameters'][key]['value'] = value


def frame_by_id(snapshot):
    return {c['id']: c for c in snapshot.cell_frame['cells']}


def stationary(project):
    for gid in project['groups']:
        set_parameter(project, gid + '_motion', 'speed_um_s', 0)
        set_parameter(project, gid + '_motion', 'tumble_rate_s', 0)


def test_objects_and_mechanisms_share_exact_registry_references():
    catalog = spatial_registry().catalog
    objects = {obj['id']: obj for obj in catalog['objects']}
    assert objects['space.obstacle_box']['initializer']['module'] == 'space.axis_aligned_obstacle@1.0.0'
    assert objects['source.attractant']['initializer']['data_modules'] == ['field.diffusive_local@1.0.0']
    assert objects['space.obstacle_box']['initializer']['adapter'] == 'environment.node@1'
    assert len(objects['space.obstacle_box']['properties']) == 6
    material = objects['material.degradable_box']
    assert material['kind'] == 'degradable_box'
    assert material['initializer']['requirements'] == [{'role': 'material.degradation',
        'default_module': 'reaction.contact_degradation@1.0.0', 'scope': 'environment'}]
    mechanism = next(m for m in catalog['entries'] if m['key'] == 'reaction.contact_degradation@1.0.0')
    assert mechanism['provides_roles'] == ['material.degradation']


def test_sources_fields_uptake_and_motion_run_together_conservatively():
    sim = simulation_from_project(example())
    initial = frame_by_id(sim.current)
    before = {s: math.fsum(sim.fields.concentrations_uM[s]) * sim.world.grid.molecules_per_uM_voxel
              + sum(x.remaining_molecules for x in sim.fields.sources if x.species == s)
              + sum(x.remaining_molecules for x in sim.materials.values() if x.species == s) for s in sim.fields.concentrations_uM}
    for _ in range(10):
        snapshot = sim.step(.1)
        for ledger in sim.ledger.values():
            assert abs(ledger.conservation_residual_molecules) <= ledger.conservation_bound_molecules
    cells = frame_by_id(snapshot)
    assert any(cells[cid]['position_um'] != initial[cid]['position_um'] for cid in cells)
    cumulative = sum(float(sim.outputs[gid + '_settle']['cumulative_uptake'].sum()) for gid in sim.world.groups)
    for species in sim.fields.concentrations_uM:
        after = math.fsum(sim.fields.concentrations_uM[species]) * sim.world.grid.molecules_per_uM_voxel
        after += sum(x.remaining_molecules for x in sim.fields.sources if x.species == species)
        after += sum(x.remaining_molecules for x in sim.materials.values() if x.species == species)
        after += cumulative if species == 'nutrient' else 0
        assert after == pytest.approx(before[species], rel=2e-13)
        assert np.min(snapshot.concentration_fields[species]) >= 0
    assert sim.outputs['degradation']['released_amount'] >= 0
    assert cumulative > 0
    mask = np.asarray(sim.fields.blocked).reshape(sim.world.grid.shape)
    assert np.all(snapshot.concentration_fields['nutrient'][mask] == 0)


def test_blocked_motion_does_not_cross_cells_obstacles_or_walls():
    project = example()
    for gid in project['groups']:
        set_parameter(project, gid + '_motion', 'tumble_rate_s', 0)
        set_parameter(project, gid + '_motion', 'speed_um_s', 20)
    # A0 and A1 have the same velocity: both would cross a solid during a long step.
    sim = simulation_from_project(project)
    before = frame_by_id(sim.current)
    after = frame_by_id(sim.step(1))
    assert any(c['channels'][c['group_id'] + '_motion.blocked'] == 1 for c in after.values())
    for capsule in sim._capsules(sim.world):
        assert capsule_wall_gap(capsule, sim.world.grid.extent_um) >= -1e-9
        assert all(capsule_box_gap(capsule, box) >= -1e-9 for box in sim.obstacles)
    assert after['a0']['position_um'] == before['a0']['position_um']
    assert not sim.current.cell_frame['events']  # blockage is not a lifecycle event


def test_head_on_cells_are_blocked_symmetrically():
    project = example()
    project['groups']['a']['positions_um'] = [[4, 4, 4], [8, 4, 4]]
    project['groups']['a']['orientation_xyzw'][1] = [0, 0, 1, 0]
    set_parameter(project, 'a_motion', 'tumble_rate_s', 0)
    set_parameter(project, 'a_motion', 'speed_um_s', 3)
    set_parameter(project, 'b_motion', 'speed_um_s', 0)
    set_parameter(project, 'b_motion', 'tumble_rate_s', 0)
    sim = simulation_from_project(project)
    sim.step(1)
    assert list(sim.outputs['a_motion']['blocked']) == [1, 1]
    assert np.array_equal(sim.world.groups['a'].positions_um, project['groups']['a']['positions_um'])
    capsules = sim._capsules(sim.world)
    assert capsule_gap(capsules[0], capsules[1]) > 0


def test_initial_overlap_is_a_precise_preflight_error():
    project = example()
    project['groups']['a']['positions_um'][1] = project['groups']['a']['positions_um'][0][:]
    with pytest.raises(SimulationError, match='initial_overlap'):
        simulation_from_project(project)
    project = example()
    project['groups']['a']['positions_um'][0] = [11, 8, 4]
    with pytest.raises(SimulationError, match='initial_overlap'):
        simulation_from_project(project)


def test_rejected_step_preserves_rng_fields_pose_signal_and_clock(monkeypatch):
    from friskoli_cad.engine import spatial_runtime
    sim = simulation_from_project(example())
    control = simulation_from_project(example())
    before = sim.current
    rng_before = sim.streams.to_dict()
    fields_before = sim.fields
    materials_before, obstacles_before = sim.materials, sim.obstacles
    real = spatial_runtime.SpatialSimulation._snapshot
    def fail(*args, **kwargs):
        raise SimulationError('test.reject', 'Rejected after candidate random draws')
    monkeypatch.setattr(spatial_runtime.SpatialSimulation, '_snapshot', fail)
    with pytest.raises(SimulationError, match='test.reject'):
        sim.step(1)
    assert sim.current is before and sim.fields is fields_before
    assert sim.materials is materials_before and sim.obstacles is obstacles_before
    assert sim.streams.to_dict() == rng_before
    assert sim.time_s == 0 and sim.frame_index == 0
    monkeypatch.setattr(spatial_runtime.SpatialSimulation, '_snapshot', real)
    assert sim.step(1).cell_frame == control.step(1).cell_frame
    assert sim.streams.to_dict() == control.streams.to_dict()


def test_keyed_randomness_is_independent_of_group_and_cell_enumeration():
    left = example(); right = deepcopy(left)
    right['groups'] = dict(reversed(list(right['groups'].items())))
    right['graph']['nodes'].reverse(); right['graph']['edges'].reverse()
    for key in ('ids', 'positions_um', 'orientation_xyzw', 'initial_geometry'):
        right['groups']['a'][key].reverse()
    a = simulation_from_project(left); b = simulation_from_project(right)
    for _ in range(5):
        assert frame_by_id(a.step(.4)) == frame_by_id(b.step(.4))
    assert a.streams.to_dict() == b.streams.to_dict()


def test_execution_seed_overrides_project_seed_without_mutation():
    project = example(); frozen = deepcopy(project)
    a = simulation_from_project(project, seed=1)
    b = simulation_from_project(project, seed=2)
    a.step(2); b.step(2)
    assert a.streams.to_dict() != b.streams.to_dict()
    assert project == frozen


def test_thin_layer_preserves_z_and_refuses_a_tilted_body():
    project = example()
    project['domain'].update(geometry='thin_layer', counts_xyz=[10,8,1])
    for group in project['groups'].values():
        for position in group['positions_um']: position[2] = 1
    for nid in ('attractant_source',):
        set_parameter(project, nid, 'center_z_um', 1)
    set_parameter(project, 'obstacle', 'upper_z_um', 2)
    set_parameter(project, 'material', 'upper_z_um', 2)
    sim = simulation_from_project(project)
    for _ in range(5): sim.step(.3)
    for group in sim.world.groups.values(): assert np.all(group.positions_um[:, 2] == 1)
    assert all(w.heading[2] == 0 for w in sim.walks.values())
    project['groups']['a']['orientation_xyzw'][0] = [0, math.sin(.1), 0, math.cos(.1)]
    with pytest.raises(SimulationError, match='planar_heading'):
        simulation_from_project(project)


def test_source_exhaustion_and_diffusion_substeps_are_normal_operations():
    project = example(); stationary(project)
    for nid in ('attractant_source',):
        set_parameter(project,nid,'initial_molecules',1)
        set_parameter(project,nid,'release_rate',10)
    set_parameter(project, 'material', 'initial_molecules', 0)
    sim = simulation_from_project(project)
    sim.step(1)
    assert all(s.remaining_molecules == 0 for s in sim.fields.sources)
    sim.step(1)
    assert all(ledger.released_molecules == 0 for ledger in sim.ledger.values())


@pytest.mark.parametrize('mutation', ['timing','source','geometry','species'])
def test_incompatible_graph_or_geometry_cannot_run(mutation):
    p = example()
    if mutation == 'timing':
        next(e for e in p['graph']['edges'] if e['to']['node']=='a_sample' and e['to']['port']=='field')['timing']='same_step'
    elif mutation == 'source':
        set_parameter(p, 'attractant_source', 'species', 'unknown')
    elif mutation == 'geometry':
        set_parameter(p,'obstacle','lower_x_um',10.1)
    else:
        p['species']['unregistered'] = deepcopy(p['species']['nutrient'])
    with pytest.raises((ProtocolError, SimulationError, ValueError)):
        simulation_from_project(p)


def contact_only(project):
    stationary(project)
    set_parameter(project, 'attractant_source', 'release_rate', 0)
    project['species']['nutrient']['initial_concentration']['value'] = 0


def test_contact_enzyme_release_becomes_soluble_before_pts_requests_can_consume_it():
    project = example(); contact_only(project)
    sim = simulation_from_project(project)
    sim.step(.1)
    assert sim.outputs['degradation']['released_amount'] == pytest.approx(4.)
    assert sim.materials['material'].remaining_molecules == pytest.approx(19996.)
    assert sum(float(sim.outputs[gid + '_settle']['accepted_amount'].sum()) for gid in sim.world.groups) == 0
    assert sum(sim.fields.concentrations_uM['nutrient']) * sim.world.grid.molecules_per_uM_voxel == pytest.approx(4.)
    assert len(sim.fields.sources) == 1  # Temporary contact sources never enter committed state.
    sim.step(.1)
    assert sum(float(sim.outputs[gid + '_settle']['accepted_amount'].sum()) for gid in sim.world.groups) > 0
    assert sim.current.object_states['material']['remaining_molecules'] == sim.materials['material'].remaining_molecules


@pytest.mark.parametrize('case', ['zero_enzyme', 'zero_kcat', 'distant'])
def test_material_requires_real_contact_and_explicit_enzyme_capacity(case):
    project = example(); contact_only(project)
    if case == 'zero_enzyme':
        for gid in project['groups']: set_parameter(project, gid + '_enzyme', 'enzyme_copies', 0)
    elif case == 'zero_kcat':
        set_parameter(project, 'degradation', 'kcat_s', 0)
    else:
        project['groups']['a']['positions_um'] = [[6, 3, 4], [8, 3, 4]]
        project['groups']['b']['positions_um'] = [[6, 13, 4]]
    sim = simulation_from_project(project)
    sim.step(1)
    assert sim.outputs['degradation']['released_amount'] == 0
    assert sim.materials['material'].remaining_molecules == 20000
    assert sum(sim.fields.concentrations_uM['nutrient']) == 0


def test_population_without_enzyme_module_does_not_degrade():
    project = example(); contact_only(project)
    project['graph']['nodes'] = [n for n in project['graph']['nodes'] if n['id'] != 'b_enzyme']
    project['run']['channels'].pop('b_enzyme.enzyme_copies')
    sim = simulation_from_project(project)
    sim.step(.1)
    assert sim.outputs['degradation']['released_amount'] == pytest.approx(2.)


def test_cell_enzyme_budget_is_shared_across_all_contacted_materials():
    project = example(); contact_only(project)
    set_parameter(project, 'degradation', 'contact_range_um', .6)
    set_parameter(project, 'b_enzyme', 'enzyme_copies', 0)
    project['groups']['a']['positions_um'][1] = [6.75, 12, 4]
    second = deepcopy(node(project, 'material'))
    second['id'] = second['owner']['id'] = 'second_material'
    second['parameters']['lower_x_um']['value'] = 6
    second['parameters']['upper_x_um']['value'] = 8
    project['graph']['nodes'].append(second)
    sim = simulation_from_project(project)
    sim.step(1)
    assert sim.outputs['degradation']['released_amount'] == pytest.approx(20.)
    assert sim.material_ledger['material'].total_accepted == pytest.approx(10.)
    assert sim.material_ledger['second_material'].total_accepted == pytest.approx(10.)


def test_exhausted_material_disappears_after_step_and_diffusion_enters_next_step():
    project = example(); contact_only(project)
    set_parameter(project, 'material', 'initial_molecules', 3)
    sim = simulation_from_project(project)
    before_mask = np.asarray(sim.fields.blocked)
    sim.step(.1)
    assert sim.materials['material'].remaining_molecules == 0
    assert all(box.obstacle_id != 'material' for box in sim.obstacles)
    opened = before_mask & ~np.asarray(sim.fields.blocked)
    assert np.any(opened)
    assert np.all(np.asarray(sim.fields.concentrations_uM['nutrient'])[opened] == 0)
    assert sim.current.object_states['material']['remaining_molecules'] == 0
    sim.step(.1)
    assert np.any(np.asarray(sim.fields.concentrations_uM['nutrient'])[opened] > 0)
    assert sim.outputs['degradation']['released_amount'] == 0


@pytest.mark.parametrize('case', ['missing_provider', 'duplicate_provider', 'no_enzyme'])
def test_material_dependency_contract_requires_one_executable_provider(case):
    project = example()
    if case == 'duplicate_provider':
        extra = deepcopy(node(project, 'degradation'))
        extra['id'] = extra['owner']['id'] = 'duplicate_provider'
        project['graph']['nodes'].append(extra)
    else:
        remove = {'degradation'} if case == 'missing_provider' else {'a_enzyme', 'b_enzyme'}
        project['graph']['nodes'] = [n for n in project['graph']['nodes'] if n['id'] not in remove]
        project['run']['channels'] = {k: v for k, v in project['run']['channels'].items() if v['node'] not in remove}
    with pytest.raises(ProtocolError, match='spatial.degradation'):
        simulation_from_project(project)


def test_multiple_spontaneous_sources_share_one_species_field_without_source_edges():
    project = example(); stationary(project)
    extra = deepcopy(node(project, 'attractant_source'))
    extra['id'] = extra['owner']['id'] = 'second_attractant'
    extra['parameters']['center_y_um']['value'] = 12
    project['graph']['nodes'].append(extra)
    sim = simulation_from_project(project)
    assert len(sim.fields.sources) == 2
    assert len(sim.fields.concentrations_uM) == 1
    assert not node(project, 'nutrient_field').get('inputs')
    sim.step(.1)
    assert sim.ledger['nutrient'].released_molecules == pytest.approx(404.)


def test_registered_alternative_role_provider_executes_and_freezes_same_schedule():
    from friskoli_cad.engine.runtime import ModuleRegistry
    from friskoli_cad.tasks.metadata import compiled_plan
    project = example(); contact_only(project)
    base = spatial_registry()
    alternate = deepcopy(base.get('reaction.contact_degradation', '1.0.0'))
    alternate.manifest['id'] = 'reaction.test_half_rate'
    alternate.propose_degradation = half_rate_degradation
    registry = ModuleRegistry([*base._modules.values(), alternate], execution_semantics='spatial-unbiased-v1')
    node(project, 'degradation')['module_id'] = alternate.manifest['id']
    sim = simulation_from_project(project, registry)
    assert compiled_plan(project, registry)['schedule'] == sim.schedule
    assert sim.schedule['degradation_nodes'] == ['degradation']
    sim.step(.1)
    assert sim.outputs['degradation']['released_amount'] == pytest.approx(2.)
    assert sim.materials['material'].remaining_molecules == pytest.approx(19998.)
    restored = type(sim).from_checkpoint(project, sim.checkpoint(), registry)
    assert restored.step(.1).cell_frame == sim.step(.1).cell_frame


def test_role_without_executable_adapter_is_rejected():
    from friskoli_cad.engine.runtime import ModuleRegistry
    project = example()
    base = spatial_registry()
    invalid = deepcopy(base.get('reaction.contact_degradation', '1.0.0'))
    invalid.propose_degradation = None
    modules = [invalid if m.manifest['id'] == 'reaction.contact_degradation' else m for m in base._modules.values()]
    registry = ModuleRegistry(modules, execution_semantics='spatial-unbiased-v1')
    with pytest.raises(ProtocolError, match='spatial.degradation'):
        simulation_from_project(project, registry)


def test_invalid_adapter_contribution_is_rejected_before_any_state_commit():
    from dataclasses import replace
    from friskoli_cad.engine.runtime import ModuleRegistry
    project = example(); contact_only(project)
    base = spatial_registry()
    invalid = deepcopy(base.get('reaction.contact_degradation', '1.0.0'))
    def corrupt(material, capsules, enzyme_copies, parameters, dt_s):
        proposal = half_rate_degradation(material, capsules, enzyme_copies, parameters, dt_s)
        return replace(proposal, released_molecules=proposal.released_molecules + 1)
    invalid.propose_degradation = corrupt
    registry = ModuleRegistry([invalid if m.manifest['id'] == 'reaction.contact_degradation' else m
                               for m in base._modules.values()], execution_semantics='spatial-unbiased-v1')
    sim = simulation_from_project(project, registry)
    old_materials, old_fields, old_world = sim.materials, sim.fields, sim.world
    with pytest.raises(SimulationError, match='Adapter release differs'):
        sim.step(.1)
    assert sim.materials is old_materials and sim.fields is old_fields and sim.world is old_world
    assert sim.time_s == 0 and sim.frame_index == 0
