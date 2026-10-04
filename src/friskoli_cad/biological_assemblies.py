"""Portable population mechanisms, with explicit environmental dependencies.

These are model assemblies, not DNA parts or experimentally calibrated strains.
Application is a pure edit of one population; shared environmental providers are
bound to existing compatible nodes and are never created or changed implicitly.
"""
from copy import deepcopy
import json
import re

from friskoli_cad.engine.profiles import registry_for_profile, registry_for_project
from friskoli_cad.project import validate_project
from friskoli_cad.protocol import ProtocolError, validate_graph, validate_run_metadata


def _fail(code, message):
    raise ProtocolError('assembly.' + code, '/assembly', message)


def _json(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        _fail('json', 'Assembly must contain finite JSON values')


def _metadata(metadata):
    required = {'id', 'name', 'version', 'kind', 'biological_role', 'provenance'}
    if not isinstance(metadata, dict) or set(metadata) != required:
        _fail('metadata', 'Metadata requires exactly id, name, version, kind, biological_role and provenance')
    for key in required - {'provenance'}:
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            _fail('metadata', f'{key} must be a non-empty string')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*', metadata['id']):
        _fail('id', 'Assembly ID must be a stable identifier')
    if not re.fullmatch(r'\d+\.\d+\.\d+', metadata['version']):
        _fail('version', 'Assembly version must be semantic major.minor.patch')
    if metadata['kind'] not in ('chassis', 'part'):
        _fail('kind', 'Assembly kind must be chassis or part')
    provenance = metadata['provenance']
    if (not isinstance(provenance, dict) or set(provenance) != {'kind', 'reference'}
            or provenance['kind'] not in ('example', 'user', 'literature', 'measurement', 'calibration')
            or not isinstance(provenance['reference'], str) or not provenance['reference'].strip()):
        _fail('provenance', 'Provenance requires a supported kind and a non-empty reference')


def extract_assembly(project, group_id, metadata):
    """Extract one complete population mechanism; no external cell links allowed."""
    _metadata(metadata)
    registry = registry_for_project(project)
    validate_project(project, registry.manifests, registry=registry)
    if group_id not in project['groups']:
        _fail('group', 'Unknown source population')
    all_nodes = {n['id']: n for n in project['graph']['nodes']}
    owned = {nid for nid, n in all_nodes.items() if n['owner'] == {'kind': 'population', 'id': group_id}}
    if not owned:
        _fail('empty', 'Population has no executable mechanism')
    manifests = {(m['id'], m['version']): m for m in registry.manifests}
    internal, requirements = [], []
    for edge in project['graph']['edges']:
        src, dst = edge['from']['node'], edge['to']['node']
        if src in owned and dst not in owned:
            _fail('outgoing_dependency', 'Population exports to another owner; independent extraction is unsupported')
        if dst not in owned:
            continue
        if src in owned:
            internal.append(deepcopy(edge))
            continue
        provider = all_nodes[src]
        if provider['owner']['kind'] == 'population':
            _fail('population_dependency', 'Cross-population mechanism imports are unsupported')
        port = manifests[(provider['module_id'], provider['module_version'])]['outputs'][edge['from']['port']]
        requirements.append({'id': edge['id'], 'source': deepcopy(edge['from']),
            'target': deepcopy(edge['to']), 'timing': edge['timing'],
            'provider': deepcopy(provider), 'port_contract': deepcopy(port)})
    nodes = [deepcopy(n) for n in project['graph']['nodes'] if n['id'] in owned]
    used = {(n['module_id'], n['module_version']) for n in nodes + [r['provider'] for r in requirements]}
    species = {n['parameters']['species']['value'] for n in nodes if 'species' in n['parameters']}
    assembly = {'assembly_version': '0.1.0', **deepcopy(metadata),
        'execution_profile': project['execution_profile'],
        'population': {'source_group_id': group_id, 'reference_cell_ids': deepcopy(project['groups'][group_id]['ids']),
            'initial_geometry': deepcopy(project['groups'][group_id].get('initial_geometry', []))},
        'graph': {'protocol_version': project['graph']['protocol_version'], 'id': metadata['id'] + '-graph',
            'nodes': nodes, 'edges': internal},
        'input_requirements': requirements,
        'species_requirements': {s: {'concentration_unit': project['species'][s]['concentration_unit']} for s in sorted(species)},
        'channels': {key: deepcopy(c) for key, c in project['run']['channels'].items() if c['node'] in owned},
        'module_manifests': [deepcopy(manifests[key]) for key in sorted(used)],
        'state_policy': 'initialize_from_node_parameters'}
    validate_assembly(assembly)
    return assembly


def validate_assembly(assembly):
    """Validate exact module contracts and the complete imported mechanism."""
    assembly = _json(assembly)
    metadata_keys = {'id', 'name', 'version', 'kind', 'biological_role', 'provenance'}
    fields = metadata_keys | {'assembly_version', 'execution_profile', 'population', 'graph',
        'input_requirements', 'species_requirements', 'channels', 'module_manifests', 'state_policy'}
    if not isinstance(assembly, dict) or set(assembly) != fields:
        _fail('schema', 'Unknown or missing assembly fields')
    _metadata({k: assembly[k] for k in metadata_keys})
    if assembly['assembly_version'] != '0.1.0' or assembly['state_policy'] != 'initialize_from_node_parameters':
        _fail('version', 'Unsupported assembly or state initialization version')
    registry = registry_for_profile(assembly['execution_profile'])
    manifests = {(m['id'], m['version']): m for m in registry.manifests}
    population = assembly['population']
    if not isinstance(population, dict) or set(population) != {'source_group_id', 'reference_cell_ids', 'initial_geometry'}:
        _fail('population', 'Invalid population geometry declaration')
    ids, geometry = population['reference_cell_ids'], population['initial_geometry']
    if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids)
            or len(set(ids)) != len(ids) or not isinstance(geometry, list) or len(geometry) != len(ids)):
        _fail('geometry', 'Reference identities and initial geometry must have equal nonzero lengths')
    # Reuse the project geometry schema; geometric domain fitting is checked on application.
    from friskoli_cad.project import _project_validator
    schema = _project_validator('0.6.0').schema['properties']['groups']['additionalProperties']['properties']['initial_geometry']
    from jsonschema import Draft202012Validator
    geometry_schema = {**schema, '$defs': _project_validator('0.6.0').schema['$defs']}
    error = next(Draft202012Validator(geometry_schema).iter_errors(geometry), None)
    if error is not None:
        _fail('geometry', error.message)
    graph = deepcopy(assembly['graph'])
    if not isinstance(graph, dict) or set(graph) != {'protocol_version', 'id', 'nodes', 'edges'} or not isinstance(graph['nodes'], list):
        _fail('graph', 'Invalid graph declaration')
    owned = {n.get('id') for n in graph['nodes'] if isinstance(n, dict)}
    for node in graph['nodes']:
        if not isinstance(node, dict) or node.get('owner') != {'kind': 'population', 'id': population['source_group_id']}:
            _fail('owner', 'All assembly nodes must belong to its source population')
    if any(e.get('from', {}).get('node') not in owned or e.get('to', {}).get('node') not in owned for e in graph['edges']):
        _fail('edge', 'Internal edges must stay inside the population')
    providers, requirement_ids = {}, set()
    for requirement in assembly['input_requirements']:
        if not isinstance(requirement, dict) or set(requirement) != {'id', 'source', 'target', 'timing', 'provider', 'port_contract'}:
            _fail('requirement', 'Invalid external input requirement')
        provider = requirement['provider']
        if not isinstance(provider, dict) or provider.get('owner', {}).get('kind') not in ('environment', 'source'):
            _fail('requirement', 'External inputs must come from an environment or source node')
        pid = provider.get('id')
        if (pid in owned or requirement['id'] in requirement_ids or requirement['source'].get('node') != pid
                or requirement['target'].get('node') not in owned):
            _fail('requirement', 'Requirement has conflicting IDs or unknown endpoints')
        requirement_ids.add(requirement['id'])
        if pid in providers and provider != providers[pid]:
            _fail('requirement', 'Conflicting declarations for one environmental provider')
        providers[pid] = provider
        manifest = manifests.get((provider.get('module_id'), provider.get('module_version')))
        if manifest is None or manifest['outputs'].get(requirement['source'].get('port')) != requirement['port_contract']:
            _fail('contract', 'Environmental port differs from its registered module contract')
        graph['edges'].append({'id': requirement['id'], 'from': requirement['source'],
            'to': requirement['target'], 'timing': requirement['timing']})
    graph['nodes'].extend(providers.values())
    validate_graph(graph, registry.manifests)
    expected = {(n['module_id'], n['module_version']) for n in graph['nodes']}
    supplied = assembly['module_manifests']
    if supplied != [manifests[key] for key in sorted(expected)]:
        _fail('manifest', 'Frozen module definitions differ from the registered implementation')
    species = {n['parameters']['species']['value'] for n in assembly['graph']['nodes'] if 'species' in n['parameters']}
    if assembly['species_requirements'] != {s: {'concentration_unit': 'uM'} for s in sorted(species)}:
        _fail('species', 'All population species must be declared explicitly with concentration units')
    validate_run_metadata({'protocol_version': '0.1.0', 'run_id': assembly['id'] + '-validation',
        'graph_id': graph['id'], 'groups': [population['source_group_id']], 'channels': assembly['channels']}, graph, registry.manifests)


