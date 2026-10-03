"""Role-based scientific transactions for registered module API 0.1.

Modules calculate proposals. This system owns shared settlement, geometry
constraints, lifecycle identity, and one atomic commit. No module ID dispatch.
"""
from copy import deepcopy
from dataclasses import replace
import math
from types import MappingProxyType
import numpy as np

from friskoli_cad.protocol import ProtocolError, FrameSequenceValidator
from .compiler import compile_graph
from .module_api import STAGES, StepContext, execute_module, execution_contract, validate_state_owners, freeze, thaw
from .runtime import World, CellGroup, CapsuleGeometry, Snapshot, SimulationError
from .random_streams import RandomStreams
from .motion import heading_from_orientation, orientation_after_heading
from .local_fields import FieldSpecies, make_local_field_state, propose_local_field_step, _stored_values
from .collision import Capsule, BoxObstacle, guard_motion, contested_mask

PROFILE = 'modular-spatial-v1'

# System effect handlers have explicit settlement boundaries. Accepting an
# effect in a later stage would otherwise leave it unapplied or silently delayed.
EFFECT_STAGES = {
    'material.release': ('field',),
    'observer.metrics': ('observation',),
    'inventory.consumption': ('physiology',),
    'field.initial': ('prepare', 'field'),
    'field.diffusivity': ('prepare', 'field'),
    'field.transport': ('field',),
    'field.delta': ('field',),
    'field.uptake': ('field',),
    'geometry.obstacle': ('prepare',),
    'geometry.residues': ('field',),
    'geometry.growth': ('physiology',),
    'geometry.elongation': ('physiology',),
    'motion.paths': ('physiology',),
    'motion.scale': ('prepare', 'field', 'physiology'),
    'motion.displacement': ('prepare', 'field', 'physiology'),
    'lifecycle.death': ('physiology', 'lifecycle'),
    'lifecycle.division': ('lifecycle',),
}


def validate_modular_project(project, manifests, registry=None):
    if project.get('execution_profile') != PROFILE or project.get('controls'):
        raise ProtocolError('modular.profile', '/execution_profile', 'Use explicit registered schedule nodes for this profile')
    if registry is None:
        from .science_extensions import modular_registry
        registry = modular_registry()
    limit = project.get('system_limits', {}).get('max_cells', 256)
    if type(limit) is not int or limit < 1 or sum(len(group['ids']) for group in project['groups'].values()) > limit:
        raise ProtocolError('resource.cell_limit', '/system_limits/max_cells', 'Initial population exceeds the positive cell resource limit')
    plan = compile_graph(project['graph'], manifests)
    validate_state_owners(plan, registry)
    fields, motions, inventories, growths, surface_owners = {}, {}, {}, {}, {}
    catalyst_owners, material_consumer = {}, None
    readonly_project = freeze(project)
    for node in plan.nodes:
        module = registry.get(node.module_id, node.module_version)
        contract = execution_contract(module)
        preflight = getattr(module, 'project_preflight', None)
        if preflight is not None:
            try: preflight(freeze({k: v.value for k, v in node.parameters.items()}), readonly_project, node.owner_id)
            except (KeyError, TypeError, ValueError) as error:
                raise ProtocolError('modular.parameters', f'/graph/nodes/{node.id}/parameters', str(error)) from error
        for name in getattr(module, 'catalytic_inputs', ()):
            binding = node.inputs.get(name)
            if binding:
                source = plan.by_id[binding.source_node]
                provider = registry.get(source.module_id, source.module_version)
                resource = getattr(provider, 'catalyst_resource', None)
                key = tuple(source.owner_id if value == '$owner' else value for value in resource) if resource else ('output', binding.source_node, binding.source_port)
                if key in catalyst_owners: raise ProtocolError('modular.catalyst', '/graph', 'A catalytic capacity output cannot be independently spent by multiple reactions')
                catalyst_owners[key] = node.id
        if 'material.release' in contract['effects']:
            if material_consumer is not None: raise ProtocolError('modular.catalyst', '/graph', 'Only one shared material catalytic consumer is supported')
            material_consumer = node.id
        roles = getattr(module, 'provides_roles', ())
        readable = {'grid_shape_zyx', 'spacing_xyz', 'molecules_per_uM_voxel', 'blocked', 'fields', 'geometry', 'positions_um', 'headings', 'length_um', 'diameter_um', 'events', 'dead_material', 'metrics', 'materials', 'surface_enzymes', 'all_cells'}
        if not set(contract['reads']) <= readable:
            raise ProtocolError('modular.reads', '/graph', 'Unknown world resource in execution contract')
        for output, resource in getattr(module, 'geometry_outputs', {}).items():
            spec = module.manifest['outputs'].get(output, {})
            if node.owner_kind != 'population' or resource not in ('length_um', 'diameter_um') or spec.get('shape') != 'cell.scalar' or spec.get('unit') != 'um':
                raise ProtocolError('modular.geometry_binding', '/graph', 'Geometry bindings require declared population scalar length outputs')
        if getattr(module, 'refresh_after_lifecycle', False) and (module.manifest['state'] or module.manifest['inputs'] or contract['effects'] or node.owner_kind != 'population'):
            raise ProtocolError('modular.refresh', '/graph', 'Lifecycle refresh requires a stateless population reader without inputs or effects')
        if 'population_id' in node.parameters and node.parameters['population_id'].value not in project['groups']:
            raise ProtocolError('modular.population', '/graph', 'Unknown population parameter')
        if 'species' in node.parameters and node.parameters['species'].value not in project['species']:
            raise ProtocolError('modular.species', '/graph', 'Unknown module species')
        if not set(contract['effects']) <= set(EFFECT_STAGES):
            raise ProtocolError('modular.effect', '/graph', 'No system handler exists for a declared effect')
        for kind in contract['effects']:
            if contract['stage'] not in EFFECT_STAGES[kind]:
                raise ProtocolError('modular.effect_stage', f'/graph/nodes/{node.id}',
                    f'{kind} requires stage {EFFECT_STAGES[kind]}, received {contract["stage"]}')
        for spec in module.manifest['state'].values():
            if spec.get('on_death', 'discard') != 'discard':
                raise ProtocolError('modular.lifecycle', '/graph', 'State retain/release requires an explicit supported lifecycle adapter')
            if spec.get('on_division') == 'reset' and 'initial_value' not in spec:
                raise ProtocolError('modular.lifecycle', '/graph', 'Reset state requires explicit initial_value')
        if node.owner_kind == 'population' and node.owner_id not in project['groups']:
            raise ProtocolError('modular.owner', '/graph', 'Unknown population owner')
        for role, table, key in [('field.owner', fields, node.parameters.get('species')),
                                  ('motion.owner', motions, node.owner_id), ('inventory.owner', inventories, node.owner_id), ('growth.owner', growths, node.owner_id), ('enzyme.surface', surface_owners, node.owner_id)]:
            if role not in roles: continue
            key = key.value if hasattr(key, 'value') else key
            if key in table: raise ProtocolError('modular.owner', '/graph', f'Duplicate {role} for {key}')
            table[key] = node.id
        for binding in node.inputs.values():
            source = plan.by_id[binding.source_node]
            source_stage = execution_contract(registry.get(source.module_id, source.module_version))['stage']
            order = STAGES
            if binding.timing == 'same_step' and order.index(source_stage) > order.index(contract['stage']):
                raise ProtocolError('modular.stage', '/graph', 'A same-step input cannot read a later execution stage')
    for node in plan.nodes:
        consumer = registry.get(node.module_id, node.module_version)
        if getattr(consumer, 'consumes_catalyst_role', None) == 'enzyme.surface':
            for gid, provider_id in surface_owners.items():
                provider_node = plan.by_id[provider_id]
                provider = registry.get(provider_node.module_id, provider_node.module_version)
                key = tuple(gid if value == '$owner' else value for value in provider.catalyst_resource)
                if key in catalyst_owners:
                    raise ProtocolError('modular.catalyst', '/graph', 'Surface catalyst pool has more than one independent consuming reaction')
                catalyst_owners[key] = node.id
    known_targets = set(project['species']) | set(project['groups']) | {node.owner_id for node in plan.nodes}
    for node in plan.nodes:
        contract = execution_contract(registry.get(node.module_id, node.module_version))
        for resource, targets in contract.get('write_targets', {}).items():
            for target in targets:
                resolved = node.owner_id if target == '$owner' else node.parameters.get(target[1:]).value if target.startswith('$') and target[1:] in node.parameters else target
                if resolved not in known_targets:
                    raise ProtocolError('modular.write_target', '/graph', 'Unknown target in write contract')
        for kind, targets in contract.get('effect_targets', {}).items():
            for target in targets:
                resolved = node.owner_id if target == '$owner' else node.parameters.get(target[1:]).value if target.startswith('$') and target[1:] in node.parameters else target
                if resolved not in known_targets:
                    raise ProtocolError('modular.effect_target', '/graph', 'Unknown target in effect contract')
                if kind.startswith('field.') and resolved not in fields:
                    raise ProtocolError('modular.effect_target', '/graph', 'Field effect requires an active field owner')
                if kind.startswith(('motion.', 'lifecycle.')) and resolved not in project['groups']:
                    raise ProtocolError('modular.effect_target', '/graph', 'Cell effect requires a known population')
    if set(motions) != set(project['groups']):
        raise ProtocolError('modular.motion_owner', '/graph', 'Each population requires a registered motion owner')
    if not set(fields) <= set(project['species']):
        raise ProtocolError('modular.species', '/graph', 'Field species must be declared')
    return plan


