"""Independent diagnostics with prerequisite-aware suppression.

This is a read-only compiler service. It never fixes scientific inputs, samples
random placements, starts a worker, or substitutes retries for missing rules.
"""
from __future__ import annotations

from collections import deque
import json
import math

from .protocol import ProtocolError
from .protocol.validation import _validator, _parameter_value, _pointer, _species


def diagnose_project(project, registry=None, *, backend='numpy-cpu', resource_check=None):
    """Collect independent errors; skip checks whose inputs are already invalid.

    resource_check(project) may raise ProtocolError or return an estimate. The
    task service remains the authority for queue, storage and dynamic resources.
    """
    checks, issues = [], []

    def record(phase, status, code, path, message, dependencies=()):
        item = {'id': f'check-{len(checks)}', 'phase': phase, 'status': status,
                'code': code, 'path': path, 'message': message, 'depends_on': list(dependencies)}
        checks.append(item)
        if status == 'fail':
            issues.append({'code': code, 'severity': 'error', 'phase': phase, 'path': path,
                           'targets': [], 'message': message})
        return item['id']

    def error(phase, exc, prefix=''):
        return record(phase, 'fail', exc.code, prefix + exc.path, str(exc))

    def finish():
        return {'diagnostic_version': '0.1.0', 'valid': not issues, 'checks': checks, 'issues': issues}

    try:
        json.dumps(project, allow_nan=False)
    except (ValueError, TypeError):
        record('structure', 'fail', 'project.number', '/', 'Project must contain finite JSON values')
        return finish()
    if not isinstance(project, dict):
        record('structure', 'fail', 'project.schema', '/', 'Project must be an object')
        return finish()
    from .project import _project_validator, validate_project
    try:
        schema = _project_validator(project.get('project_version'))
    except ProtocolError as exc:
        error('structure', exc)
        return finish()
    structure_errors = list(schema.iter_errors(project))
    for problem in structure_errors:
        record('structure', 'fail', 'project.schema', _pointer(problem.absolute_path), problem.message)
    if not structure_errors:
        record('structure', 'pass', 'project.schema', '/', 'Project structure is valid')
    try:
        if registry is None:
            from .engine.profiles import registry_for_project
            registry = registry_for_project(project)
    except ProtocolError as exc:
        dependency = error('resolve', exc)
        record('graph', 'skipped', 'diagnostic.prerequisite', '/graph', 'Resolve registered implementations first', [dependency])
        return finish()
    manifests = {(m['id'], m['version']): m for m in registry.manifests}
    graph = project.get('graph')
    if not isinstance(graph, dict):
        record('graph', 'skipped', 'diagnostic.prerequisite', '/graph', 'Graph structure is unavailable')
        return finish()
    graph_schema = _validator('graph-v0.2' if graph.get('protocol_version') == '0.2.0' else 'graph')
    graph_structure_errors = list(graph_schema.iter_errors(graph))
    for problem in graph_structure_errors:
        record('structure', 'fail', 'graph.schema', '/graph' + _pointer(problem.absolute_path), problem.message)
    # Malformed individual nodes do not prevent checking independent nodes.
    invalid_nodes, invalid_edges = set(), set()
    for problem in graph_structure_errors:
        path = list(problem.absolute_path)
        if len(path) >= 2 and path[0] in ('nodes', 'edges') and isinstance(path[1], int):
            (invalid_nodes if path[0] == 'nodes' else invalid_edges).add(path[1])
        else:
            record('graph', 'skipped', 'diagnostic.prerequisite', '/graph', 'Graph collection structure must be repaired')
            return finish()
    nodes, bad_parameters, indices = {}, set(), {}
    graph_issue_count = len(issues)
    for index, node in enumerate(graph['nodes']):
        if index in invalid_nodes:
            continue
        path = f'/graph/nodes/{index}'
        identifier = node['id']
        if identifier in nodes:
            record('resolve', 'fail', 'node.duplicate', path + '/id', 'Node ID is already present')
            continue
        manifest = manifests.get((node['module_id'], node['module_version']))
        if manifest is None:
            record('resolve', 'fail', 'module.unknown', path, 'Exact module version is unavailable')
            continue
        nodes[identifier], indices[identifier] = (node, manifest), index
        if node['owner']['kind'] != manifest['scope']:
            record('owner', 'fail', 'node.scope', path + '/owner', 'Owner kind differs from module scope')
        if node['owner']['kind'] == 'population' and node['owner']['id'] not in project.get('groups', {}):
            record('owner', 'fail', 'node.owner_missing', path + '/owner', 'Population owner is absent')
        declared, supplied = set(manifest['parameters']), set(node['parameters'])
        if declared != supplied:
            bad_parameters.add(identifier)
            record('parameters', 'fail', 'parameter.set', path + '/parameters',
                   f'Missing {sorted(declared-supplied)}; unknown {sorted(supplied-declared)}')
        for name in sorted(declared & supplied):
            try:
                _parameter_value(node, index, name, manifest['parameters'][name])
            except ProtocolError as exc:
                bad_parameters.add(identifier)
                error('parameters', exc, '/graph')
    incoming, edge_ids, adjacency = set(), set(), {name: set() for name in nodes}
    for index, edge in enumerate(graph['edges']):
        if index in invalid_edges:
            continue
        path = f'/graph/edges/{index}'
        sid, tid = edge['from']['node'], edge['to']['node']
        target = (tid, edge['to']['port'])
        # A broken connected edge is reported at the edge, not again as a missing input.
        if target in incoming:
            record('connections', 'fail', 'edge.multiple_inputs', path, 'Input has multiple providers')
        incoming.add(target)
        if edge['id'] in edge_ids:
            record('connections', 'fail', 'edge.duplicate', path + '/id', 'Duplicate edge ID')
        edge_ids.add(edge['id'])
        if sid not in nodes or tid not in nodes:
            unknown = {sid, tid} - {n.get('id') for n in graph['nodes'] if isinstance(n, dict)}
            record('connections', 'fail' if unknown else 'skipped',
                   'edge.node' if unknown else 'diagnostic.prerequisite', path,
                   'Edge references an absent node' if unknown else 'Resolve the connected module before checking this edge')
            continue
        src, sm = nodes[sid];dst, dm = nodes[tid]
        output, input_ = sm['outputs'].get(edge['from']['port']), dm['inputs'].get(edge['to']['port'])
        if output is None or input_ is None:
            record('connections', 'fail', 'edge.port', path, 'Output or input port is absent')
            continue
        if any(output[key] != input_[key] for key in ('shape', 'quantity', 'unit')):
            record('connections', 'fail', 'edge.type', path, 'Port shape, quantity or unit differs')
        elif sid in bad_parameters or tid in bad_parameters:
            record('connections', 'skipped', 'diagnostic.prerequisite', path, 'Repair parameters before resolving species/entity bindings')
        else:
            if _species(src, output) != _species(dst, input_):
                record('connections', 'fail', 'edge.species', path, 'Port species differs')
            typed = sm['protocol_version'] == '0.2.0' or dm['protocol_version'] == '0.2.0'
            if output['shape'].startswith('cell.') and src['owner'] != dst['owner'] and not (typed and (output.get('entity_set') or input_.get('entity_set'))):
                record('connections', 'fail', 'edge.population', path, 'An explicit entity mapping module is required')
            elif sm['protocol_version'] == '0.2.0' or dm['protocol_version'] == '0.2.0':
                from .engine.port_semantics import validate_connection
                try:
                    validate_connection(output, input_, src, dst, path)
                except ProtocolError as exc:
                    error('connections', exc)
        if edge['timing'] == 'same_step':
            adjacency[sid].add(tid)
            if sm['phase'] > dm['phase']:
                record('connections', 'fail', 'edge.phase', path, 'Backward phase edge requires previous_step')
        elif edge['from']['port'] not in sm['initial_outputs']:
            record('connections', 'fail', 'edge.initial', path, 'Previous-step source has no initial output')
    for identifier, (_, manifest) in nodes.items():
        for name, port in manifest['inputs'].items():
            if not port.get('optional') and (identifier, name) not in incoming:
                record('connections', 'fail', 'edge.required', f'/graph/nodes/{indices[identifier]}/inputs/{name}', 'Required input has no provider')
    indegree = dict.fromkeys(nodes, 0)
    for successors in adjacency.values():
        for node in successors:
            indegree[node] += 1
    ready = deque(node for node, degree in indegree.items() if not degree)
    visited = 0
    while ready:
        visited += 1
        for node in adjacency[ready.popleft()]:
            indegree[node] -= 1
            if not indegree[node]:
                ready.append(node)
    if visited != len(nodes):
        record('connections', 'fail', 'edge.cycle', '/graph/edges', 'Same-step dependency cycle')
    graph_valid = len(issues) == graph_issue_count and not graph_structure_errors
    if graph_valid:
        record('graph', 'pass', 'graph.valid', '/graph', 'Parameters and graph connections are valid')
    if not structure_errors:
        _geometry(project, record)
    else:
        record('geometry', 'skipped', 'diagnostic.prerequisite', '/groups', 'Repair project structure before geometric checks')
    if graph_valid:
        from .engine.compiler import compile_graph
        from .engine.execution_planner import plan_execution
        try:
            plan_execution(compile_graph(graph, registry.manifests), registry, backend=backend)
            record('planner', 'pass', 'planner.valid', '/graph', 'Ownership, stages and implementations are compatible')
        except ProtocolError as exc:
            error('planner', exc)
    else:
        record('planner', 'skipped', 'diagnostic.prerequisite', '/graph', 'Repair graph errors before planning execution')
    if not issues:
        try:
            validate_project(project, registry.manifests, registry=registry)
            record('profile', 'pass', 'profile.valid', '/', 'Profile-specific invariants are valid')
        except ProtocolError as exc:
            error('profile', exc)
    else:
        record('profile', 'skipped', 'diagnostic.prerequisite', '/', 'Dependent profile checks wait for the independent errors above')
    if resource_check is None:
        record('resources', 'unknown', 'resource.task_admission', '/', 'Task settings and current service limits are needed for resource admission')
    elif issues:
        record('resources', 'skipped', 'diagnostic.prerequisite', '/', 'Repair invalid inputs before resource estimation')
    else:
        try:
            resource_check(project)
            record('resources', 'pass', 'resource.valid', '/', 'Resource admission passed')
        except ProtocolError as exc:
            error('resources', exc)
    return finish()


