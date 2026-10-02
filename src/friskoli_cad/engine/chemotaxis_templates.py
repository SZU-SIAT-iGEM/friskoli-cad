"""Editable scientific recipes. Every mechanism expands to ordinary graph nodes."""
from copy import deepcopy
from importlib.resources import files
import json
import math


EXAMPLES = {
    'chemotaxis-pts-a': ('PTS 重建版 / PTS rebuilt · concentration memory', 'Finite nutrient with accepted-flux PTS, concentration memory and instantaneous source-A turns.'),
    'chemotaxis-pts-b': ('PTS 简化版 / PTS simplified · CheY memory', 'Finite nutrient with accepted-flux PTS, CheY-P adaptation and finite tumble dwell.'),
    'chemotaxis-mcp': ('MCP · receptor adaptation', 'Reduced MWC receptor adaptation in a separate ligand field, with an externally maintained uniform nutrient background.'),
    'chemotaxis-control': ('No chemotaxis · control', 'Same finite nutrient and uptake with explicitly constant motor bias.'),
    'chemotaxis-materials': ('Contact degradation', 'Registered solid nutrient equivalents and surface enzyme release into the same soluble PTS nutrient field.'),
    'chemotaxis-lifecycle': ('Nutrient growth and lifecycle', 'Separate uptake, growth, total-copy expression, health/death and area-adder nodes with explicit accounting.'),
}
FOUNDATIONS = {
    'foundation-control': 'chemotaxis-control',
    'foundation-pts-a': 'chemotaxis-pts-a',
    'foundation-pts-b': 'chemotaxis-pts-b',
    'foundation-mcp': 'chemotaxis-mcp',
    'foundation-materials': 'chemotaxis-materials',
}
EXAMPLES.update({key: ('Complete · ' + EXAMPLES[base][0],
    'Explicit uptake, finite maintenance reserve and delayed starvation survival, with ' + EXAMPLES[base][1])
    for key, base in FOUNDATIONS.items()})
N5_EXAMPLE_ID = 'n5-b-lifecycle-128um'
REGISTERED_EXAMPLES = {**EXAMPLES, N5_EXAMPLE_ID: ('简化版完整生命周期 / Simplified lifecycle · 128 µm central source',
    '100 PTS + 100 matched constant-bias controls; original B biological parameters, finite central soluble source, complete lifecycle and radial observations.')}


def _module_ids(example):
    if example == N5_EXAMPLE_ID:
        return [mid for mid in _module_ids('chemotaxis-lifecycle') if mid not in (
            'material.degradable_box', 'reaction.direct_bulk_hydrolysis', 'space.axis_aligned_obstacle')] + ['signal.constant_bias']
    if example in FOUNDATIONS:
        ids = _module_ids(FOUNDATIONS[example]) + ['metabolism.reserve_balance', 'life.starvation_hazard']
        if example in ('foundation-control', 'foundation-mcp'):
            ids = [mid for mid in ids if mid not in ('uptake.pts_request', 'pts.capacity_rebuilt')]
            ids.append('uptake.saturating_request')
        if example not in ('foundation-mcp', 'foundation-materials'):
            ids.append('source.finite_local')
        return ids
    ids = ['pts.capsule_area', 'field.sample_local', 'uptake.pts_request', 'uptake.local_settlement', 'motion.hazard_run_tumble']
    ids += ['pts.capacity_simplified' if example in ('chemotaxis-pts-b', 'chemotaxis-lifecycle') else 'pts.capacity_rebuilt']
    ids += ['field.diffusive_local']
    if example == 'chemotaxis-mcp':
        ids += ['signal.mcp_adaptation', 'field.ideal_local_reservoir']
    elif example == 'chemotaxis-control':
        ids += ['signal.constant_bias']
    else:
        ids += ['signal.pts_accepted', 'signal.chey_memory' if example in ('chemotaxis-pts-b', 'chemotaxis-lifecycle') else 'signal.concentration_memory']
    if example in ('chemotaxis-materials', 'chemotaxis-lifecycle'):
        ids += ['source.finite_local', 'material.degradable_box', 'reaction.direct_bulk_hydrolysis', 'space.axis_aligned_obstacle']
        ids += ['surface.enzyme_activity'] if example == 'chemotaxis-materials' else [
            'growth.nutrient_yield', 'expression.surface_copies', 'life.health_balance', 'division.area_adder']
    return ids


