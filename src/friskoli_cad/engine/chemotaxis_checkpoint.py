"""Strict complete checkpoints for adaptive motion and variable populations."""
from copy import deepcopy
from dataclasses import asdict, fields as dataclass_fields
import math
from types import MappingProxyType

import numpy as np

from friskoli_cad.protocol import FrameSequenceValidator
from .collision import Contact
from .local_fields import FieldLedger, local_field_state_to_dict
from .motion import heading_from_orientation
from .random_streams import RandomStreams
from .hazard_walk import HazardWalkState
from .runtime import CellGroup, CapsuleGeometry, World, SimulationError
from .pts_runtime import _freeze
from .spatial_checkpoint import (_hash, _implementation_lock, _nested_to_dict, _validator_record,
                                 _array, _restore_materials, _restore_fields)

CHECKPOINT_VERSION = 'chemotaxis-checkpoint/v1'
KEYS = {'version', 'execution_profile', 'project_sha256', 'implementation_lock', 'seed', 'time_s', 'frame_index', 'last_dt_s',
        'world', 'local_fields', 'materials', 'material_ledger', 'walks', 'random_streams', 'outputs', 'state',
        'current_frame', 'ledger', 'frame_validator', 'motion_contacts', 'supply_totals', 'uptake_totals',
        'physiology_ledger', 'dead_material', 'next_cell_index', 'observation_state', 'object_states', 'metrics', 'lifecycle_details', 'payload_sha256'}


def _reject(message):
    raise SimulationError('chemotaxis.checkpoint', message)