def _geometry(project, record):
    lengths = [n * d for n, d in zip(project['domain']['counts_xyz'], project['domain']['spacing_um_xyz'])]
    seen = set()
    capsules, cell_paths = [], {}
    spatial = project.get('execution_profile') == 'modular-spatial-v1'
    for owner, group in project['groups'].items():
        path = '/groups/' + owner.replace('~', '~0').replace('/', '~1')
        count = len(group['ids'])
        arrays = ('positions_um', 'orientation_xyzw') + (('initial_geometry',) if 'initial_geometry' in group else ())
        if any(len(group[key]) != count for key in arrays):
            record('geometry', 'fail', 'group.length', path, 'Cell arrays differ from the number of stable IDs')
            continue
        duplicates = seen.intersection(group['ids']);seen.update(group['ids'])
        if duplicates:
            record('geometry', 'fail', 'cell.duplicate', path + '/ids', 'Cell IDs occur in another population')
        geometries = group.get('initial_geometry', [None] * count)
        for index, (position, quaternion) in enumerate(zip(group['positions_um'], group['orientation_xyzw'])):
            quaternion_valid = math.isclose(sum(v*v for v in quaternion), 1., rel_tol=0, abs_tol=1e-3)
            if not quaternion_valid:
                record('geometry', 'fail', 'cell.orientation', path + f'/orientation_xyzw/{index}', 'Orientation quaternion is not normalized')
            if any(v < 0 or v > limit for v, limit in zip(position, lengths)):
                record('geometry', 'fail', 'cell.position', path + f'/positions_um/{index}', 'Cell center is outside the physical domain')
            geometry = geometries[index]
            if geometry and geometry['length_um'] < geometry['diameter_um']:
                record('geometry', 'fail', 'cell.geometry', path + f'/initial_geometry/{index}', 'Capsule length is smaller than its diameter')
            elif geometry and spatial and quaternion_valid and group['ids'][index] not in cell_paths:
                import numpy as np
                from .engine.collision import Capsule
                from .engine.motion import heading_from_orientation
                identifier = group['ids'][index]
                heading = heading_from_orientation(np.asarray([quaternion]))[0]
                capsules.append(Capsule(identifier, position, heading, geometry['length_um'], geometry['diameter_um']))
                cell_paths[identifier] = path + f'/positions_um/{index}'
    if spatial and capsules:
        from .engine.collision import guard_motion, InitialOverlapError
        try:
            guard_motion(capsules, capsules, extent_um=lengths, geometry=project['domain']['geometry'])
            record('geometry', 'pass', 'geometry.cells', '/groups', 'Known capsule extents and mutual clearance are valid')
        except InitialOverlapError as exc:
            for contact in exc.contacts:
                record('geometry', 'fail', 'geometry.initial_contact', cell_paths[contact.cell_ids[0]],
                       f'{contact.kind}: {", ".join(map(str, contact.cell_ids))}; {contact.reason}')