def template_catalog():
    result = [{'id': key, 'version': '2.0.0' if key == 'chemotaxis-mcp' else '1.0.0', 'label': label, 'description': description,
        'example_id': key, 'maturity': 'exploratory',
        'source': 'docs/science/n3-mechanisms.md; constructed demonstration parameters, not strain calibration',
        'module_keys': [mid + ('@2.0.0' if mid in ('field.diffusive_local', 'signal.mcp_adaptation') or key == N5_EXAMPLE_ID and mid == 'division.area_adder' else '@1.0.0') for mid in _module_ids(key)]}
        for key, (label, description) in REGISTERED_EXAMPLES.items()]
    for item in result:
        if item['id'] == N5_EXAMPLE_ID:
            item['source'] = 'docs/science/n5-b-lifecycle.md; source B biological parameters; constructed domain, source and paired control scenario'
            item['recommended_settings'] = {'backend': 'numpy-cupy-cuda', 'dt_s': .01, 'steps': 10000, 'frame_every_steps': 100,
                'field_stride_xyz': [8, 8, 8], 'include_final_fields': True}
    return result


def migrate_mcp_parameters(project):
    """Return an explicit v1→v2 project copy plus removed parameter records.

    Never mutate loaded projects, tasks or checkpoints. New run/version locks
    must be created from the returned project; old artifacts retain v1.
    """
    migrated = deepcopy(project)
    changes = []
    for node in migrated['graph']['nodes']:
        if node['module_id'] == 'signal.mcp_adaptation' and node['module_version'] == '1.0.0':
            removed = {key: node['parameters'].pop(key) for key in (
                'ei_total_um', 'ei_dephos_per_molecule', 'ei_rephos_s', 'ei_chea_inhibition_um')}
            node['module_version'] = '2.0.0'
            changes.append({'node_id': node['id'], 'from_version': '1.0.0', 'to_version': '2.0.0',
                            'removed_parameters': removed, 'reason': 'Unused by the MCP execution branch'})
    return migrated, changes


