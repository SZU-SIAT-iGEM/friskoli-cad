"""Complete modular ownership checkpoint, including structured module state."""
from copy import deepcopy
from dataclasses import asdict
import math
import numpy as np

from friskoli_cad.protocol import FrameSequenceValidator
from .core import World, CellGroup, CapsuleGeometry, SimulationError
from .module_api import thaw, freeze, StepContext, execute_module
from .local_fields import local_field_state_to_dict, local_field_state_from_dict
from .random_streams import RandomStreams
from .checkpoint_tools import _hash, _implementation_lock, _validator_record

VERSION = 'modular-checkpoint/v1'


def _json(value):
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, dict): return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_json(v) for v in value]
    return value.item() if isinstance(value, np.generic) else value


def export_checkpoint(sim, binary=False):
    fields = local_field_state_to_dict(sim.fields, binary=binary)
    payload = {'version': VERSION, 'execution_profile': sim.project['execution_profile'], 'project_sha256': _hash(sim.project),
        'implementation_lock': _implementation_lock(sim.registry, sim.field_backend), 'seed': sim.seed,
        'time_s': sim.time_s, 'frame_index': sim.frame_index, 'last_dt_s': sim.last_dt_s, 'next_cell_index': sim.next_cell_index,
        'world': {gid: {'ids': list(g.ids), 'positions_um': g.positions_um, 'orientation_xyzw': g.orientation_xyzw,
                       'geometry': [asdict(v) for v in g.geometry]} for gid, g in sim.world.groups.items()},
        'local_fields': fields, 'outputs': thaw(sim.outputs), 'state': thaw(sim.state), 'random_streams': sim.streams.to_dict(),
        'ledger': deepcopy(sim.ledger), 'initial_amounts': dict(sim.initial_amounts), 'metrics': deepcopy(sim.metrics),
        'dead_material': deepcopy(sim.dead_material), 'geometry_state': deepcopy(sim.geometry_state),
        'events': deepcopy(sim.events), 'frame_validator': _validator_record(sim.frame_validator), 'current_frame': deepcopy(sim.current.cell_frame)}
    if hasattr(sim,'migration_origin_project'):
        payload['migration_origin_project'] = deepcopy(sim.migration_origin_project)
    if not binary: payload = _json(payload)
    payload['payload_sha256'] = _hash(payload)
    return payload


