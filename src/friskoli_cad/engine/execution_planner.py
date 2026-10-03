"""Whole-graph planning from registered contracts, without example-ID dispatch.

The transaction coordinator owns host arrays. A CUDA implementation explicitly
performs its own round trip; this plan does not promise persistent GPU residency.
"""
from __future__ import annotations

import math
import numpy as np

from friskoli_cad.protocol import ProtocolError
from .module_api import BACKENDS, STAGES, execution_contract, validate_state_owners
from .port_semantics import port_semantics


def _bytes(port, node, sizes):
    semantic = port_semantics(port)
    if semantic['dtype'] == 'json':
        return None
    count = math.prod(node.parameters[d.split(':', 1)[1]].value if isinstance(d, str) else d
                      for d in semantic['tensor_shape'])
    scope = port['shape'].split('.')[0]
    if scope == 'cell':
        entity_set = semantic['entity_set']
        if entity_set.startswith('parameter:'):
            entity_set = node.parameters[entity_set.split(':', 1)[1]].value
        owner = node.owner_id if entity_set == 'owner.cells' else entity_set.removeprefix('population:')
        count *= sizes.get('populations', {}).get(owner, 0)
    elif scope == 'field':
        count *= sizes.get('voxels', 0)
    return int(count * np.dtype(semantic['dtype']).itemsize)


def plan_execution(plan, registry, *, backend='numpy-cpu', world_sizes=None):
    """Return the exact node backend map and deterministic stage schedule.

    A mixed CUDA request keeps modules with CPU-only implementations on CPU.
    No precision, grid, graph edge, or numerical timestep is changed here.
    Sizes are counts, not shape-based guesses about shared entity identity.
    """
    if backend not in BACKENDS:
        raise ProtocolError('planner.backend', '/execution/backend', 'Unknown execution backend')
    sizes = world_sizes or {}
    if any(type(n) is not int or n < 0 for n in [sizes.get('voxels', 0), *sizes.get('populations', {}).values()]):
        raise ProtocolError('planner.sizes', '/domain', 'World sizes must be nonnegative integer counts')
    ownership = validate_state_owners(plan, registry)
    contracts = {n.id: execution_contract(registry.get(n.module_id, n.module_version)) for n in plan.nodes}
    rank = {name: index for index, name in enumerate(STAGES)}
    for node in plan.nodes:
        for binding in node.inputs.values():
            if binding.timing == 'same_step' and rank[contracts[binding.source_node]['stage']] > rank[contracts[node.id]['stage']]:
                raise ProtocolError('planner.stage_dependency', '/graph',
                                    f'{binding.source_node} -> {node.id} crosses a stage backwards')
            source = contracts[binding.source_node]
            if (binding.timing == 'same_step'
                    and binding.source_port in source.get('settled_outputs', ())
                    and rank[source['stage']] >= rank[contracts[node.id]['stage']]):
                raise ProtocolError('planner.unsettled_output', '/graph',
                    f'{binding.source_node}.{binding.source_port} is settled after {source["stage"]}; '
                    f'{node.id} must use a later stage or explicit previous_step')
    ordered = sorted(enumerate(plan.nodes), key=lambda item: (rank[contracts[item[1].id]['stage']], item[0]))
    nodes, transfers, groups = [], [], []
    total_outputs = total_states = unknown_records = declared_workspace = 0
    for _, node in ordered:
        module = registry.get(node.module_id, node.module_version)
        contract = contracts[node.id]
        implemented = contract.get('backends', ['numpy-cpu'])
        chosen = backend if backend in implemented else 'numpy-cpu'
        if chosen not in implemented:
            raise ProtocolError('planner.implementation', '/execution/backend',
                                f'{node.id} has no implementation compatible with {backend}')
        port_bytes = {name: _bytes(port, node, sizes) for name, port in module.manifest['outputs'].items()}
        state_bytes = {name: _bytes(port, node, sizes) for name, port in module.manifest['state'].items()}
        total_outputs += sum(value or 0 for value in port_bytes.values())
        total_states += sum(value or 0 for value in state_bytes.values())
        unknown_records += sum(value is None for value in [*port_bytes.values(), *state_bytes.values()])
        workspace = contract.get('workspace_bytes', {})
        owner_cells = (sizes.get('populations', {}).get(node.owner_id, 0)
                       if node.owner_kind == 'population' else sum(sizes.get('populations', {}).values()))
        workspace_bytes = (workspace.get('fixed', 0) + workspace.get('per_voxel', 0) * sizes.get('voxels', 0)
                           + workspace.get('per_cell', 0) * owner_cells)
        declared_workspace += workspace_bytes
        item = {'node_id': node.id, 'module_id': node.module_id, 'module_version': node.module_version,
                'stage': contract['stage'], 'backend': chosen, 'owner_id': node.owner_id,
                'reads': list(contract.get('reads', ())), 'effects': list(contract.get('effects', ())),
                'settled_outputs': list(contract.get('settled_outputs', ())),
                'workspace_bytes': workspace_bytes,
                'output_bytes': port_bytes, 'state_bytes': state_bytes, 'residency': 'host_transaction',
                'dependencies': [{'node_id': b.source_node, 'port': b.source_port, 'timing': b.timing}
                                 for b in node.inputs.values()]}
        nodes.append(item)
        key = (item['stage'], chosen)
        if not groups or (groups[-1]['stage'], groups[-1]['backend']) != key:
            groups.append({'id': f'group-{len(groups)}', 'stage': key[0], 'backend': chosen, 'nodes': []})
        groups[-1]['nodes'].append(node.id)
        if chosen != 'numpy-cpu':
            transfers.append({'node_id': node.id, 'source': 'host', 'target': 'cuda',
                              'return_to': 'host', 'policy': 'implementation_round_trip',
                              'precision': 'declared_port_dtype', 'boundary': 'proposal'})
    field_species = sorted(node.parameters['species'].value for node in plan.nodes
        if 'field.owner' in getattr(registry.get(node.module_id, node.module_version), 'provides_roles', ()))
    return {'schedule_version': '0.4.0', 'execution_api_version': '0.1.0',
            'read_boundary': 'stage_snapshot_with_current_dependency_outputs', 'commit': 'atomic',
            'requested_backend': backend, 'nodes': nodes,
            'node_backends': {n['node_id']: n['backend'] for n in nodes},
            'stages': [{'stage': stage, 'nodes': [n['node_id'] for n in nodes if n['stage'] == stage]}
                       for stage in STAGES],
            'solver_groups': groups, 'transfers': transfers,
            'system_solvers': [{'id': 'finite_volume_diffusion', 'backend': backend,
                'residency': 'host', 'transfer': 'none' if backend == 'numpy-cpu' else 'round_trip_per_diffusion',
                'precision': 'float64', 'stage': 'field', 'fields': field_species}],
            'state_owners': [{'owner_id': owner, 'resource': resource, 'node_id': node}
                             for (owner, resource), node in sorted(ownership.items())],
            'memory': {'numeric_outputs_bytes': total_outputs, 'numeric_state_bytes': total_states,
                       'declared_workspace_bytes': declared_workspace,
                       'transaction_numeric_bytes_lower_bound': 2 * (total_outputs + total_states),
                       'unbounded_record_ports': unknown_records,
                       'complete_estimate': False,
                       'note': 'Task admission also budgets world buffers, cells, records, checkpoints and I/O.'}}