def make_example(example='chemotaxis-pts-a', *, registry=None):
    """Construct a small reproducible project from explicit registered defaults."""
    if example not in REGISTERED_EXAMPLES:
        raise ValueError('Unknown scientific template')
    if registry is None:
        from .chemotaxis_modules import chemotaxis_registry
        registry = chemotaxis_registry()
    if example == N5_EXAMPLE_ID:
        return make_n5_acceptance(registry=registry)
    if example in FOUNDATIONS:
        return _make_foundation(example, registry)
    manifest = {m['id']: m for m in registry.manifests if not (m['id'] == 'division.area_adder' and m['version'] != '1.0.0')}
    defaults = {m.manifest['id']: deepcopy(getattr(m, 'default_parameters', {})) for m in registry._modules.values()
                if not (m.manifest['id'] == 'division.area_adder' and m.manifest['version'] != '1.0.0')}
    # Explicit older source-derived example values supply reused modules only;
    # provenance in the resulting recipe remains clearly constructed.
    original = json.loads(files('friskoli_cad').joinpath('examples', 'spatial_baseline.project.json').read_text(encoding='utf-8'))
    for n in original['graph']['nodes']:
        defaults[n['module_id']] = {**{k: v['value'] for k, v in n['parameters'].items()}, **defaults.get(n['module_id'], {})}
    ref = {'kind': 'example', 'reference': 'N3 constructed numerical demonstration; docs/science/n3-mechanisms.md; not experimentally calibrated'}
    nodes, edges = [], []
    def node(nid, mid, params=None, *, population=True):
        m = manifest[mid]
        values = {**defaults.get(mid, {}), **(params or {})}
        missing = set(m['parameters']) - set(values)
        if missing:
            raise ValueError(f'{mid} lacks explicit template parameters: {sorted(missing)}')
        nodes.append({'id': nid, 'module_id': mid, 'module_version': m['version'],
            'owner': {'kind': 'population' if population else 'environment', 'id': 'cells' if population else nid},
            'parameters': {k: {'value': values[k], **({'unit': schema['unit']} if 'unit' in schema else {}), 'provenance': deepcopy(ref)} for k, schema in m['parameters'].items()}})
        return nid
    def edge(a, ap, b, bp, timing='same_step'):
        edges.append({'id': f'{a}_{ap}_to_{b}_{bp}', 'from': {'node': a, 'port': ap}, 'to': {'node': b, 'port': bp}, 'timing': timing})
    mcp = example == 'chemotaxis-mcp'
    b_source = example in ('chemotaxis-pts-b', 'chemotaxis-lifecycle')
    material = example in ('chemotaxis-materials', 'chemotaxis-lifecycle')
    life = example == 'chemotaxis-lifecycle'
    species = {'nutrient': {'concentration_unit': 'uM', 'initial_concentration': {'value': 1., 'unit': 'uM', 'provenance': deepcopy(ref)}}}
    node('nutrient_field', 'field.ideal_local_reservoir' if mcp else 'field.diffusive_local',
         {'species': 'nutrient', 'diffusivity_um2_s': 1., 'gradient_x_um_per_um': .008}, population=False)
    if mcp:
        species['ligand'] = deepcopy(species['nutrient'])
        node('ligand_field', 'field.diffusive_local', {'species': 'ligand', 'diffusivity_um2_s': 1., 'gradient_x_um_per_um': .008}, population=False)
    node('cell_area', 'pts.capsule_area')
    node('capacity', 'pts.capacity_simplified' if b_source else 'pts.capacity_rebuilt',
        {'g_requested': 2., 'reference_pts_copies': 100., 'ascf_area_um2': .001, 'g_cap': 3., 'reference_area_um2': 5.})
    node('nutrient_sample', 'field.sample_local', {'species': 'nutrient'})
    node('uptake_request', 'uptake.pts_request', {'species': 'nutrient', 'turnover_s': 2., 'half_saturation_um': 1.})
    node('accepted_uptake', 'uptake.local_settlement', {'species': 'nutrient', 'initial_molecules': 0.})
    edge('cell_area', 'surface_area', 'capacity', 'surface_area')
    edge('capacity', 'functional_copies', 'uptake_request', 'functional_copies')
    edge('nutrient_field', 'concentration', 'nutrient_sample', 'field', 'previous_step')
    edge('motility', 'position', 'nutrient_sample', 'position', 'previous_step')
    edge('nutrient_sample', 'concentration', 'uptake_request', 'concentration')
    edge('nutrient_field', 'concentration', 'accepted_uptake', 'field', 'previous_step')
    edge('uptake_request', 'requested_flux', 'accepted_uptake', 'requested_flux')
    if mcp:
        node('ligand_sample', 'field.sample_local', {'species': 'ligand'})
        node('motor_signal', 'signal.mcp_adaptation', {'species': 'ligand'})
        edge('ligand_field', 'concentration', 'ligand_sample', 'field', 'previous_step')
        edge('motility', 'position', 'ligand_sample', 'position', 'previous_step')
        edge('ligand_sample', 'concentration', 'motor_signal', 'concentration')
    elif example == 'chemotaxis-control':
        node('motor_signal', 'signal.constant_bias', {'bias': .5})
    else:
        node('pts_signal', 'signal.pts_accepted', {'species': 'nutrient', 'ei_dephos_per_molecule': .01,
            'chey_total_um': 10., 'motor_hill': 4., 'motor_half_um': 3.5, 'initial_ei_fraction': .6, 'initial_chey_p_um': 4.})
        edge('accepted_uptake', 'accepted_flux', 'pts_signal', 'accepted_flux')
        if b_source:
            node('motor_signal', 'signal.chey_memory', {'baseline_um': 3.5, 'initial_memory_um': 4., 'motor_hill': 8., 'motor_half_um': 3.5})
            edge('pts_signal', 'chey_p', 'motor_signal', 'chey_p')
        else:
            node('motor_signal', 'signal.concentration_memory', {'species': 'nutrient', 'gradient_strength_per_um': 20., 'initial_memory_um': .68})
            edge('pts_signal', 'motor_bias', 'motor_signal', 'motor_bias')
            edge('nutrient_sample', 'concentration', 'motor_signal', 'concentration')
    node('motility', 'motion.hazard_run_tumble', {'tumble_mode': 'dwell' if b_source else 'instant',
        'turn_kernel': 'simplified_normal' if b_source else 'rebuilt_normal' if not mcp else 'isotropic'})
    edge('motor_signal', 'motor_bias', 'motility', 'motor_bias', 'previous_step')
    if material:
        node('attractant', 'source.finite_local', {'species': 'nutrient', 'center_x_um': 160., 'center_y_um': 50., 'center_z_um': 1., 'radius_um': 5., 'initial_molecules': 10000., 'release_rate': 100.}, population=False)
        node('substrate', 'material.degradable_box', {'species': 'nutrient', 'initial_molecules': 10000., 'lower_x_um': 80., 'lower_y_um': 40., 'lower_z_um': 0., 'upper_x_um': 90., 'upper_y_um': 60., 'upper_z_um': 2.}, population=False)
        node('obstacle', 'space.axis_aligned_obstacle', {'lower_x_um': 110., 'lower_y_um': 10., 'lower_z_um': 0., 'upper_x_um': 120., 'upper_y_um': 30., 'upper_z_um': 2.}, population=False)
        node('degradation', 'reaction.direct_bulk_hydrolysis', {'kcat_s': 2., 'contact_range_um': .5}, population=False)
        if not life:
            node('surface_enzyme', 'surface.enzyme_activity', {'enzyme_copies': 50.})
    if life:
        node('growth', 'growth.nutrient_yield', {'species': 'nutrient', 'initial_molecules': 2000., 'max_growth_per_min': 1., 'volume_yield_um3_molecule': .001})
        node('surface_enzyme', 'expression.surface_copies', {'initial_copies': 50.})
        node('health', 'life.health_balance')
        node('division', 'division.area_adder', {'added_area_um2': .25})
        edge('accepted_uptake', 'accepted_amount', 'growth', 'accepted_amount')
        edge('growth', 'growth_rate', 'surface_enzyme', 'growth_rate')
        edge('health', 'health', 'surface_enzyme', 'health', 'previous_step')
        edge('cell_area', 'surface_area', 'surface_enzyme', 'surface_area')
        edge('growth', 'growth_rate', 'health', 'growth_rate')
        edge('surface_enzyme', 'enzyme_copies', 'health', 'enzyme_copies')
        edge('capacity', 'functional_copies', 'health', 'functional_copies')
        edge('cell_area', 'surface_area', 'health', 'surface_area')
        edge('growth', 'volume', 'division', 'volume')
    positions = [[60., float(15 + 10 * i), 1.] for i in range(8)]
    if material:
        positions[3] = [79., 45., 1.]
    orientations = [[0., 0., math.sin(math.pi * i / 8), math.cos(math.pi * i / 8)] for i in range(8)]
    channels = {f'{n["id"]}.{key}': {'node': n['id'], 'port': key, 'group_id': 'cells',
        **{k: value[k] for k in ('shape', 'quantity', 'unit')}}
        for n in nodes if n['owner']['kind'] == 'population' for key, value in manifest[n['module_id']]['outputs'].items()
        if value['shape'] == 'cell.scalar'}
    return {'project_version': '0.5.0', 'execution_profile': 'chemotaxis-spatial-v1', 'random_seed': 42,
        'id': example, 'domain': {'geometry': 'thin_layer', 'counts_xyz': [20, 10, 1], 'spacing_um_xyz': [10., 10., 2.]},
        'groups': {'cells': {'ids': [f'cell-{i:03d}' for i in range(8)], 'positions_um': positions,
            'orientation_xyzw': orientations, 'initial_geometry': [{'shape': 'capsule', 'length_um': 2., 'diameter_um': .8, 'provenance': deepcopy(ref)} for _ in positions]}},
        'species': species, 'controls': {}, 'graph': {'protocol_version': '0.1.0', 'id': example + '-graph', 'nodes': nodes, 'edges': edges},
        'run': {'protocol_version': '0.1.0', 'run_id': example + '-run', 'graph_id': example + '-graph', 'groups': ['cells'], 'channels': channels},
        'observation': {'id': 'positive_x_region', 'label': 'Positive X · x ≥ 100 um', 'axis': 0,
            'region_lower_um': [100., 0., 0.], 'region_upper_um': [200., 100., 2.]}}


