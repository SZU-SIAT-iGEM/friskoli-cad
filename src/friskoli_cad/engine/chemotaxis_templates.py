"""Editable scientific recipes. Every mechanism expands to ordinary graph nodes."""
from copy import deepcopy
from importlib.resources import files
import json
import math


EXAMPLES = {
    'chemotaxis-pts-a': ('PTS A · concentration memory', 'Finite nutrient with accepted-flux PTS, concentration memory and instantaneous source-A turns.'),
    'chemotaxis-pts-b': ('PTS B · CheY memory', 'Finite nutrient with accepted-flux PTS, CheY-P adaptation and finite tumble dwell.'),
    'chemotaxis-mcp': ('MCP · receptor adaptation', 'Reduced MWC receptor adaptation in a separate ligand field, with an externally maintained uniform nutrient background.'),
    'chemotaxis-control': ('No chemotaxis · control', 'Same finite nutrient and uptake with explicitly constant motor bias.'),
    'chemotaxis-materials': ('Contact degradation', 'Registered solid nutrient equivalents and surface enzyme release into the same soluble PTS nutrient field.'),
    'chemotaxis-lifecycle': ('Nutrient growth and lifecycle', 'Separate uptake, growth, total-copy expression, health/death and area-adder nodes with explicit accounting.'),
}


def _module_ids(example):
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
    return [{'id': key, 'version': '1.0.0', 'label': label, 'description': description,
        'example_id': key, 'maturity': 'exploratory',
        'source': 'docs/science/n3-mechanisms.md; constructed demonstration parameters, not strain calibration',
        'module_keys': [mid + ('@2.0.0' if mid == 'field.diffusive_local' else '@1.0.0') for mid in _module_ids(key)]}
        for key, (label, description) in EXAMPLES.items()]


def make_example(example='chemotaxis-pts-a', *, registry=None):
    """Construct a small reproducible project from explicit registered defaults."""
    if example not in EXAMPLES:
        raise ValueError('Unknown scientific template')
    if registry is None:
        from .chemotaxis_modules import chemotaxis_registry
        registry = chemotaxis_registry()
    manifest = {m['id']: m for m in registry.manifests}
    defaults = {m.manifest['id']: deepcopy(getattr(m, 'default_parameters', {})) for m in registry._modules.values()}
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