def _keys(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        _reject(f'{label} has missing or unknown fields')


def _number(value, label, nonnegative=True):
    if type(value) not in (float, int) or not math.isfinite(value) or (nonnegative and value < 0):
        _reject(f'{label} must be a finite JSON number')
    return value


def export_checkpoint(sim, *, binary=False):
    payload = {'version': CHECKPOINT_VERSION, 'execution_profile': sim.project['execution_profile'],
        'project_sha256': _hash(sim.project), 'implementation_lock': _implementation_lock(sim.registry, sim.field_backend),
        'seed': sim.seed, 'time_s': sim.time_s, 'frame_index': sim.frame_index, 'last_dt_s': sim.last_dt_s,
        'world': {gid: {'ids': list(g.ids), 'positions_um': g.positions_um.tolist(), 'orientation_xyzw': g.orientation_xyzw.tolist(),
                         'geometry': [asdict(geom) for geom in g.geometry]} for gid, g in sim.world.groups.items()},
        'local_fields': local_field_state_to_dict(sim.fields, binary=binary),
        'materials': {mid: asdict(material) for mid, material in sim.materials.items()},
        'material_ledger': {mid: asdict(ledger) for mid, ledger in sim.material_ledger.items()},
        'walks': {cid: state.to_dict() for cid, state in sim.walks.items()}, 'random_streams': sim.streams.to_dict(),
        'outputs': _nested_to_dict(sim.outputs, binary=binary), 'state': _nested_to_dict(sim.state, binary=binary),
        'current_frame': deepcopy(sim.current.cell_frame), 'ledger': {s: asdict(v) for s, v in sim.ledger.items()},
        'frame_validator': _validator_record(sim.frame_validator), 'motion_contacts': [asdict(v) for v in sim.motion_contacts],
        'supply_totals': dict(sim.supply_totals), 'uptake_totals': dict(sim.uptake_totals),
        'physiology_ledger': deepcopy(sim.physiology_ledger), 'dead_material': deepcopy(sim.dead_material),
        'next_cell_index': sim.next_cell_index, 'observation_state': deepcopy(sim.observation_state),
        'object_states': {key: dict(value) for key, value in sim.current.object_states.items()}, 'metrics': dict(sim.current.metrics),
        'lifecycle_details': deepcopy(sim.current.lifecycle_details)}
    # Normalize dataclass tuple fields to ordinary JSON before hashing/return.
    import json
    if not binary:
        payload = json.loads(json.dumps(payload, allow_nan=False))
    payload['payload_sha256'] = _hash(payload)
    return payload


def _nested(sim, payload, world, attribute):
    _keys(payload, sim.plan.by_id, attribute)
    result = {}
    for node in sim.plan.nodes:
        manifest = sim.registry.get(node.module_id, node.module_version).manifest
        specs = manifest['outputs' if attribute == 'outputs' else 'state']
        _keys(payload[node.id], specs, node.id)
        count = len(world.groups[node.owner_id].ids) if node.owner_kind == 'population' else None
        result[node.id] = {}
        for key, spec in specs.items():
            shape = spec['shape']
            expected = world.grid.shape if shape == 'field.scalar' else (count, 3) if shape == 'cell.vector' else (count,) if shape.startswith('cell.') else ()
            value = _array(payload[node.id][key], expected, node.id + '.' + key)
            result[node.id][key] = value
    return result


def _restore_validator(sim, raw, world, index, time):
    _keys(raw, {'run_id', 'next_index', 'previous_time', 'frame_version', 'alive', 'seen'}, 'frame validator')
    alive = {cid: gid for gid, group in world.groups.items() for cid in group.ids}
    initial = {cid for group in sim.project['groups'].values() for cid in group['ids']}
    if (raw['run_id'] != sim.run['run_id'] or type(raw['next_index']) is not int or raw['next_index'] != index + 1
        or raw['previous_time'] != time or raw['frame_version'] != '0.2.0' or raw['alive'] != alive
        or type(raw['seen']) is not list or any(type(cid) is not str or not cid for cid in raw['seen'])
        or raw['seen'] != sorted(set(raw['seen'])) or not (initial | set(alive)).issubset(raw['seen'])):
        _reject('Validator identity/time/history disagrees with world')
    result = FrameSequenceValidator(sim.run)
    for key in ('run_id', 'next_index', 'previous_time', 'frame_version', 'alive'):
        setattr(result, key, deepcopy(raw[key]))
    result.seen = set(raw['seen'])
    return result


def _coherence(sim):
    from friskoli_cad.science.physiology import capsule_volume_um3
    for node in sim.plan.nodes:
        output, state = sim.outputs[node.id], sim.state[node.id]
        for key, array in output.items():
            if key not in ('heading', 'gradient', 'adaptation') and np.any(array < 0):
                _reject(f'Negative output {node.id}.{key}')
            if key in ('motor_bias', 'health', 'activity', 'blocked', 'tumble_phase', 'ei_fraction') and np.any(array > 1):
                _reject(f'Fraction exceeds one: {node.id}.{key}')
        for key, array in state.items():
            if key in output and not np.array_equal(array, output[key]):
                _reject(f'State/output disagree: {node.id}.{key}')
        if node.module_id in sim.FIELD_MODULE_IDS:
            species = node.parameters['species'].value
            if not np.array_equal(output['concentration'], np.asarray(sim.fields.concentrations_uM[species]).reshape(sim.world.grid.shape)):
                _reject('Field output disagrees with owner')
            if species in sim.supply_totals and float(output['cumulative_supply']) != sim.supply_totals[species]:
                _reject('Reservoir supply output disagrees with ledger')
        elif node.module_id in sim.MOTION_MODULE_IDS:
            group = sim.world.groups[node.owner_id]
            heading = np.asarray([sim.walks[c].heading for c in group.ids]).reshape(-1, 3)
            if not np.array_equal(output['position'], group.positions_um) or not np.array_equal(output['heading'], heading):
                _reject('Motion output disagrees with world or clocks')
            hazard = np.asarray([sim.walks[c].remaining_hazard or 0. for c in group.ids])
            phase = np.asarray([float(sim.walks[c].phase == 'tumble') for c in group.ids])
            dwell = np.asarray([sim.walks[c].dwell_remaining_s for c in group.ids])
            if (not np.array_equal(output['hazard_remaining'], hazard) or not np.array_equal(output['tumble_phase'], phase)
                or not np.array_equal(state['remaining_hazard'], hazard) or not np.array_equal(state['dwell_remaining'], dwell)):
                _reject('Visible motion clocks disagree with exact clocks')
            if np.any(output['turns'] != np.floor(output['turns'])) or np.any((output['blocked'] != 0) & (output['blocked'] != 1)):
                _reject('Turns and blocked must be integral')
        elif node.module_id.startswith('growth.nutrient_'):
            group = sim.world.groups[node.owner_id]
            volume = capsule_volume_um3([g.length_um for g in group.geometry], [g.diameter_um for g in group.geometry])
            if not np.allclose(output['volume'], volume, rtol=1e-13, atol=0):
                _reject('Growth volume disagrees with geometry')
        elif node.module_id == 'life.starvation_hazard':
            expected_health = np.exp(-state['starvation_time_s'] / node.parameters['grace_s'].value)
            if not np.array_equal(output['health'], expected_health):
                _reject('Starvation readout differs from exposure state')
        elif node.module_id == 'metabolism.reserve_balance':
            high, low = state['intracellular_molecules'], state['reserve_correction_molecules']
            if np.any(np.abs(low) > np.spacing(high) / 2) or np.any(high + low < 0):
                _reject('Invalid compensated reserve')
        elif node.module_id == 'division.area_adder':
            if np.any(state['birth_area'] <= 0):
                _reject('Division birth area must be positive')
            if node.module_version == '2.0.0' and np.any(state['required_area'] < node.parameters['minimum_area_um2'].value):
                _reject('Division threshold is below its positive floor')
        elif node.module_id == 'source.finite_local':
            value = next(s.remaining_molecules for s in sim.fields.sources if s.id == node.id)
            if float(output['inventory']) != value:
                _reject('Source inventory output differs from owner')
        elif node.module_id == 'material.degradable_box':
            if float(output['inventory']) != sim.materials[node.id].remaining_molecules:
                _reject('Material inventory output differs from owner')


def restore_checkpoint(project, payload, registry=None):
    from friskoli_cad.project import simulation_from_project
    from .chemotaxis_runtime import PROFILE
    from .observations import validate_observation_state
    try:
        _keys(payload, KEYS, 'checkpoint')
        if payload['version'] != CHECKPOINT_VERSION or payload['execution_profile'] != PROFILE:
            _reject('Unsupported checkpoint version/profile')
        if payload['payload_sha256'] != _hash({k: v for k, v in payload.items() if k != 'payload_sha256'}):
            _reject('Checkpoint checksum mismatch')
        if payload['project_sha256'] != _hash(project):
            _reject('Checkpoint project mismatch')
        if type(payload['seed']) is not int or payload['seed'] < 0:
            _reject('Invalid execution seed')
        if type(payload['local_fields']) is not dict:
            _reject('Invalid local field state')
        backend = payload['local_fields'].get('backend', 'numpy-cpu')
        from .field_backend import validate_backend
        validate_backend(backend)
        sim = simulation_from_project(project, registry=registry, seed=payload['seed'], field_backend=backend)
        initial_field_amounts = {s: math.fsum(values) * sim.world.grid.molecules_per_uM_voxel for s, values in sim.fields.concentrations_uM.items()}
        if payload['implementation_lock'] != _implementation_lock(sim.registry, sim.field_backend):
            _reject('Checkpoint source/catalog/environment lock mismatch')
        index, time = payload['frame_index'], _number(payload['time_s'], 'time')
        if type(index) is not int or index < 0 or (index == 0) != (time == 0):
            _reject('Frame index and clock disagree')
        last_dt = _number(payload['last_dt_s'], 'last dt')
        if (index == 0) != (last_dt == 0) or last_dt > time:
            _reject('Last interval disagrees with current clock')
        if index == 0:
            if _hash(payload) != _hash(export_checkpoint(sim, binary=isinstance(payload['local_fields']['blocked'], np.ndarray))):
                _reject('Frame-zero checkpoint differs from explicit project initialization')
            return sim
        _keys(payload['world'], sim.world.groups, 'world')
        groups = {}
        for gid, raw in payload['world'].items():
            _keys(raw, {'ids', 'positions_um', 'orientation_xyzw', 'geometry'}, 'population')
            if type(raw['ids']) is not list or any(type(cid) is not str or not cid for cid in raw['ids']):
                _reject('Invalid living cell IDs')
            count = len(raw['ids'])
            if type(raw['geometry']) is not list or len(raw['geometry']) != count:
                _reject('Geometry/ID length mismatch')
            geometry = []
            for entry in raw['geometry']:
                _keys(entry, {'length_um', 'diameter_um'}, 'capsule geometry')
                geometry.append(CapsuleGeometry(_number(entry['length_um'], 'length'), _number(entry['diameter_um'], 'diameter')))
            groups[gid] = CellGroup(gid, tuple(raw['ids']), _array(raw['positions_um'], (count, 3), 'positions'),
                _array(raw['orientation_xyzw'], (count, 4), 'orientations'), tuple(geometry))
        from .spatial_runtime import MAX_CELLS
        if sum(len(g.ids) for g in groups.values()) > MAX_CELLS:
            _reject('Population budget exceeded')
        world = World(sim.world.grid, groups, sim.world.species_initial_uM, sim.world.schedules)
        validator = _restore_validator(sim, payload['frame_validator'], world, index, time)
        materials, material_ledger = _restore_materials(sim, payload['materials'], payload['material_ledger'], index)
        fields = _restore_fields(sim, payload['local_fields'], index, materials)
        sim.obstacles, _ = sim._geometry_for(materials)
        capsules = sim._capsules(world)
        sim._guard(capsules, capsules)
        ids = {cid for g in groups.values() for cid in g.ids}
        _keys(payload['walks'], ids, 'walks')
        walks = {cid: HazardWalkState.from_dict(value) for cid, value in payload['walks'].items()}
        for gid, group in groups.items():
            heading = np.asarray([walks[c].heading for c in group.ids]).reshape(-1, 3)
            if not np.allclose(heading, heading_from_orientation(group.orientation_xyzw), atol=1e-10, rtol=0):
                _reject('Walk heading differs from pose')
            node = sim._motion_nodes[gid]
            for cid in group.ids:
                walk = walks[cid]
                if node.parameters['tumble_mode'].value == 'instant' and walk.phase != 'run':
                    _reject('Instant mode has a dwell phase')
                if walk.dwell_remaining_s > node.parameters['tumble_duration_s'].value:
                    _reject('Dwell exceeds configured duration')
        streams = RandomStreams.from_dict(payload['random_streams'])
        if streams.run_seed != sim.seed:
            _reject('Random seed disagrees with execution seed')
        initial_streams = RandomStreams(sim.seed)
        allowed = {'motion.hazard_run_tumble': {'run_hazard', 'tumble_direction'},
                   'life.health_balance': {'death'}, 'life.starvation_hazard': {'death'}, 'division.area_adder': {'division_fraction'}}
        for entry in payload['random_streams']['streams']:
            nid, gid, cid, purpose = entry['key']
            node = sim.plan.by_id.get(nid)
            purposes = allowed.get(node.module_id, set()) if node is not None else set()
            if node is not None and node.module_id == 'division.area_adder' and node.module_version == '2.0.0':
                purposes = purposes | {'division_threshold'}
            if node is None or gid != node.owner_id or cid not in validator.seen or purpose not in purposes:
                _reject('Foreign RNG namespace')
            increment = initial_streams.stream(nid, gid, cid, purpose).bit_generator.state['state']['inc']
            if entry['state']['state']['inc'] != format(increment, '032x'):
                _reject('RNG namespace increment differs from seed')
        namespaces = {tuple(entry['key']) for entry in payload['random_streams']['streams']}
        for node in sim.plan.nodes:
            if node.module_id == 'division.area_adder' and node.module_version == '2.0.0' and node.parameters['area_cv'].value > 0:
                for cid in groups[node.owner_id].ids:
                    if (node.id, node.owner_id, cid, 'division_threshold') not in namespaces:
                        _reject('Cycle threshold is missing its RNG stream')
        for gid, group in groups.items():
            node = sim._motion_nodes[gid]
            for cid in group.ids:
                walk = walks[cid]
                if (walk.remaining_hazard is not None or walk.phase == 'tumble') and (node.id, gid, cid, 'run_hazard') not in namespaces:
                    _reject('Pending hazard is missing its RNG stream')
                if walk.phase == 'tumble' and (node.id, gid, cid, 'tumble_direction') not in namespaces:
                    _reject('Dwell phase is missing its turn RNG stream')
        outputs, state = _nested(sim, payload['outputs'], world, 'outputs'), _nested(sim, payload['state'], world, 'state')
        supplies, uptakes = payload['supply_totals'], payload['uptake_totals']
        _keys(supplies, sim.supply_totals, 'supplies')
        _keys(uptakes, sim.uptake_totals, 'uptakes')
        for key, value in {**supplies, **uptakes}.items():
            _number(value, key)
        if type(payload['next_cell_index']) is not int or payload['next_cell_index'] < 0:
            _reject('Invalid next child ID index')
        initial_ids = {cid for group in project['groups'].values() for cid in group['ids']}
        for cid in validator.seen - initial_ids:
            parent, separator, serial = cid.rpartition('__child_')
            if not separator or parent not in validator.seen or not serial.isascii() or not serial.isdecimal() or int(serial) >= payload['next_cell_index']:
                _reject('Child ID history disagrees with monotone division IDs')
        if type(payload['dead_material']) is not dict or set(payload['dead_material']) != validator.seen - ids:
            _reject('Dead residual history must account for all departed cells')
        for cid, entry in payload['dead_material'].items():
            _keys(entry, {'time_s', 'group_id', 'residual_molecules', 'removed_copies', 'death_rule'}, 'dead residual')
            if entry['group_id'] not in groups or not 0 < _number(entry['time_s'], 'death time') <= time:
                _reject('Invalid dead identity/time')
            if type(entry['residual_molecules']) is not dict or not set(entry['residual_molecules']).issubset(fields.concentrations_uM):
                _reject('Invalid residual species')
            for value in entry['residual_molecules'].values():
                _number(value, 'residual')
            _number(entry['removed_copies'], 'removed copies')
            rule = entry['death_rule']
            _keys(rule, {'node_id', 'module_id', 'policy', 'health', 'death_hazard_per_min', 'probability', 'random_draw'}, 'death rule')
            owner = sim.plan.by_id.get(rule['node_id'])
            if (owner is None or owner.module_id not in ('life.health_balance', 'life.starvation_hazard') or owner.module_id != rule['module_id'] or
                owner.owner_id != entry['group_id'] or (owner.parameters['policy'].value if owner.module_id == 'life.health_balance' else 'reserve_starvation') != rule['policy']):
                _reject('Death rule differs from declared health owner')
            if (not 0 <= _number(rule['health'], 'death health') <= 1 or
                not 0 < _number(rule['random_draw'], 'death draw') < _number(rule['probability'], 'death probability') <= 1
                or _number(rule['death_hazard_per_min'], 'death hazard') <= 0):
                _reject('Invalid biological death trigger')
        from .chemotaxis_runtime import STOCK, RESERVE
        expected_biology = {n.parameters['species'].value for n in sim.plan.nodes if n.module_id in STOCK}
        if type(payload['physiology_ledger']) is not dict or set(payload['physiology_ledger']) != expected_biology:
            _reject('Invalid physiological ledger')
        for species, values in payload['physiology_ledger'].items():
            reserve_species = {n.parameters['species'].value for n in sim.plan.nodes if n.module_id == RESERVE}
            expected_keys = {'growth_consumed_molecules', 'removed_residual_molecules'} | ({'maintenance_consumed_molecules'} if species in reserve_species else set())
            _keys(values, expected_keys, 'physiology ledger')
            for value in values.values():
                _number(value, 'physiology amount')
            residual = math.fsum(d['residual_molecules'].get(species, 0.) for d in payload['dead_material'].values())
            if not math.isclose(residual, values['removed_residual_molecules'], rel_tol=1e-12, abs_tol=1e-12):
                _reject('Removed residual ledger differs from dead cell accounting')
        raw_ledger, ledger = payload['ledger'], {}
        _keys(raw_ledger, (set(fields.concentrations_uM) - set(supplies)) if index else set(), 'field ledger')
        for species, value in raw_ledger.items():
            _keys(value, {f.name for f in dataclass_fields(FieldLedger)}, 'field ledger record')
            for key, number in value.items():
                _number(number, key, nonnegative=key != 'conservation_residual_molecules')
            if abs(value['conservation_residual_molecules']) > value['conservation_bound_molecules']:
                _reject('Field ledger exceeds conservation bound')
            ledger[species] = FieldLedger(**value)
        contacts = []
        if type(payload['motion_contacts']) is not list or (index == 0 and payload['motion_contacts']):
            _reject('Invalid collision diagnostics')
        targets = {n.owner_id for n in sim._fixed_obstacle_nodes} | {n.owner_id for n in sim._material_nodes.values()}
        for entry in payload['motion_contacts']:
            _keys(entry, {'cell_ids', 'kind', 'target_id', 'reason'}, 'contact')
            cids = entry['cell_ids']
            if type(cids) is not list or not cids or len(set(cids)) != len(cids) or not set(cids).issubset(validator.seen):
                _reject('Foreign collision cell ID')
            kind, target = entry['kind'], entry['target_id']
            if not ((kind == 'wall' and len(cids) == 1 and target == 'domain') or
                    (kind == 'obstacle' and len(cids) == 1 and target in targets) or
                    (kind == 'cell' and len(cids) == 2 and target == cids[1])):
                _reject('Invalid collision target')
            if entry['reason'] not in ('collision', 'numerically_uncertain', 'budget_exhausted'):
                _reject('Invalid collision reason')
            contacts.append(Contact(tuple(cids), kind, target, entry['reason']))
        sim.world, sim.fields, sim.materials, sim.material_ledger = world, fields, materials, material_ledger
        sim.walks, sim.streams = MappingProxyType(walks), streams
        sim.outputs, sim.state = _freeze(outputs), _freeze(state)
        sim.supply_totals, sim.uptake_totals = dict(supplies), dict(uptakes)
        sim.physiology_ledger, sim.dead_material = deepcopy(payload['physiology_ledger']), deepcopy(payload['dead_material'])
        sim.next_cell_index, sim.frame_validator = payload['next_cell_index'], validator
        sim.time_s, sim.frame_index = time, index
        sim.last_dt_s = last_dt
        sim.ledger, sim.motion_contacts = MappingProxyType(ledger), tuple(contacts)
        _coherence(sim)
        _validate_material_balance(sim, initial_field_amounts)
        current = sim._snapshot(world, fields, sim.outputs, time, index, materials)
        current.cell_frame['events'] = deepcopy(payload['current_frame']['events'])
        if _hash(current.cell_frame) != _hash(payload['current_frame']):
            _reject('Frame differs from restored owners')
        # Reverse this boundary's supported events, then validate them against
        # the saved complete ID history and last integration interval.
        checker = FrameSequenceValidator(sim.run)
        if index == 0:
            checker.accept(current.cell_frame)
        else:
            checker.run_id, checker.frame_version = sim.run['run_id'], '0.2.0'
            checker.next_index, checker.previous_time = index, time - last_dt
            checker.alive, checker.seen = dict(validator.alive), set(validator.seen)
            for event in reversed(current.cell_frame['events']):
                if event['type'] == 'division':
                    child, parent = event['child_id'], event['parent_id']
                    if child not in checker.alive or parent not in checker.alive:
                        _reject('Division event disagrees with live daughter state')
                    del checker.alive[child]
                    checker.seen.remove(child)
                elif event['type'] == 'death':
                    cid = event['cell_id']
                    if cid in checker.alive or cid not in sim.dead_material:
                        _reject('Death event disagrees with removed residual owner')
                    record = sim.dead_material[cid]
                    if record['time_s'] != time:
                        _reject('Death event time differs from residual ledger')
                    probability = -math.expm1(-record['death_rule']['death_hazard_per_min'] * last_dt / 60)
                    if probability != record['death_rule']['probability']:
                        _reject('Death probability differs from interval hazard')
                    checker.alive[cid] = record['group_id']
                else:
                    _reject('This profile does not support independent birth events')
            checker.accept(current.cell_frame)
        if _validator_record(checker) != _validator_record(validator):
            _reject('Current events disagree with saved frame history')
        if _hash(payload['object_states']) != _hash({k: dict(v) for k, v in current.object_states.items()}):
            _reject('Visible material/source inventory differs from owners')
        sim.observation_state = validate_observation_state(payload['observation_state'], project, current.cell_frame)
        sim.current = sim._with_metrics(current, sim.observation_state)
        from dataclasses import replace
        details = {'lifecycle_version': '0.1.0', 'deaths': [
            {'cell_id': event['cell_id'], 'time_s': sim.dead_material[event['cell_id']]['time_s'],
             'group_id': sim.dead_material[event['cell_id']]['group_id'], **sim.dead_material[event['cell_id']]['death_rule']}
            for event in current.cell_frame['events'] if event['type'] == 'death']}
        if details != payload['lifecycle_details']:
            _reject('Lifecycle details differ from recorded death rules')
        sim.current = replace(sim.current, lifecycle_details=details)
        if _hash(sim.current.metrics) != _hash(payload['metrics']):
            _reject('Metrics differ from cohort state')
        return sim
    except SimulationError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise SimulationError('chemotaxis.checkpoint', f'Invalid checkpoint: {error}') from error


def _validate_material_balance(sim, initial_field_amounts):
    """Check cumulative owners, including uptake from cells that later died."""
    from .chemotaxis_runtime import STOCK
    for species in sim.fields.concentrations_uM:
        initial_amount = initial_field_amounts[species]
        source_initial = math.fsum(n.parameters['initial_molecules'].value for n in sim._source_nodes.values() if n.parameters['species'].value == species)
        material_initial = math.fsum(n.parameters['initial_molecules'].value for n in sim._material_nodes.values() if n.parameters['species'].value == species)
        now = math.fsum(sim.fields.concentrations_uM[species]) * sim.world.grid.molecules_per_uM_voxel
        now += math.fsum(s.remaining_molecules for s in sim.fields.sources if s.species == species)
        now += math.fsum(m.remaining_molecules for m in sim.materials.values() if m.species == species)
        uptake = math.fsum(sim.uptake_totals[n.id] for n in sim.plan.nodes if n.module_id == 'uptake.local_settlement' and n.parameters['species'].value == species)
        expected = initial_amount + source_initial + material_initial + sim.supply_totals.get(species, 0.)
        if not math.isclose(now + uptake, expected, rel_tol=2e-11, abs_tol=1e-8):
            _reject('Cumulative material/field/uptake/supply balance disagrees')
    # Growth has its own initial available stock; statistics are never reused as stock.
    for species, record in sim.physiology_ledger.items():
        nodes = [n for n in sim.plan.nodes if n.module_id in STOCK and n.parameters['species'].value == species]
        initial = math.fsum(n.parameters['initial_molecules'].value * len(sim.project['groups'][n.owner_id]['ids']) for n in nodes)
        uptake = math.fsum(sim.uptake_totals[n.inputs['accepted_amount'].source_node] for n in nodes)
        living = math.fsum(float(v) for n in nodes for key in ('intracellular_molecules', 'reserve_correction_molecules')
                           for v in sim.state[n.id].get(key, ()))
        total = living + record['growth_consumed_molecules'] + record['removed_residual_molecules'] + record.get('maintenance_consumed_molecules', 0.)
        if not math.isclose(total, initial + uptake, rel_tol=2e-11, abs_tol=1e-8):
            _reject('Intracellular/growth/death material balance disagrees')