def _make_foundation(example, registry):
    project = make_example(FOUNDATIONS[example], registry=registry)
    project['id'] = example
    project['graph']['id'] = project['run']['graph_id'] = example + '-graph'
    project['run']['run_id'] = example + '-run'
    nodes, edges = project['graph']['nodes'], project['graph']['edges']
    ref = {'kind': 'example', 'reference': 'Constructed phenomenological foundation; docs/science/foundation-composition.md; not strain calibrated'}
    def add(nid, mid, values=None, population=True):
        module = registry.get(mid, '1.0.0')
        params = {**getattr(module, 'default_parameters', {}), **(values or {})}
        node = {'id': nid, 'module_id': mid, 'module_version': '1.0.0',
            'owner': {'kind': 'population' if population else 'environment', 'id': 'cells' if population else nid},
            'parameters': {key: {'value': params[key], 'unit': schema.get('unit', '1'), 'provenance': deepcopy(ref)}
                           for key, schema in module.manifest['parameters'].items()}}
        # String identity parameters carry no numerical unit.
        for key, schema in module.manifest['parameters'].items():
            if 'unit' not in schema:
                node['parameters'][key].pop('unit')
        nodes.append(node)
        return node
    def edge(a, ap, b, bp):
        edges.append({'id': f'{a}_{ap}_to_{b}_{bp}', 'from': {'node': a, 'port': ap},
                      'to': {'node': b, 'port': bp}, 'timing': 'same_step'})
    if example in ('foundation-control', 'foundation-mcp'):
        nodes[:] = [n for n in nodes if n['id'] not in ('capacity', 'uptake_request')]
        edges[:] = [e for e in edges if e['from']['node'] != 'capacity' and e['to']['node'] not in ('capacity', 'uptake_request')]
        add('uptake_request', 'uptake.saturating_request')
        edge('nutrient_sample', 'concentration', 'uptake_request', 'concentration')
    add('nutrient_reserve', 'metabolism.reserve_balance')
    add('survival', 'life.starvation_hazard')
    edge('accepted_uptake', 'accepted_amount', 'nutrient_reserve', 'accepted_amount')
    edge('nutrient_reserve', 'unmet_duration_s', 'survival', 'unmet_duration_s')
    if example not in ('foundation-mcp', 'foundation-materials'):
        add('nutrient_source', 'source.finite_local', {'species': 'nutrient', 'center_x_um': 160., 'center_y_um': 50.,
            'center_z_um': 1., 'radius_um': 5., 'initial_molecules': 10000., 'release_rate': 100.}, population=False)
    # Full source cases start without an imposed nutrient gradient. The finite
    # source/contact material and normal diffusion build the field themselves.
    if example != 'foundation-mcp':
        project['species']['nutrient']['initial_concentration']['value'] = 0.
        project['species']['nutrient']['initial_concentration']['provenance'] = deepcopy(ref)
        field = next(n for n in nodes if n['id'] == 'nutrient_field')
        for axis in 'xyz':
            field['parameters'][f'gradient_{axis}_um_per_um']['value'] = 0.
            field['parameters'][f'gradient_{axis}_um_per_um']['provenance'] = deepcopy(ref)
        signal = next((n for n in nodes if n['id'] == 'pts_signal'), None)
        if signal:
            from dataclasses import fields
            from friskoli_cad.science import pts
            p = pts.SignalParameters(**{f.name: signal['parameters'][f.name.lower()]['value'] for f in fields(pts.SignalParameters)})
            steady = pts.steady_state_signal(0., p)
            for key, value in (('initial_ei_fraction', float(steady.ei_fraction)), ('initial_chey_p_um', float(steady.chey_p_uM))):
                signal['parameters'][key].update(value=value, provenance=deepcopy(ref))
            memory = next(n for n in nodes if n['id'] == 'motor_signal')
            memory['parameters']['initial_memory_um'].update(
                value=float(steady.chey_p_uM) if memory['module_id'] == 'signal.chey_memory' else 0., provenance=deepcopy(ref))
    manifests = {(m['id'], m['version']): m for m in registry.manifests}
    project['run']['channels'] = {f'{n["id"]}.{key}': {'node': n['id'], 'port': key, 'group_id': 'cells',
        **{k: value[k] for k in ('shape', 'quantity', 'unit')}}
        for n in nodes if n['owner']['kind'] == 'population'
        for key, value in manifests[(n['module_id'], n['module_version'])]['outputs'].items()
        if value['shape'] == 'cell.scalar'}
    return project