def apply_assembly(project, group_id, assembly, *, bindings=None):
    """Replace one branch, preserving target identity, placement and environment.

    A chassis also supplies initial cell geometry. Homogeneous geometry can be
    replicated; heterogeneous geometry requires the same population size.
    """
    validate_assembly(assembly)
    # Check the target branch before validating the whole document.  An empty
    # population cannot be a replacement target and should produce a useful
    # assembly error even when the surrounding draft is not executable yet.
    if not isinstance(project, dict) or group_id not in project.get('groups', {}):
        _fail('target', 'Unknown target population or incompatible execution profile')
    preliminary_nodes = project.get('graph', {}).get('nodes', []) if isinstance(project.get('graph', {}), dict) else []
    preliminary_owned = {
        node.get('id') for node in preliminary_nodes
        if isinstance(node, dict) and node.get('owner') == {'kind': 'population', 'id': group_id}
    }
    if not preliminary_owned:
        _fail('empty_target', 'Target population has no executable mechanism to replace')
    registry = registry_for_project(project)
    validate_project(project, registry.manifests, registry=registry)
    if group_id not in project['groups'] or project['execution_profile'] != assembly['execution_profile']:
        _fail('target', 'Unknown target population or incompatible execution profile')
    bindings = {} if bindings is None else bindings
    requirements = {r['id']: r for r in assembly['input_requirements']}
    if not isinstance(bindings, dict) or set(bindings) - set(requirements):
        _fail('bindings', 'Bindings contain unknown input requirements')
    provider_bindings = {}
    for rid, binding in bindings.items():
        source_node = requirements[rid]['source']['node']
        previous = provider_bindings.setdefault(source_node, binding.get('node') if isinstance(binding, dict) else None)
        if previous != (binding.get('node') if isinstance(binding, dict) else None):
            _fail('bindings', f'Conflicting bindings for provider {source_node}')
    for name, spec in assembly['species_requirements'].items():
        if project['species'].get(name, {}).get('concentration_unit') != spec['concentration_unit']:
            _fail('species', f'Target environment does not declare compatible species {name}')
    target_nodes = {n['id']: n for n in project['graph']['nodes']}
    old = {nid for nid, n in target_nodes.items() if n['owner'] == {'kind': 'population', 'id': group_id}}
    if any(e['from']['node'] in old and e['to']['node'] not in old for e in project['graph']['edges']):
        _fail('outgoing_dependency', 'Target population exports to another owner and cannot be replaced independently')
    resolved = {}
    manifest_by_key = {(manifest['id'], manifest['version']): manifest for manifest in registry.manifests}
    for rid, req in requirements.items():
        endpoint = bindings.get(rid)
        if endpoint is None and req['source']['node'] in provider_bindings:
            # One provider may feed several population inputs.  Binding one
            # requirement therefore rebinds the provider for all its ports;
            # each port is still checked against its own contract below.
            endpoint = {'node': provider_bindings[req['source']['node']], 'port': req['source']['port']}
        if endpoint is None:
            endpoint = req['source']
        if not isinstance(endpoint, dict) or set(endpoint) != {'node', 'port'}:
            _fail('binding', f'Invalid binding for {rid}')
        actual = target_nodes.get(endpoint['node'])
        expected = req['provider']
        actual_manifest = (manifest_by_key.get((actual['module_id'], actual['module_version']))
                           if actual is not None else None)
        actual_port = (actual_manifest['outputs'].get(endpoint['port'])
                       if actual_manifest is not None else None)
        # A binding may point at another registered provider when its output
        # contract is identical.  This keeps the assembly portable across
        # environment object IDs while refusing silent unit/species changes.
        if (actual is None or actual['owner']['kind'] != expected['owner']['kind']
                or actual_port != req['port_contract']
                or endpoint['port'] != req['source']['port']
                or actual['parameters'].get('species', {}).get('value') != expected['parameters'].get('species', {}).get('value')):
            _fail('binding', f'Environment provider for {rid} is absent or incompatible; supply an explicit compatible binding')
        resolved[rid] = deepcopy(endpoint)
    result = deepcopy(project)
    nodes = [n for n in result['graph']['nodes'] if n['id'] not in old]
    edges = [e for e in result['graph']['edges'] if e['from']['node'] not in old and e['to']['node'] not in old]
    used = {n['id'] for n in nodes}
    def unique(base, identifiers):
        value, index = base, 1
        while value in identifiers:
            value = f'{base}_{index}'
            index += 1
        identifiers.add(value)
        return value
    mapping = {}
    for node in assembly['graph']['nodes']:
        copy = deepcopy(node)
        copy['id'] = mapping[node['id']] = unique(group_id + '_' + node['id'], used)
        copy['owner'] = {'kind': 'population', 'id': group_id}
        nodes.append(copy)
    edge_ids = {e['id'] for e in edges}
    source_edges = deepcopy(assembly['graph']['edges'])
    source_edges += [{'id': r['id'], 'from': resolved[r['id']], 'to': r['target'], 'timing': r['timing'], '_external': True}
        for r in assembly['input_requirements']]
    for edge in source_edges:
        external = edge.pop('_external', False)
        edge['id'] = unique(group_id + '_' + edge['id'], edge_ids)
        if not external:
            edge['from']['node'] = mapping[edge['from']['node']]
        edge['to']['node'] = mapping[edge['to']['node']]
        edges.append(edge)
    channels = {key: value for key, value in result['run']['channels'].items() if value['node'] not in old}
    channel_ids = set(channels)
    for key, channel in assembly['channels'].items():
        copy = deepcopy(channel)
        copy['node'], copy['group_id'] = mapping[channel['node']], group_id
        channels[unique(group_id + '_' + key, channel_ids)] = copy
    if assembly['kind'] == 'chassis':
        geometry = assembly['population']['initial_geometry']
        count = len(result['groups'][group_id]['ids'])
        if all(g == geometry[0] for g in geometry):
            geometry = [deepcopy(geometry[0]) for _ in range(count)]
        elif len(geometry) != count:
            _fail('geometry_count', 'Heterogeneous chassis geometry requires an equal target population size')
        result['groups'][group_id]['initial_geometry'] = deepcopy(geometry)
    result['graph']['nodes'], result['graph']['edges'], result['run']['channels'] = nodes, edges, channels
    validate_project(result, registry.manifests, registry=registry)
    return result