def restore_checkpoint(project, payload, registry=None):
    from friskoli_cad.project import simulation_from_project
    sim = simulation_from_project(project, registry, seed=payload.get('seed'), field_backend=payload.get('local_fields', {}).get('backend', 'numpy-cpu'))
    raw = deepcopy(payload)
    signature = raw.pop('payload_sha256', None)
    if signature != _hash(raw): raise SimulationError('modular.checkpoint_hash', 'Checkpoint payload hash mismatch')
    origin = raw.pop('migration_origin_project',None)
    expected_keys = set(export_checkpoint(sim, binary=True)) - {'payload_sha256'}
    if set(raw) != expected_keys: raise SimulationError('modular.checkpoint_keys', 'Missing or unknown checkpoint owners')
    if raw['version'] != VERSION or raw['project_sha256'] != _hash(project) or raw['implementation_lock'] != _implementation_lock(sim.registry, sim.field_backend):
        raise SimulationError('modular.checkpoint_lock', 'Project or implementation identity differs')
    initial_amounts = sim.initial_amounts
    if origin is not None:
        from .task_migration import scientific_identity
        if scientific_identity(origin) != scientific_identity(project):
            raise SimulationError('modular.checkpoint_origin','Migration origin changes scientific project data or dependency locks')
        if origin['domain']['geometry'] != project['domain']['geometry']:
            raise SimulationError('modular.checkpoint_origin','Migration origin changes geometry mode')
        baseline = simulation_from_project(origin,registry,seed=raw['seed'],field_backend=sim.field_backend)
        initial_amounts = baseline.initial_amounts
        sim.migration_origin_project = deepcopy(origin)
    if raw['initial_amounts'] != initial_amounts:
        raise SimulationError('modular.checkpoint_initial', 'Initial inventory differs from project initialization')
    time, index, dt = raw['time_s'], raw['frame_index'], raw['last_dt_s']
    if type(index) is not int or index < 0 or not math.isfinite(time) or time < 0 or not math.isfinite(dt) or dt < 0:
        raise SimulationError('modular.checkpoint_clock', 'Invalid committed clock')
    if (index == 0 and (time != 0 or dt != 0)) or (index > 0 and not 0 < dt <= time):
        raise SimulationError('modular.checkpoint_clock', 'Clock and interval disagree')
    if set(raw['world']) != set(sim.world.groups): raise SimulationError('modular.checkpoint_groups', 'Population owners differ')
    groups = {}
    for gid, record in raw['world'].items():
        if set(record) != {'ids', 'positions_um', 'orientation_xyzw', 'geometry'}: raise SimulationError('modular.checkpoint_groups', 'Population fields differ')
        groups[gid] = CellGroup(gid, tuple(record['ids']), np.asarray(record['positions_um']).reshape(-1, 3),
            np.asarray(record['orientation_xyzw']).reshape(-1, 4), tuple(CapsuleGeometry(**value) for value in record['geometry']))
    world = World(sim.world.grid, groups, sim.world.species_initial_uM, sim.world.schedules)
    fields_raw = deepcopy(raw['local_fields'])
    # The bounded binary container already validates dtypes/hash; the field
    # constructor still validates shape, positivity, mask and physical units.
    if np.asarray(fields_raw['blocked']).dtype != np.dtype(bool):
        raise SimulationError('modular.checkpoint_mask', 'Mask must contain actual booleans')
    if fields_raw.get('sources') != []:
        raise SimulationError('modular.checkpoint_sources', 'This profile permits only registered module-owned sources')
    fields = local_field_state_from_dict(fields_raw, max_voxels=world.grid.voxel_count,
        max_values=world.grid.voxel_count * max(1, len(sim.fields.concentrations_uM)))
    if fields.grid != world.grid or set(fields.concentrations_uM) != set(sim.fields.concentrations_uM):
        raise SimulationError('modular.checkpoint_field', 'Physical grid or active species differ')
    outputs, state = raw['outputs'], raw['state']
    if set(outputs) != set(sim.plan.by_id) or set(state) != set(sim.plan.by_id):
        raise SimulationError('modular.checkpoint_nodes', 'Node owners differ')
    for node in sim.plan.nodes:
        manifest = sim.registry.get(node.module_id, node.module_version).manifest
        for collection, specs in [(outputs[node.id], manifest['outputs']), (state[node.id], manifest['state'])]:
            for name, value in collection.items():
                if name in specs and not specs[name]['shape'].endswith('.record') and specs[name]['shape'] != 'event':
                    from .port_semantics import port_semantics
                    dtype = port_semantics(specs[name])['dtype']
                    raw_array = np.asarray(value)
                    if dtype == 'bool' and raw_array.size and raw_array.dtype.kind != 'b':
                        raise SimulationError('modular.checkpoint_dtype', 'Boolean state requires actual booleans')
                    if dtype.startswith(('int', 'uint')) and raw_array.size and raw_array.dtype.kind not in 'iu':
                        raise SimulationError('modular.checkpoint_dtype', 'Index state requires integer values')
                    if dtype.startswith('uint') and np.any(raw_array < 0):
                        raise SimulationError('modular.checkpoint_dtype', 'Unsigned index is negative')
                    array = np.asarray(value, dtype=dtype)
                    if specs[name]['shape'].startswith('cell.') and array.size == 0:
                        from .port_semantics import port_semantics
                        dimensions = port_semantics(specs[name])['tensor_shape']
                        resolved = tuple(node.parameters[d.split(':', 1)[1]].value if isinstance(d, str) else d for d in dimensions)
                        array = array.reshape((0,) + resolved)
                    collection[name] = array
    sim._validate_values(world, fields, outputs, state)
    for species, nid in sim.field_owners.items():
        if not np.array_equal(outputs[nid]['concentration'].reshape(-1), fields.concentrations_uM[species]):
            raise SimulationError('modular.checkpoint_field', 'Field output differs from owner state')
    for node in sim.plan.nodes:
        module = sim.registry.get(node.module_id, node.module_version)
        inventory = getattr(module, 'checkpoint_inventory', None)
        if inventory:
            value = float(state[node.id][inventory['state']])
            if not 0 <= value <= node.parameters[inventory['initial_parameter']].value or value != float(outputs[node.id][inventory['output']]):
                raise SimulationError('modular.checkpoint_inventory', 'Finite inventory differs from its declared owner or initial stock')
    if sum(len(group.ids) for group in groups.values()) > project.get('system_limits', {}).get('max_cells', 256):
        raise SimulationError('resource.cell_limit', 'Restored live population exceeds system_limits.max_cells')
    streams = RandomStreams.from_dict(raw['random_streams'])
    if streams.run_seed != raw['seed'] or raw['seed'] != sim.seed: raise SimulationError('modular.checkpoint_rng', 'RNG seed differs')
    for entry in raw['random_streams']['streams']:
        nid, gid, cid, purpose = entry['key']
        if nid not in sim.plan.by_id or sim.plan.by_id[nid].owner_id != gid or cid not in raw['frame_validator']['seen']:
            raise SimulationError('modular.checkpoint_rng', 'Foreign RNG namespace')
        module = sim.registry.get(sim.plan.by_id[nid].module_id, sim.plan.by_id[nid].module_version)
        if purpose not in module.execution_contract.get('rng_purposes', ()):
            raise SimulationError('modular.checkpoint_rng', 'Undeclared random stream purpose')
        expected_inc = RandomStreams(sim.seed).stream(nid, gid, cid, purpose).bit_generator.state['state']['inc']
        if int(entry['state']['state']['inc'], 16) != expected_inc:
            raise SimulationError('modular.checkpoint_rng', 'Random stream increment differs from its namespace')
    sim.time_s, sim.frame_index, sim.last_dt_s = time, index, dt
    # Re-evaluate only declared geometric/property modules at zero duration,
    # using the restored typed state. Never relax old-profile validators.
    effects = []
    for node in sim.plan.nodes:
        module = sim.registry.get(node.module_id, node.module_version)
        kinds = set(module.execution_contract['effects'])
        if not kinds & {'geometry.obstacle', 'geometry.residues', 'field.diffusivity'}: continue
        resources = sim._resources(world, fields, node, raw['events'], raw['dead_material'], raw['metrics'])
        context = StepContext(time, 0., index, node.owner_id, groups[node.owner_id].ids if node.owner_kind == 'population' else (),
            {k: resources[k] for k in module.execution_contract['reads']},
            {name: outputs[b.source_node][b.source_port] for name, b in node.inputs.items()},
            {k: v.value for k, v in node.parameters.items()}, state[node.id], streams.clone(), node_id=node.id,
            entity_sets={'population:' + gid: group.ids for gid, group in groups.items()})
        proposal = execute_module(module, context)
        effects.extend((node.id, effect) for effect in proposal.effects)
    derived_fields, obstacles, derived = sim._geometry_effects(fields, effects, {})
    if not np.array_equal(derived_fields.blocked, fields.blocked):
        raise SimulationError('modular.checkpoint_geometry', 'Blocked mask differs from registered geometry')
    if _hash(_json(derived)) != _hash(raw['geometry_state']): raise SimulationError('modular.checkpoint_geometry', 'Geometry differs from declared module state')
    expected_diffusivities = dict(sim.fields.diffusivities_um2_s)
    for nid, effect in effects:
        if effect.kind == 'field.diffusivity': expected_diffusivities[effect.target] = float(effect.value)
    if dict(fields.diffusivities_um2_s) != expected_diffusivities:
        raise SimulationError('modular.checkpoint_diffusivity', 'Diffusivity differs from declared property')
    from .collision import guard_motion
    caps = sim._capsules(world)
    guard_motion(caps, caps, extent_um=world.grid.extent_um, obstacles=obstacles, geometry=world.grid.geometry)
    validator = FrameSequenceValidator(sim.run)
    v = raw['frame_validator']; ids = {cid: gid for gid, group in groups.items() for cid in group.ids}
    if v['alive'] != ids or v['previous_time'] != time or v['next_index'] != index + 1:
        raise SimulationError('modular.checkpoint_history', 'Frame history disagrees with live owners')
    validator.run_id, validator.frame_version = v['run_id'], v['frame_version']
    validator.next_index, validator.previous_time, validator.alive, validator.seen = v['next_index'], time, dict(ids), set(v['seen'])
    sim.world, sim.fields, sim.outputs, sim.state = world, fields, freeze(outputs), freeze(state)
    sim.streams, sim.ledger, sim.metrics, sim.dead_material = streams, raw['ledger'], raw['metrics'], raw['dead_material']
    sim.geometry_state, sim.obstacles, sim.events = derived, obstacles, raw['events']
    sim.initial_amounts, sim.next_cell_index, sim.frame_validator = raw['initial_amounts'], raw['next_cell_index'], validator
    sim.current = sim._snapshot(world, fields, outputs, time, index, sim.events, sim.metrics)
    if _hash(sim.current.cell_frame) != _hash(raw['current_frame']): raise SimulationError('modular.checkpoint_frame', 'Frame differs from owners')
    for species, initial in sim.initial_amounts.items():
        ledger = sim.ledger.get(species, {})
        actual = float(np.sum(fields.concentrations_uM[species])) * world.grid.molecules_per_uM_voxel + ledger.get('consumed', 0.)
        expected = initial + sum(ledger.get(key, 0.) for key in ('external_net', 'internal_net', 'reaction_net'))
        if not math.isclose(actual, expected, rel_tol=2e-10, abs_tol=1e-7):
            raise SimulationError('modular.checkpoint_balance', 'Field and transfer ledger disagree')
    return sim