def make_n5_acceptance(*, registry=None):
    """Independent 128-um cube B-lifecycle case; run for 10,000 steps of .01 s.

    Original B biological parameters are kept. Grid, soluble source and matched
    mixed-population placement are new numerical scenario choices, not a replay
    of the original fiber scenario. Deliberately separate from small templates.
    """
    import numpy as np
    from .motion import orientation_after_heading
    if registry is None:
        from .chemotaxis_modules import chemotaxis_registry
        registry = chemotaxis_registry()
    project = make_example('chemotaxis-lifecycle', registry=registry)
    source = {'kind': 'example', 'reference': 'Source model simplified-v4/config.py and core/agent.py; reviewed commit 6b6180b68ac047b244aa9947fe7cc7d227f76310; science/data/n3_source_lock.json; source-model parameters, not strain measurements'}
    constructed = {'kind': 'example', 'reference': 'N5 constructed 128 um cube central soluble-source comparison; docs/science/n5-b-lifecycle.md'}
    inactive = {'kind': 'example', 'reference': 'Inactive under simplified policy; retained version-1 compatibility field; docs/science/n5-b-lifecycle.md'}
    nodes = [n for n in project['graph']['nodes'] if n['id'] not in ('substrate', 'obstacle', 'degradation')]
    project['graph']['nodes'] = nodes
    project['graph']['edges'] = [e for e in project['graph']['edges'] if e['from']['node'] not in ('substrate', 'obstacle', 'degradation') and e['to']['node'] not in ('substrate', 'obstacle', 'degradation')]
    by_id = {n['id']: n for n in nodes}
    def set_values(nid, values, provenance=source):
        for key, value in values.items():
            by_id[nid]['parameters'][key].update(value=value, provenance=deepcopy(provenance))
    set_values('nutrient_field', {'diffusivity_um2_s': 65.})
    set_values('nutrient_field', {f'gradient_{a}_um_per_um': 0. for a in 'xyz'}, constructed)
    set_values('capacity', {'g_requested': 1., 'reference_pts_copies': 500., 'g_cap': 10., 'reference_area_um2': 3.1702064327658226})
    set_values('uptake_request', {'turnover_s': 20., 'half_saturation_um': 30.})
    set_values('accepted_uptake', {'initial_molecules': 0.})
    set_values('pts_signal', {'ei_total_um': 6., 'ei_dephos_per_molecule': .001, 'ei_rephos_s': 20.,
        'ei_chea_inhibition_um': .3, 'chea_total_um': 5., 'chey_total_um': 8., 'chey_phos_per_um_s': 2.,
        'chey_dephos_s': 10., 'motor_hill': 10.3, 'motor_half_um': 3.1, 'initial_ei_fraction': 0., 'initial_chey_p_um': 0.})
    set_values('motor_signal', {'adaptation_tau_s': 3., 'baseline_um': 2.59, 'total_um': 8.,
        'motor_hill': 10.3, 'motor_half_um': 3.1, 'initial_memory_um': 0.})
    set_values('motility', {'speed_um_s': 25., 'minimum_tumble_rate_s': .1, 'maximum_tumble_rate_s': 10.,
        'tumble_mode': 'dwell', 'tumble_duration_s': .1, 'turn_kernel': 'simplified_normal'})
    set_values('attractant', {'center_x_um': 64., 'center_y_um': 64., 'center_z_um': 64., 'radius_um': 5.,
        'initial_molecules': 1e10, 'release_rate': 1e8}, constructed)
    set_values('growth', {'initial_molecules': 0., 'max_growth_per_min': .005, 'volume_yield_um3_molecule': 1.1672551805315156e-9})
    for nid in ('health', 'surface_enzyme'):
        for p in by_id[nid]['parameters'].values():
            p['provenance'] = deepcopy(inactive)
    set_values('surface_enzyme', {'policy': 'simplified', 'initial_copies': 10000., 'synthesis_copies_min': 800., 'turnover_per_min': .03,
        'enzyme_footprint_um2': 5e-5, 'available_fraction': .5})
    set_values('health', {'policy': 'simplified', 'initial_health': 1., 'repair_per_min': .01, 'burden_per_min': .006,
        'starvation_per_min': .002, 'max_growth_per_min': .005, 'death_max_per_min': 1., 'death_threshold': .05,
        'reference_pts_copies': 500., 'carrier_footprint_um2': 3e-5, 'enzyme_footprint_um2': 5e-5, 'available_fraction': .5})
    division = registry.get('division.area_adder', '2.0.0')
    by_id['division']['module_version'] = '2.0.0'
    by_id['division']['parameters'] = {key: {'value': value, 'unit': division.manifest['parameters'][key].get('unit', '1'),
        'provenance': deepcopy(source)} for key, value in division.default_parameters.items()}
    population_ids = {n['id'] for n in nodes if n['owner']['kind'] == 'population'}
    old_edges = deepcopy(project['graph']['edges'])
    for n in list(nodes):
        if n['id'] not in population_ids:
            continue
        n['owner']['id'] = 'pts'
        other = deepcopy(n)
        other['id'] = 'control_' + n['id']
        other['owner']['id'] = 'control'
        nodes.append(other)
    for e in old_edges:
        if e['to']['node'] in population_ids:
            other = deepcopy(e)
            other['id'] = 'control_' + e['id']
            for endpoint in ('from', 'to'):
                if other[endpoint]['node'] in population_ids:
                    other[endpoint]['node'] = 'control_' + other[endpoint]['node']
            project['graph']['edges'].append(other)
    bias = 1 / (1 + (3.1 / 2.59) ** 10.3)
    nodes.append({'id': 'control_fixed_bias', 'module_id': 'signal.constant_bias', 'module_version': '1.0.0',
        'owner': {'kind': 'population', 'id': 'control'}, 'parameters': {'bias': {'value': bias, 'unit': '1', 'provenance': deepcopy(constructed)}}})
    for e in project['graph']['edges']:
        if e['to'] == {'node': 'control_motility', 'port': 'motor_bias'}:
            e['from'] = {'node': 'control_fixed_bias', 'port': 'motor_bias'}
            e['id'] = 'control_fixed_bias_to_motility'
    # Central inversion pairs match radial position AND radial heading exactly.
    rng = np.random.Generator(np.random.PCG64(20261002))
    positions, headings, occupied = [], [], []
    while len(positions) < 100:
        p = rng.uniform(1., 127., 3)
        twin = 128. - p
        if np.linalg.norm(p - twin) < 2 or any(np.linalg.norm(candidate - old) < 2 for candidate in (p, twin) for old in occupied):
            continue
        h = rng.normal(size=3)
        h /= np.linalg.norm(h)
        positions.append(p)
        headings.append(h)
        occupied.extend((p, twin))
    positions, headings = np.asarray(positions), np.asarray(headings)
    identity = np.tile([0., 0., 0., 1.], (100, 1))
    geometry = {'shape': 'capsule', 'length_um': 1.2613850609910124, 'diameter_um': .8, 'provenance': deepcopy(source)}
    project['groups'] = {gid: {'ids': [f'{gid}-{i:03d}' for i in range(100)],
        'positions_um': (positions if gid == 'pts' else 128. - positions).tolist(),
        'orientation_xyzw': orientation_after_heading(identity, headings if gid == 'pts' else -headings).tolist(),
        'initial_geometry': [deepcopy(geometry) for _ in range(100)]} for gid in ('pts', 'control')}
    project['domain'] = {'geometry': 'volume', 'counts_xyz': [256, 256, 256], 'spacing_um_xyz': [.5, .5, .5]}
    project['species']['nutrient']['initial_concentration'].update(value=0., provenance=deepcopy(constructed))
    project['id'] = 'n5-b-lifecycle-128um'
    project['graph']['id'] = project['run']['graph_id'] = project['id'] + '-graph'
    project['run']['run_id'] = project['id'] + '-run'
    project['run']['groups'] = ['pts', 'control']
    manifests = {(m['id'], m['version']): m for m in registry.manifests}
    project['run']['channels'] = {f'{n["id"]}.{key}': {'node': n['id'], 'port': key, 'group_id': n['owner']['id'],
        **{k: value[k] for k in ('shape', 'quantity', 'unit')}}
        for n in nodes if n['owner']['kind'] == 'population'
        for key, value in manifests[(n['module_id'], n['module_version'])]['outputs'].items() if value['shape'] == 'cell.scalar'}
    project['observation'] = {'id': 'central_box_legacy', 'label': 'Central 40 um box (legacy box metric; not spherical)',
        'axis': 0, 'region_lower_um': [44., 44., 44.], 'region_upper_um': [84., 84., 84.],
        'radial_center_um': [64., 64., 64.], 'radial_radii_um': [10., 20., 40.]}
    return project