def snapshot_object_declarations(project, registry=None):
    if registry is None:
        from .science_extensions import modular_registry
        registry = modular_registry()
    result = {}
    for node in project['graph']['nodes']:
        module = registry.get(node['module_id'], node['module_version'])
        if hasattr(module, 'snapshot_object_type'):
            result[node['owner']['id']] = {'object_type': module.snapshot_object_type, 'outputs': dict(module.snapshot_outputs), 'node_id': node['id']}
    return result


class ModularSimulation:
    def __init__(self, world, project, registry, *, seed=None, field_backend='numpy-cpu'):
        from .field_backend import validate_backend
        validate_backend(field_backend)
        self.plan = validate_modular_project(project, registry.manifests, registry)
        self.project, self.run, self.registry = deepcopy(project), deepcopy(project['run']), registry
        self.world, self.field_backend = world, field_backend
        self.seed = project['random_seed'] if seed is None else seed
        self.streams = RandomStreams(self.seed)
        self.time_s = self.last_dt_s = 0.; self.frame_index = self.next_cell_index = 0
        self.outputs, self.state, self.ledger, self.metrics, self.dead_material = {}, {}, {}, {}, {}
        self.obstacles, self.events, self.geometry_state = (), [], {}
        self.field_owners = {n.parameters['species'].value: n.id for n in self.plan.nodes
            if 'field.owner' in getattr(registry.get(n.module_id, n.module_version), 'provides_roles', ())}
        self.fields = make_local_field_state(world.grid, [FieldSpecies(species, project['species'][species]['initial_concentration']['value'], 0.) for species in self.field_owners], backend=field_backend,
            max_voxels=world.grid.voxel_count, max_values=world.grid.voxel_count * max(1, len(self.field_owners)))
        self.initial_amounts = {}
        from .execution_planner import plan_execution
        self.execution_plan = plan_execution(self.plan, registry, backend=field_backend,
            world_sizes={'voxels': world.grid.voxel_count, 'populations': {gid: len(group.ids) for gid, group in world.groups.items()}})
        self.execution_plan['system_solvers'] = [{'id': 'finite_volume_diffusion', 'backend': field_backend,
            'residency': 'host', 'transfer': 'none' if field_backend == 'numpy-cpu' else 'round_trip_per_diffusion',
            'precision': 'float64', 'stage': 'field', 'fields': sorted(self.field_owners)}]
        self.schedule = {**self.execution_plan, 'stages': {stage['stage']: stage['nodes'] for stage in self.execution_plan['stages']}}
        self.frame_validator = FrameSequenceValidator(self.run)
        self._transaction(0., initialize=True)
        self.initial_amounts = {s: float(np.sum(v)) * world.grid.molecules_per_uM_voxel - sum(self.ledger.get(s, {}).get(k, 0.) for k in ('external_net', 'internal_net', 'reaction_net'))
                               for s, v in self.fields.concentrations_uM.items()}

    def _resources(self, world, fields, node, events, dead, metrics, outputs=None, state=None):
        group = world.groups.get(node.owner_id)
        outputs = self.outputs if outputs is None else outputs; state = self.state if state is None else state
        materials, enzymes = {}, {}
        for owner in self.plan.nodes:
            roles = getattr(self.registry.get(owner.module_id, owner.module_version), 'provides_roles', ())
            if 'material.owner' in roles:
                p = {k: v.value for k, v in owner.parameters.items()}
                materials[owner.id] = {'species': p['species'], 'initial_molecules': p['initial_molecules'],
                    'inventory': state.get(owner.id, {}).get('inventory', p['initial_molecules']),
                    'lower_um': [p['lower_' + a + '_um'] for a in 'xyz'], 'upper_um': [p['upper_' + a + '_um'] for a in 'xyz']}
            if 'enzyme.surface' in roles and owner.id in outputs:
                enzymes.update(zip(world.groups[owner.owner_id].ids, outputs[owner.id]['enzyme_copies'], strict=True))
        return {'grid_shape_zyx': world.grid.shape, 'spacing_xyz': (world.grid.dx_um, world.grid.dy_um, world.grid.dz_um),
            'molecules_per_uM_voxel': world.grid.molecules_per_uM_voxel, 'blocked': fields.blocked,
            'fields': fields.concentrations_uM, 'geometry': world.grid.geometry,
            'positions_um': np.empty((0, 3)) if group is None else group.positions_um,
            'headings': np.empty((0, 3)) if group is None else heading_from_orientation(group.orientation_xyzw),
            'length_um': np.asarray([] if group is None else [g.length_um for g in group.geometry]),
            'diameter_um': np.asarray([] if group is None else [g.diameter_um for g in group.geometry]),
            'events': events, 'dead_material': dead, 'metrics': metrics, 'materials': materials, 'surface_enzymes': enzymes,
            'all_cells': {cid: {'group_id': gid, 'position_um': group.positions_um[i], 'length_um': group.geometry[i].length_um,
                'diameter_um': group.geometry[i].diameter_um, 'heading': heading_from_orientation(group.orientation_xyzw)[i]}
                for gid, group in world.groups.items() for i, cid in enumerate(group.ids)}}

    def _seed_outputs(self, world, fields):
        result = thaw(self.outputs)
        for species, nid in self.field_owners.items():
            result[nid] = {'concentration': np.asarray(fields.concentrations_uM[species]).reshape(world.grid.shape)}
        return result

    def _transaction(self, dt, initialize=False):
        world, fields = self.world, self.fields
        outputs, state = self._seed_outputs(world, fields), thaw(self.state)
        previous = self._seed_outputs(world, fields)
        streams = self.streams.clone()
        ledger, metrics, dead = deepcopy(self.ledger), deepcopy(self.metrics), deepcopy(self.dead_material)
        events, obstacle_specs = [], deepcopy(self.geometry_state)
        next_cell_index = self.next_cell_index
        obstacles = self.obstacles
        all_effects = []
        for stage in STAGES:
            effects = []
            for nid in self.schedule['stages'][stage]:
                node = self.plan.by_id[nid]; module = self.registry.get(node.module_id, node.module_version)
                contract = execution_contract(module)
                available = self._resources(world, fields, node, events, dead, metrics, outputs, state)
                context = StepContext(self.time_s, dt, self.frame_index, node.owner_id,
                    world.groups[node.owner_id].ids if node.owner_kind == 'population' else (),
                    {key: available[key] for key in contract['reads']},
                    {name: (previous if binding.timing == 'previous_step' and not initialize else outputs)[binding.source_node][binding.source_port]
                     for name, binding in node.inputs.items()
                     if binding.source_node in (previous if binding.timing == 'previous_step' and not initialize else outputs)},
                    {k: v.value for k, v in node.parameters.items()},
                    state.get(nid, {}), streams, backend=self.execution_plan['node_backends'][nid], node_id=nid,
                    entity_sets={'population:' + gid: group.ids for gid, group in world.groups.items()})
                try:
                    proposal = execute_module(module, context, initialize=initialize)
                except (ProtocolError, ValueError) as error:
                    raise SimulationError('modular.proposal', f'{nid}: {error}', f'/graph/nodes/{nid}') from error
                outputs[nid], state[nid] = thaw(proposal.outputs), thaw(proposal.state)
                effects.extend((nid, item) for item in proposal.effects)
            if any(effect.kind.startswith('field.') or effect.kind == 'material.release'
                   for _, effect in effects):
                fields = self._field_effects(fields, effects, outputs, state, dt, ledger, initialize)
            for nid, effect in effects:
                if effect.kind == 'material.release':
                    for transfer in effect.value:
                        owner = self.plan.by_id[transfer['material_node']].owner_id
                        if state[transfer['material_node']]['inventory'] == 0 and owner in obstacle_specs:
                            obstacle_specs[owner]['active'] = False
            if initialize or any(effect.kind in ('geometry.obstacle', 'geometry.residues', 'material.release')
                                 for _, effect in effects):
                fields, obstacles, obstacle_specs = self._geometry_effects(fields, effects, obstacle_specs, initialize=initialize)
            if stage == 'field' and dt:
                proposal = propose_local_field_step(fields, dt)
                fields = proposal.after
            if stage == 'physiology':
                world = self._growth_effects(world, effects, outputs, state, obstacles, ledger, dt)
                world, _ = self._motion_effects(world, all_effects + effects, outputs, state, obstacles)
            if stage == 'lifecycle':
                lifecycle_effects = all_effects + effects
                world, events, dead = self._lifecycle_effects(world, lifecycle_effects, outputs, state, dead, self.time_s + dt)
                world, division_events, division_details, next_cell_index = self._division_effects(world, lifecycle_effects, outputs, state, obstacles, self.time_s + dt, next_cell_index)
                events.extend(division_events)
                for refresh_node in self.plan.nodes:
                    refresh_module = self.registry.get(refresh_node.module_id, refresh_node.module_version)
                    bindings = getattr(refresh_module, 'geometry_outputs', {})
                    if bindings:
                        geometry = self._resources(world, fields, refresh_node, events, dead, metrics, outputs, state)
                        for output, resource in bindings.items(): outputs[refresh_node.id][output] = np.asarray(geometry[resource]).copy()
                    if not getattr(refresh_module, 'refresh_after_lifecycle', False): continue
                    contract = execution_contract(refresh_module)
                    if refresh_module.manifest['state'] or refresh_module.manifest['inputs'] or contract['effects']:
                        raise SimulationError('modular.refresh', 'Geometry refresh requires a stateless reader with no inputs or effects')
                    available = self._resources(world, fields, refresh_node, events, dead, metrics, outputs, state)
                    context = StepContext(self.time_s + dt, 0., self.frame_index, refresh_node.owner_id,
                        world.groups[refresh_node.owner_id].ids, {k: available[k] for k in contract['reads']},
                        parameters={k: v.value for k, v in refresh_node.parameters.items()}, rng=streams,
                        node_id=refresh_node.id, entity_sets={'population:' + gid: group.ids for gid, group in world.groups.items()})
                    outputs[refresh_node.id] = thaw(execute_module(refresh_module, context).outputs)
                if division_details: metrics['system.division_geometry'] = division_details
            for nid, effect in effects:
                if effect.kind == 'observer.metrics': metrics[nid] = thaw(effect.value)
                elif effect.kind == 'inventory.consumption':
                    item = ledger.setdefault(effect.value['species'], {'external_net': 0., 'consumed': 0., 'maintenance': 0., 'growth': 0.})
                    item['maintenance'] += effect.value['maintenance']; item['growth'] += effect.value['growth']
            all_effects.extend(effects)
        for species, nid in self.field_owners.items():
            outputs[nid]['concentration'] = np.asarray(fields.concentrations_uM[species]).reshape(world.grid.shape)
            state[nid]['concentration'] = outputs[nid]['concentration']
        # Every initialized node is explicit; no optional or hidden execution.
        if set(state) != set(self.plan.by_id): raise SimulationError('modular.state', 'Uninitialized registered module')
        self._validate_values(world, fields, outputs, state)
        if initialize:
            caps = self._capsules(world)
            guard_motion(caps, caps, extent_um=world.grid.extent_um, obstacles=obstacles, geometry=world.grid.geometry)
        snapshot = self._snapshot(world, fields, outputs, self.time_s + dt, self.frame_index + (not initialize), events, metrics)
        validator = deepcopy(self.frame_validator); validator.accept(snapshot.cell_frame, validate_schema=initialize)
        self.world, self.fields, self.outputs, self.state = world, fields, freeze(outputs), freeze(state)
        self.streams, self.ledger, self.metrics, self.dead_material = streams, ledger, metrics, dead
        self.obstacles, self.geometry_state = obstacles, obstacle_specs
        self.next_cell_index = next_cell_index
        self.current, self.frame_validator, self.events = snapshot, validator, events
        self.time_s += dt; self.frame_index += not initialize; self.last_dt_s = dt
        return snapshot

    def _field_effects(self, fields, effects, outputs, state, dt, ledger, initial):
        values = {s: np.asarray(v).reshape(fields.grid.shape).copy() for s, v in fields.concentrations_uM.items()}
        diffusivity = dict(fields.diffusivities_um2_s)
        requested = {s: [] for s in values}
        deltas = {s: [] for s in values}
        transports = set()
        initials = set()
        material_transfers = []
        for nid, effect in effects:
            if effect.kind != 'material.release': continue
            for transfer in effect.value:
                mid = transfer['material_node']; amount = float(transfer['amount'])
                if mid not in state or 'material.owner' not in getattr(self.registry.get(self.plan.by_id[mid].module_id, self.plan.by_id[mid].module_version), 'provides_roles', ()):
                    raise SimulationError('modular.material_owner', 'Unknown material inventory owner')
                if amount < 0 or amount > state[mid]['inventory']: raise SimulationError('modular.material_budget', 'Material proposal exceeds shared stock')
                state[mid]['inventory'] -= amount; outputs[mid]['inventory'] = state[mid]['inventory']
                positions = np.asarray(transfer['positions_um']).reshape(-1, 3)
                delta = np.zeros(fields.grid.shape)
                np.add.at(delta.reshape(-1), fields.grid.flat_indices(positions), amount / len(positions))
                material_transfers.append((nid, transfer['species'], delta))
        for nid, effect in effects:
            if not effect.kind.startswith('field.'): continue
            species = effect.target
            if species not in values: raise SimulationError('modular.field', 'Effect targets an inactive field')
            if effect.kind == 'field.initial':
                if not initial or species in initials: raise SimulationError('modular.initial', 'Initial field has one owner and runs once')
                values[species] = np.asarray(effect.value, float).copy(); initials.add(species)
            elif effect.kind == 'field.diffusivity': diffusivity[species] = float(effect.value)
            elif effect.kind == 'field.transport':
                if species in transports: raise SimulationError('modular.transport_owner', 'One transport provider per species')
                delta = np.asarray(effect.value, float)
                scale = fields.grid.molecules_per_uM_voxel
                if delta.shape != values[species].shape or not math.isclose(float(delta.sum()), 0., abs_tol=1e-10 * max(1., float(np.abs(delta).sum()))):
                    raise SimulationError('modular.transport_balance', 'Transport must conserve total amount')
                after = values[species] + delta / scale
                if np.any(after < -1e-12): raise SimulationError('modular.transport_inventory', 'Transport exceeds donor inventory')
                values[species] = np.maximum(after, 0.); transports.add(species)
            elif effect.kind == 'field.delta': deltas[species].append((nid, np.asarray(effect.value, float)))
            elif effect.kind == 'field.uptake': requested[species].append((nid, thaw(effect.value)))
            else: raise SimulationError('modular.effect', 'Unsupported field effect')
        unit = fields.grid.molecules_per_uM_voxel
        for nid, species, delta in material_transfers:
            values[species] += delta / unit
            item = ledger.setdefault(species, {'external_net': 0., 'consumed': 0., 'maintenance': 0., 'growth': 0.})
            item['internal_net'] = item.get('internal_net', 0.) + float(delta.sum())
        for species, before in values.items():
            item = ledger.setdefault(species, {'external_net': 0., 'consumed': 0., 'maintenance': 0., 'growth': 0.})
            positive = np.zeros(before.shape); negative = np.zeros(before.shape)
            for nid, delta in deltas[species]:
                if delta.shape != before.shape: raise SimulationError('modular.field_shape', 'Delta must follow ZYX grid')
                positive += np.maximum(delta, 0.); negative += np.maximum(-delta, 0.)
            uptake_rows = []
            for nid, payload in requested[species]:
                indexes = fields.grid.flat_indices(np.asarray(payload['positions_um']).reshape(-1, 3))
                amount = np.asarray(payload['requested_amount'], float)
                if np.any(amount < 0): raise SimulationError('modular.uptake', 'Negative request')
                np.add.at(negative.reshape(-1), indexes, amount)
                uptake_rows.append((nid, indexes, amount))
            available = before * unit + positive
            ratio = np.ones(before.shape); np.divide(available, negative, out=ratio, where=negative > 0); ratio = np.minimum(ratio, 1.)
            after = available - negative * ratio
            for nid, indexes, amount in uptake_rows:
                accepted = amount * ratio.reshape(-1)[indexes]
                outputs[nid]['accepted_amount'] = accepted
                outputs[nid]['accepted_flux'] = accepted / dt if dt else np.zeros_like(accepted)
                outputs[nid]['cumulative_uptake'] += accepted
                state[nid]['cumulative_uptake'] = outputs[nid]['cumulative_uptake'].copy()
                item['consumed'] += float(accepted.sum())
            for nid, delta in deltas[species]:
                actual = np.maximum(delta, 0.) - np.maximum(-delta, 0.) * ratio
                module = self.registry.get(self.plan.by_id[nid].module_id, self.plan.by_id[nid].module_version)
                account = getattr(module, 'effect_accounting', {}).get('field.delta', 'external_net')
                item[account] = item.get(account, 0.) + float(actual.sum())
                if 'net_input' in outputs[nid]:
                    actual_total, proposed_total = float(actual.sum()), float(delta.sum())
                    outputs[nid]['net_input'] = actual_total
                    if 'cumulative_net_input' in outputs[nid]:
                        outputs[nid]['cumulative_net_input'] += actual_total - proposed_total
                        state[nid]['cumulative_net_input'] += actual_total - proposed_total
                if 'consumed' in outputs[nid]:
                    consumed = float(-actual.sum()); proposed = float(-delta.sum())
                    outputs[nid]['consumed'] = consumed
                    outputs[nid]['cumulative_consumed'] += consumed - proposed
                    state[nid]['cumulative_consumed'] += consumed - proposed
            values[species] = np.maximum(after / unit, 0.)
        return replace(fields, concentrations_uM={s: v.reshape(-1) for s, v in values.items()}, diffusivities_um2_s=diffusivity)

    def _capsules(self, world):
        return tuple(Capsule(cid, group.positions_um[i], heading_from_orientation(group.orientation_xyzw)[i],
                    group.geometry[i].length_um, group.geometry[i].diameter_um)
            for gid, group in world.groups.items() for i, cid in enumerate(group.ids))

    def _geometry_effects(self, fields, effects, specs, initialize=False):
        from friskoli_cad.science.processes import mesh_voxel_mask, mesh_geometry
        specs = deepcopy(specs)
        for nid, effect in effects:
            if effect.kind == 'geometry.obstacle': specs[effect.target] = thaw(effect.value)
            elif effect.kind == 'geometry.residues': specs[effect.target] = {'kind': 'residues', 'bodies': thaw(effect.value), 'active': True}
        if not specs: return fields, (), specs
        grid = fields.grid; spacing = np.asarray((grid.dx_um, grid.dy_um, grid.dz_um))
        z, y, x = np.indices(grid.shape)
        centers = np.stack(((x + .5) * spacing[0], (y + .5) * spacing[1], (z + .5) * spacing[2]), axis=-1)
        blocked = np.zeros(grid.shape, bool); obstacles = []
        for owner, spec in specs.items():
            if not spec['active']: continue
            if spec['kind'] == 'box':
                lower, upper = np.asarray(spec['lower_um']), np.asarray(spec['upper_um'])
                mask = np.all((centers + spacing / 2 > lower) & (centers - spacing / 2 < upper), axis=-1)
                obstacles.append(BoxObstacle(owner, tuple(lower), tuple(upper)))
                blocked |= mask
            elif spec['kind'] == 'mesh':
                vertices, faces, _ = mesh_geometry(spec['vertices_xyz'], np.asarray(spec['faces'], int))
                if np.any(vertices < 0) or np.any(vertices > grid.extent_um): raise SimulationError('modular.mesh_domain', 'Mesh outside physical domain')
                mask = mesh_voxel_mask(vertices, faces, grid.shape, spacing)
                for iz, iy, ix in np.argwhere(mask):
                    lower = np.array((ix, iy, iz)) * spacing
                    obstacles.append(BoxObstacle(f'{owner}:{ix}:{iy}:{iz}', tuple(lower), tuple(lower + spacing)))
                blocked |= mask
            elif spec['kind'] == 'residues':
                for body in spec['bodies']:
                    center = np.asarray(body['position_um']); radius = body['diameter_um'] / 2
                    # Porous capsule envelope is represented conservatively by
                    # an axis-aligned box; it does not remove solvent volume.
                    half = np.full(3, body['length_um'] / 2)
                    obstacles.append(BoxObstacle(body['id'], tuple(center - half), tuple(center + half)))
            else: raise SimulationError('modular.geometry_kind', 'Unknown geometry proposal')
        concentrations = {}
        for species, values in fields.concentrations_uM.items():
            value = np.asarray(values).reshape(grid.shape).copy()
            if not initialize and np.any(value[blocked] > 0): raise SimulationError('modular.geometry_inventory', 'New solid would discard fluid inventory; explicit displacement mapping required')
            value[blocked] = 0.; concentrations[species] = value.reshape(-1)
        return replace(fields, blocked=_stored_values(grid, blocked.reshape(-1), boolean=True), concentrations_uM=concentrations), tuple(obstacles), specs

    def _growth_effects(self, world, effects, outputs, state, obstacles, ledger, dt):
        from friskoli_cad.science import physiology
        from .growth_system import realize_capsule_growth
        groups = dict(world.groups)
        proposals = []
        for nid, effect in effects:
            if effect.kind != 'geometry.growth': continue
            gid = effect.target; group = groups[gid]; value = effect.value
            requested = np.asarray(value['consumed'])
            # Realize from actual float64 geometry, then guard all cells jointly.
            available = np.asarray(state[nid]['intracellular_molecules']) + requested
            realized, lengths = realize_capsule_growth(available, [g.length_um for g in group.geometry],
                [g.diameter_um for g in group.geometry], requested, dt, value['yield_um3_molecule'])
            geometry = tuple(CapsuleGeometry(float(length), g.diameter_um) for length, g in zip(lengths, group.geometry))
            groups[gid] = CellGroup(gid, group.ids, group.positions_um, group.orientation_xyzw, geometry)
            proposals.append((nid, gid, value, realized, available))
        elongations = []
        for nid, effect in effects:
            if effect.kind != 'geometry.elongation': continue
            group = groups[effect.target]
            geometry = tuple(CapsuleGeometry(float(length), old.diameter_um) for length, old in zip(effect.value, group.geometry, strict=True))
            groups[effect.target] = CellGroup(group.id, group.ids, group.positions_um, group.orientation_xyzw, geometry)
            elongations.append((nid, effect.target))
        if not proposals and not elongations: return world
        candidate = World(world.grid, groups, world.species_initial_uM, world.schedules)
        # Capsule guard does not interpolate size; static validation identifies
        # every conflicting body and refunds those proposals in stable ID order.
        blocked = set()
        from .collision import InitialOverlapError
        for _ in range(len(proposals) + len(elongations) + 1):
            try:
                caps = self._capsules(candidate)
                guard_motion(caps, caps, extent_um=world.grid.extent_um, obstacles=obstacles, geometry=world.grid.geometry)
                break
            except InitialOverlapError as error:
                affected = {cid for contact in error.contacts for cid in contact.cell_ids}; changed = False
                for nid, gid, value, realized, available in proposals:
                    group, original = groups[gid], world.groups[gid]; geometry = list(group.geometry)
                    for i, cid in enumerate(group.ids):
                        if cid in affected and cid not in blocked:
                            geometry[i] = original.geometry[i]; blocked.add(cid); changed = True
                    groups[gid] = CellGroup(gid, group.ids, group.positions_um, group.orientation_xyzw, tuple(geometry))
                for nid, gid in elongations:
                    group, original = groups[gid], world.groups[gid]; geometry = list(group.geometry)
                    for i, cid in enumerate(group.ids):
                        if cid in affected and cid not in blocked:
                            geometry[i] = original.geometry[i]; blocked.add(cid); changed = True
                    groups[gid] = CellGroup(gid, group.ids, group.positions_um, group.orientation_xyzw, tuple(geometry))
                if not changed: raise
                candidate = World(world.grid, groups, world.species_initial_uM, world.schedules)
        for nid, gid, value, realized, available in proposals:
            mask = np.asarray([cid in blocked for cid in groups[gid].ids]); used = np.where(mask, 0., realized.used_molecules)
            volume = physiology.capsule_volume_um3([g.length_um for g in groups[gid].geometry], [g.diameter_um for g in groups[gid].geometry])
            from friskoli_cad.science.processes import compensated_amount
            remaining, correction = compensated_amount(state[nid]['intracellular_molecules'], state[nid]['reserve_correction_molecules'], np.asarray(value['consumed']) - used)
            outputs[nid].update(intracellular_molecules=remaining, volume=volume, used_molecules=used,
                growth_rate=np.where(mask, 0., realized.actual_growth_per_min), blocked=mask.astype(float))
            state[nid].update(intracellular_molecules=remaining, reserve_correction_molecules=correction, volume=volume)
            ledger[value['species']]['growth'] += float(used.sum())
        for nid, gid in elongations:
            outputs[nid]['length'] = np.asarray([g.length_um for g in groups[gid].geometry])
        return candidate

    def _motion_effects(self, world, effects, outputs, state, obstacles):
        paths, node_by_group = {}, {}
        for nid, effect in effects:
            if effect.kind == 'motion.paths':
                if effect.target in node_by_group: raise SimulationError('modular.motion_owner', 'Competing motion owners')
                node_by_group[effect.target] = nid
                paths.update({p['id']: p for p in thaw(effect.value)})
        if not paths: return world, []
        scales, displacements = {}, {}
        for nid, effect in effects:
            if effect.kind == 'motion.scale': scales[effect.target] = scales.get(effect.target, 1.) * np.asarray(effect.value)
            elif effect.kind == 'motion.displacement': displacements[effect.target] = displacements.get(effect.target, 0.) + np.asarray(effect.value)
        for gid, scale in scales.items():
            for cid, value in zip(world.groups[gid].ids, scale, strict=True):
                if not math.isfinite(value) or value < 0: raise SimulationError('modular.motion_scale', 'Propulsion factor must be finite and nonnegative')
                paths[cid]['speed_um_s'] *= value
        grid, groups_in = world.grid, list(world.groups.values())
        ids = [cid for g in groups_in for cid in g.ids]
        geometry = [geo for g in groups_in for geo in g.geometry]
        centers = np.vstack([g.positions_um.reshape(-1, 3) for g in groups_in])
        start_headings = np.vstack([heading_from_orientation(g.orientation_xyzw).reshape(-1, 3) if len(g.ids) else np.zeros((0, 3)) for g in groups_in])
        delta_by_id = {cid: np.asarray(displacements[gid][i], dtype=float) for gid in displacements for i, cid in enumerate(world.groups[gid].ids)}
        travel = np.zeros(len(ids))
        for k, cid in enumerate(ids):
            path = paths.get(cid)
            if path: travel[k] = path['speed_um_s'] * math.fsum(s['end_s'] - s['start_s'] for s in path['segments'] if s['phase'] == 'run')
            if cid in delta_by_id: travel[k] += float(np.linalg.norm(delta_by_id[cid]))
        scale = max(1., *grid.extent_um, float(np.abs(centers).max()) + float(travel.max()))
        # Cells whose whole-step swept sphere is clear of walls, solids and every other swept sphere cannot
        # collide: they take their path directly and only contested cells run the exact interval certification.
        contested = contested_mask(centers, [g.length_um / 2 for g in geometry], travel, grid.extent_um, obstacles, 1e-9 + 1024 * np.finfo(float).eps * scale)
        positions, headings = centers.copy(), start_headings.copy()
        for k, cid in enumerate(ids):
            if contested[k]: continue
            path = paths.get(cid)
            if path:
                for s in path['segments']:
                    if s['phase'] == 'run': positions[k] += path['speed_um_s'] * (s['end_s'] - s['start_s']) * np.asarray(s['heading'])
                headings[k] = path['final_heading']
            if cid in delta_by_id: positions[k] += delta_by_id[cid]
        blocked = set()
        if contested.any():
            index = np.flatnonzero(contested)
            capsules = tuple(Capsule(ids[k], centers[k], start_headings[k], geometry[k].length_um, geometry[k].diameter_um) for k in index)
            boundaries = sorted({s['end_s'] for p in paths.values() for s in p['segments']})
            elapsed = 0.
            for boundary in boundaries:
                proposed = []
                for capsule in capsules:
                    path = paths.get(capsule.cell_id)
                    segment = next((s for s in path['segments'] if s['start_s'] <= elapsed < s['end_s']), None) if path else None
                    if segment is None: proposed.append(capsule); continue
                    heading = segment['heading']; speed = path['speed_um_s'] if segment['phase'] == 'run' else 0.
                    proposed.append(replace(capsule, heading=tuple(heading), position_um=tuple(np.asarray(capsule.position_um) + speed * (boundary - elapsed) * np.asarray(heading))))
                guarded = guard_motion(capsules, tuple(proposed), extent_um=grid.extent_um, obstacles=obstacles, geometry=grid.geometry)
                capsules = guarded.capsules; blocked.update(guarded.blocked_ids); elapsed = boundary
            if displacements:
                proposed = tuple(replace(c, position_um=tuple(np.asarray(c.position_um) + delta_by_id.get(c.cell_id, 0.))) for c in capsules)
                guarded = guard_motion(capsules, proposed, extent_um=grid.extent_um, obstacles=obstacles, geometry=grid.geometry)
                capsules = guarded.capsules; blocked.update(guarded.blocked_ids)
            final = tuple(replace(c, heading=tuple(paths[c.cell_id]['final_heading'])) if c.cell_id in paths else c for c in capsules)
            guarded = guard_motion(capsules, final, extent_um=grid.extent_um, obstacles=obstacles, geometry=grid.geometry)
            for k, c in zip(index, guarded.capsules): positions[k], headings[k] = c.position_um, c.heading
            blocked.update(guarded.blocked_ids)
        groups, offset = {}, 0
        for gid, group in world.groups.items():
            n = len(group.ids); group_positions, group_headings = positions[offset:offset + n], headings[offset:offset + n]; offset += n
            groups[gid] = CellGroup(gid, group.ids, group_positions, orientation_after_heading(group.orientation_xyzw, group_headings), group.geometry)
            nid = node_by_group.get(gid)
            if nid:
                outputs[nid]['position'], outputs[nid]['heading'] = group_positions, group_headings
                outputs[nid]['blocked'] = np.asarray([float(c in blocked) for c in group.ids])
                state[nid]['heading'] = group_headings
                for i, heading in enumerate(group_headings): state[nid]['walks'][i]['heading'] = heading.tolist()
        return World(world.grid, groups, world.species_initial_uM, world.schedules), []

    def _lifecycle_effects(self, world, effects, outputs, state, dead, time):
        deaths = {cid for nid, e in effects if e.kind == 'lifecycle.death' for cid in e.value['ids']}
        if not deaths: return world, [], dead
        groups, events = {}, []
        for gid, group in world.groups.items():
            keep = [i for i, cid in enumerate(group.ids) if cid not in deaths]
            for i, cid in enumerate(group.ids):
                if cid not in deaths: continue
                residual = {}
                for node in self.plan.nodes:
                    if node.owner_id == gid and 'inventory.owner' in getattr(self.registry.get(node.module_id, node.module_version), 'provides_roles', ()):
                        residual[node.parameters['species'].value] = float(state[node.id]['intracellular_molecules'][i]) + float(state[node.id].get('reserve_correction_molecules', np.zeros(len(group.ids)))[i])
                dead[cid] = {'group_id': gid, 'time_s': time, 'residual_molecules': residual,
                    'position_um': group.positions_um[i].tolist(), 'length_um': group.geometry[i].length_um, 'diameter_um': group.geometry[i].diameter_um}
                events.append({'type': 'death', 'cell_id': cid, 'time_s': time})
            groups[gid] = CellGroup(gid, tuple(group.ids[i] for i in keep), group.positions_um[keep].reshape(-1, 3),
                group.orientation_xyzw[keep].reshape(-1, 4), tuple(group.geometry[i] for i in keep))
            for node in self.plan.nodes:
                if node.owner_kind != 'population' or node.owner_id != gid: continue
                for collection, specs in [(outputs, self.registry.get(node.module_id, node.module_version).manifest['outputs']),
                                          (state, self.registry.get(node.module_id, node.module_version).manifest['state'])]:
                    for key, value in collection[node.id].items():
                        if specs[key]['shape'].startswith('cell.'):
                            collection[node.id][key] = [value[i] for i in keep] if specs[key]['shape'].endswith('.record') else np.asarray(value)[keep]
        return World(world.grid, groups, world.species_initial_uM, world.schedules), events, dead

    def _division_effects(self, world, effects, outputs, state, obstacles, time, next_id):
        from friskoli_cad.science import physiology
        groups = dict(world.groups); events, details = [], []
        for division_node, effect in effects:
            if effect.kind != 'lifecycle.division': continue
            gid = effect.target
            for request in effect.value:
                group = groups[gid]; parent = request['parent_id']
                if parent not in group.ids: continue  # death precedes division
                index = group.ids.index(parent); old = group.geometry[index]
                total = float(physiology.capsule_volume_um3(old.length_um, old.diameter_um))
                if total < request['threshold_volume']: continue
                fraction = float(request['fraction'])
                if not 0 < fraction < 1: raise SimulationError('modular.division_fraction', 'Invalid daughter volume fraction')
                volumes = (total * fraction, total * (1 - fraction))
                try:
                    shapes = tuple(CapsuleGeometry(float(physiology.capsule_geometry_from_volume(v, old.diameter_um / 2).total_length_um), old.diameter_um) for v in volumes)
                except ValueError:
                    outputs[division_node]['blocked'][index] = 1.; continue
                if sum(len(g.ids) for g in groups.values()) >= self.project.get('system_limits', {}).get('max_cells', 256):
                    raise SimulationError('resource.cell_limit', 'Division would exceed system_limits.max_cells; last committed state is preserved', '/system_limits/max_cells')
                heading = heading_from_orientation(group.orientation_xyzw)[index]
                separation = (shapes[0].length_um + shapes[1].length_um) / 2
                center = group.positions_um[index]
                child = f'{parent}__child_{next_id}'
                while child in self.frame_validator.seen or any(child in g.ids for g in groups.values()):
                    next_id += 1; child = f'{parent}__child_{next_id}'
                positions = np.vstack((group.positions_um, center + fraction * separation * heading)); positions[index] = center - (1 - fraction) * separation * heading
                geometry = list(group.geometry) + [shapes[1]]; geometry[index] = shapes[0]
                proposed = CellGroup(gid, group.ids + (child,), positions,
                    np.vstack((group.orientation_xyzw, group.orientation_xyzw[index])), tuple(geometry))
                candidate = World(world.grid, {**groups, gid: proposed}, world.species_initial_uM, world.schedules)
                from .collision import InitialOverlapError
                try:
                    capsules = self._capsules(candidate)
                    guard_motion(capsules, capsules, extent_um=world.grid.extent_um, obstacles=obstacles, geometry=world.grid.geometry)
                except InitialOverlapError:
                    outputs[division_node]['blocked'][index] = 1.; continue
                groups[gid] = proposed; next_id += 1
                for node in self.plan.nodes:
                    if node.owner_kind != 'population' or node.owner_id != gid: continue
                    module = self.registry.get(node.module_id, node.module_version)
                    for collection, specs in [(outputs, module.manifest['outputs']), (state, module.manifest['state'])]:
                        for key, value in collection[node.id].items():
                            spec = specs[key]
                            if not spec['shape'].startswith('cell.'): continue
                            if spec['shape'].endswith('.record'):
                                fresh = thaw(value); fresh.append(deepcopy(fresh[index]))
                                if spec.get('on_division') == 'reset':
                                    fresh[index] = deepcopy(spec['initial_value']); fresh[-1] = deepcopy(spec['initial_value'])
                            else:
                                value = np.asarray(value); fresh = np.concatenate((value, value[index:index + 1]), axis=0)
                                policy = spec.get('on_division')
                                extensive = policy == 'split' or (collection is outputs and spec.get('unit') in ('molecule', 'molecule/s', 'um^3'))
                                if policy == 'reset':
                                    fresh[index] = spec['initial_value']; fresh[-1] = spec['initial_value']
                                elif extensive:
                                    first = np.floor(value[index] * fraction).astype(value.dtype) if np.issubdtype(value.dtype, np.integer) else value[index] * fraction
                                    fresh[index] = first; fresh[-1] = value[index] - first
                            collection[node.id][key] = fresh
                    for key in module.execution_contract.get('division_reset_states', ()):
                        state[node.id][key][index] = volumes[0]; state[node.id][key][-1] = volumes[1]
                        if key in outputs[node.id]: outputs[node.id][key] = state[node.id][key].copy()
                    if 'motion.owner' in getattr(module, 'provides_roles', ()):
                        outputs[node.id]['position'] = proposed.positions_um
                outputs[division_node]['divide'][index] = outputs[division_node]['divide'][-1] = 1.
                events.append({'type': 'division', 'parent_id': parent, 'child_id': child, 'time_s': time})
                details.append({'parent_id': parent, 'child_id': child,
                    'added_membrane_area_um2': math.pi * old.diameter_um * (shapes[0].length_um + shapes[1].length_um - old.length_um),
                    'membrane_assumption': 'new geometric membrane area is implicitly available; no membrane material budget'})
        return World(world.grid, groups, world.species_initial_uM, world.schedules), events, details, next_id

    def _validate_values(self, world, fields, outputs, state):
        from .port_semantics import validate_port_value
        for node in self.plan.nodes:
            module = self.registry.get(node.module_id, node.module_version)
            context = StepContext(self.time_s, 0., self.frame_index, node.owner_id,
                world.groups[node.owner_id].ids if node.owner_kind == 'population' else (), {'grid_shape_zyx': world.grid.shape},
                parameters={k: v.value for k, v in node.parameters.items()},
                entity_sets={'population:' + gid: group.ids for gid, group in world.groups.items()})
            for collection, specs in [(outputs[node.id], module.manifest['outputs']), (state[node.id], module.manifest['state'])]:
                if set(collection) != set(specs): raise SimulationError('modular.ports', 'Declared output/state mismatch')
                for key, value in collection.items(): validate_port_value(value, specs[key], context, f'/{node.id}/{key}')

    def _snapshot(self, world, fields, outputs, time, index, events, metrics):
        cells = []
        for gid, group in world.groups.items():
            for i, cid in enumerate(group.ids):
                cells.append({'id': cid, 'group_id': gid, 'position_um': group.positions_um[i].tolist(),
                    'orientation_xyzw': group.orientation_xyzw[i].tolist(), 'geometry': {'shape': 'capsule',
                    'length_um': group.geometry[i].length_um, 'diameter_um': group.geometry[i].diameter_um},
                    'channels': {key: np.asarray(outputs[ch['node']][ch['port']][i]).tolist() for key, ch in self.run['channels'].items() if ch['group_id'] == gid}})
        frame = {'protocol_version': '0.1.0', 'frame_version': '0.2.0', 'run_id': self.run['run_id'],
                 'frame_index': int(index), 'time_s': time, 'cells': cells, 'events': events}
        objects = {owner: {'object_type': declaration['object_type'], **{name: float(outputs[declaration['node_id']][port]) for name, port in declaration['outputs'].items()}}
            for owner, declaration in snapshot_object_declarations(self.project, self.registry).items()}
        return Snapshot(frame, MappingProxyType({s: np.asarray(v).reshape(world.grid.shape) for s, v in fields.concentrations_uM.items()}),
            MappingProxyType({s: 'uM' for s in fields.concentrations_uM}), MappingProxyType({}), world.grid,
            object_states=freeze(objects), metrics=deepcopy(metrics), lifecycle_details={'lifecycle_version': '1.0.0', 'events': deepcopy(events)})

    def step(self, dt_s):
        if not isinstance(dt_s, (float, int)) or not math.isfinite(dt_s) or dt_s <= 0:
            raise SimulationError('time.step', 'dt must be positive and finite')
        return self._transaction(float(dt_s))

    def checkpoint(self):
        from .modular_checkpoint import export_checkpoint
        return export_checkpoint(self)

    @classmethod
    def from_checkpoint(cls, project, payload, registry=None):
        from .modular_checkpoint import restore_checkpoint
        return restore_checkpoint(project, payload, registry)
