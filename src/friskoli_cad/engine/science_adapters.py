"""Executable adapters: each registered mechanism owns its numerical proposal.

The modular runner dispatches contracts, never module identifiers. Existing
profile implementations and their numerical timing are left unchanged.
"""
from copy import deepcopy
from dataclasses import fields
import math
import numpy as np

from friskoli_cad.science import pts, chemotaxis, physiology, survival, processes
from .module_api import Effect, ModuleProposal, thaw
from .science_extensions import ScientificModule


def parameters(context, cls):
    return cls(**{item.name: context.parameters[item.name.lower()] for item in fields(cls)})


def field_owner(c, initial):
    p = c.parameters
    value = np.asarray(c.world['fields'][p['species']]).reshape(c.world['grid_shape_zyx'])
    effects = []
    if initial:
        value = value.copy(); z, y, x = np.indices(value.shape)
        for xyz, coordinate in enumerate((x, y, z)):
            spacing = c.world['spacing_xyz'][xyz]
            value += p.get('gradient_' + 'xyz'[xyz] + '_um_per_um', 0.) * ((coordinate + .5) * spacing - value.shape[2 - xyz] * spacing / 2)
        value[np.asarray(c.world['blocked'], bool).reshape(value.shape)] = 0.
        if np.any(value < 0): raise ValueError('initial gradient produces negative fluid concentration')
        effects = [Effect('field.diffusivity', p['species'], p['diffusivity_um2_s']), Effect('field.initial', p['species'], value)]
    return ModuleProposal({'concentration': value}, {'concentration': value}, tuple(effects))


def capsule_area(c, initial):
    return ModuleProposal({'surface_area': pts.capsule_area_um2(c.world['length_um'], c.world['diameter_um'])}, {})


def sample_local(c, initial):
    field = np.asarray(c.inputs['field'])
    positions = np.asarray(c.world['positions_um'])
    index = np.floor(positions / np.asarray(c.world['spacing_xyz'])).astype(int)
    if np.any(index < 0) or np.any(index >= np.asarray(field.shape[::-1])):
        raise ValueError('sampler position outside field')
    values = field[index[:, 2], index[:, 1], index[:, 0]]
    blocked = np.asarray(c.world['blocked'], bool).reshape(field.shape)
    if np.any(blocked[index[:, 2], index[:, 1], index[:, 0]]):
        raise ValueError('cell sampling in solid obstacle is unsupported')
    gradient = np.empty((len(index), 3), float)
    dimensions = np.asarray(field.shape[::-1])
    # Reflective ghost values at walls/blocked faces, exactly as the shared
    # local-field sampler. Allocate only per-cell neighbors, never full gradients.
    for axis in range(3):
        neighbors = []
        for direction in (-1, 1):
            neighbor = index.copy(); neighbor[:, axis] += direction
            valid = (neighbor[:, axis] >= 0) & (neighbor[:, axis] < dimensions[axis])
            neighbor[:, axis] = np.clip(neighbor[:, axis], 0, dimensions[axis] - 1)
            valid &= ~blocked[neighbor[:, 2], neighbor[:, 1], neighbor[:, 0]]
            neighbors.append(np.where(valid, field[neighbor[:, 2], neighbor[:, 1], neighbor[:, 0]], values))
        gradient[:, axis] = (neighbors[1] - neighbors[0]) / (2 * c.world['spacing_xyz'][axis])
    return ModuleProposal({'concentration': values, 'gradient': gradient}, {})



def capacity(rebuilt):
    def evaluate(c, initial):
        cls, fn = (pts.RebuiltCapacityParameters, pts.rebuilt_capacity) if rebuilt else (pts.SimplifiedCapacityParameters, pts.simplified_capacity)
        value = fn(c.inputs['surface_area'], c.parameters['g_requested'], parameters(c, cls))
        return ModuleProposal({'functional_copies': value.functional_copies, 'g_effective': value.g_effective}, {})
    return evaluate


def uptake_request(pts_specific):
    def evaluate(c, initial):
        p = c.parameters
        copies = c.inputs['functional_copies'] if pts_specific else p['maximum_flux_molecules_s']
        turnover = p['turnover_s'] if pts_specific else 1.
        return ModuleProposal({'requested_flux': pts.pts_request(c.inputs['concentration'], copies, turnover, p['half_saturation_um'])}, {})
    return evaluate


def uptake_settlement(c, initial):
    zeros = np.zeros(len(c.entity_ids))
    cumulative = np.full(len(c.entity_ids), c.parameters['initial_molecules'], dtype=float) if initial else np.asarray(c.state['cumulative_uptake'])
    outputs = {'accepted_amount': zeros, 'accepted_flux': zeros, 'cumulative_uptake': cumulative}
    return ModuleProposal(outputs, {'cumulative_uptake': cumulative}, () if initial else (Effect('field.uptake', c.parameters['species'], {
        'cell_ids': c.entity_ids, 'positions_um': c.world['positions_um'], 'requested_amount': np.asarray(c.inputs['requested_flux']) * c.dt_s}),))


def constant_bias(c, initial):
    return ModuleProposal({'motor_bias': np.full(len(c.entity_ids), c.parameters['bias'], dtype=float)}, {})


def pts_signal(c, initial):
    p, count = c.parameters, len(c.entity_ids)
    par = parameters(c, pts.SignalParameters)
    value = pts.signal_readout(np.full(count, p['initial_ei_fraction'], dtype=float), np.full(count, p['initial_chey_p_um'], dtype=float), par) if initial else pts.advance_accepted_signal(c.inputs['accepted_flux'], c.state['ei_fraction'], c.state['chey_p'], c.dt_s, par)
    return ModuleProposal({'ei_fraction': value.ei_fraction, 'chey_p': value.chey_p_uM,
        'chea_active': value.chea_active_uM, 'motor_bias': value.motor_bias}, {'ei_fraction': value.ei_fraction, 'chey_p': value.chey_p_uM})


def concentration_memory(c, initial):
    p = c.parameters; concentration = c.inputs['concentration']
    memory = np.full(len(c.entity_ids), p['initial_memory_um'], dtype=float) if initial else chemotaxis.advance_concentration_memory(c.state['memory'], concentration, c.dt_s, memory_tau_s=p['memory_tau_s'])
    bias = chemotaxis.rebuilt_motor_bias(c.inputs['motor_bias'], memory, concentration, gradient_strength_per_uM=p['gradient_strength_per_um'])
    return ModuleProposal({'memory': memory, 'motor_bias': bias}, {'memory': memory})


def chey_memory(c, initial):
    p = c.parameters; chey = c.inputs['chey_p']
    memory = np.full(len(c.entity_ids), p['initial_memory_um'], dtype=float) if initial else chemotaxis.advance_chey_memory(c.state['memory'], chey, c.dt_s, adaptation_tau_s=p['adaptation_tau_s'])
    effective = chemotaxis.adapted_chey_signal(chey, memory, baseline_uM=p['baseline_um'], total_uM=p['total_um'])
    bias = chemotaxis.motor_bias(effective, half_uM=p['motor_half_um'], hill=p['motor_hill'])
    return ModuleProposal({'memory': memory, 'effective_chey': effective, 'motor_bias': bias}, {'memory': memory})


def mcp(c, initial):
    p, ligand = c.parameters, c.inputs['concentration']
    par = parameters(c, chemotaxis.MWCParameters)
    old = chemotaxis.mcp_adapted_methylation(ligand, par) if initial else c.state['adaptation']
    value = chemotaxis.advance_mcp_adaptation(ligand, old, c.dt_s, par)
    chey = chemotaxis.advance_chey_from_activity(value.activity, np.full(len(c.entity_ids), p['initial_chey_p_um'], dtype=float) if initial else c.state['chey_p'], c.dt_s, parameters(c, chemotaxis.CheYParameters))
    return ModuleProposal({'activity': value.activity, 'adaptation': value.methylation, 'chey_p': chey,
        'motor_bias': chemotaxis.motor_bias(chey, half_uM=p['motor_half_um'], hill=p['motor_hill'])}, {'adaptation': value.methylation, 'chey_p': chey})


def reserve(c, initial):
    count, p = len(c.entity_ids), c.parameters
    before = np.full(count, p['initial_molecules'], dtype=float) if initial else c.state['intracellular_molecules']
    correction = np.zeros(count) if initial else c.state['reserve_correction_molecules']
    value = processes.advance_compensated_reserve(before, np.zeros(count) if initial else c.inputs['accepted_amount'], c.dt_s,
                                    p['maintenance_molecules_s'], correction)
    return ModuleProposal({'intracellular_molecules': value.reserve_molecules, 'used_molecules': value.used_molecules,
        'unmet_duration_s': value.unmet_duration_s}, {'intracellular_molecules': value.reserve_molecules,
        'reserve_correction_molecules': value.correction_molecules},
        () if initial else (Effect('inventory.consumption', c.owner_id, {'species': p['species'], 'maintenance': float(np.sum(value.used_molecules)), 'growth': 0.}),))


def starvation(c, initial):
    p = c.parameters; count = len(c.entity_ids)
    value = survival.advance_starvation(np.zeros(count) if initial else c.state['starvation_time_s'],
        np.zeros(count) if initial else c.inputs['unmet_duration_s'], c.dt_s,
        grace_s=p['grace_s'], recovery_rate=p['recovery_rate'], death_rate_per_min=p['death_rate_per_min'])
    deaths = []
    if not initial:
        for i, cid in enumerate(c.entity_ids):
            probability = -math.expm1(-float(value.death_hazard_per_min[i]) * c.dt_s / 60)
            if probability and c.rng.stream(c.node_id, c.owner_id, cid, 'death').uniform_open() < probability:
                deaths.append(cid)
    return ModuleProposal({'starvation_time_s': value.starvation_time_s, 'health': value.health,
        'death_hazard': value.death_hazard_per_min}, {'starvation_time_s': value.starvation_time_s},
        (Effect('lifecycle.death', c.owner_id, {'ids': deaths}),) if deaths else ())


def motion(c, initial):
    from .hazard_walk import HazardWalkState, HazardWalkParameters, advance_hazard_walk
    positions, headings = np.asarray(c.world['positions_um']), np.asarray(c.world['headings'])
    p = c.parameters
    paths, walks, turn_count = [], [], []
    old = c.state.get('walks', ())
    bias = np.asarray(c.inputs.get('motor_bias', np.full(len(c.entity_ids), .25)))
    for i, cid in enumerate(c.entity_ids):
        walk = HazardWalkState(tuple(headings[i])) if initial else HazardWalkState.from_dict(thaw(old[i]))
        hazard = p['minimum_tumble_rate_s'] + (p['maximum_tumble_rate_s'] - p['minimum_tumble_rate_s']) * float(bias[i])
        par = HazardWalkParameters(p['speed_um_s'], hazard, 2 if c.world['geometry'] == 'thin_layer' else 3,
                                  p['tumble_mode'], p['tumble_duration_s'] if p['tumble_mode'] == 'dwell' else 0., p['turn_kernel'])
        proposal = advance_hazard_walk(positions[i], walk, c.dt_s, par, c.rng, node_id=c.node_id, group_id=c.owner_id, cell_id=cid, max_events=2048)
        walks.append(proposal.state.to_dict()); turn_count.append(proposal.event_count)
        paths.append({'id': cid, 'speed_um_s': p['speed_um_s'], 'segments': [
            {'start_s': s.start_s, 'end_s': s.end_s, 'phase': s.phase, 'heading': s.heading} for s in proposal.segments],
            'final_heading': proposal.state.heading})
    outputs = {'position': positions, 'heading': headings, 'blocked': np.zeros(len(positions)), 'turns': np.asarray(turn_count, float),
               'tumble_phase': np.asarray([float(w['phase'] == 'tumble') for w in walks]),
               'hazard_remaining': np.asarray([w['remaining_hazard'] or 0. for w in walks])}
    state = {'heading': headings, 'remaining_hazard': outputs['hazard_remaining'],
             'dwell_remaining': np.asarray([w['dwell_remaining_s'] for w in walks]), 'walks': walks}
    return ModuleProposal(outputs, state, () if initial else (Effect('motion.paths', c.owner_id, paths),))


def finite_source(c, initial):
    p = c.parameters; stock = p['initial_molecules'] if initial else float(c.state['inventory'])
    amount = min(stock, p['release_rate'] * c.dt_s)
    shape = tuple(c.world['grid_shape_zyx']); z, y, x = np.indices(shape)
    centers = np.stack(((x + .5) * c.world['spacing_xyz'][0], (y + .5) * c.world['spacing_xyz'][1], (z + .5) * c.world['spacing_xyz'][2]), axis=-1)
    center = np.array([p['center_' + axis + '_um'] for axis in 'xyz'])
    gap = np.maximum(np.abs(centers - center) - np.asarray(c.world['spacing_xyz']) / 2, 0.)
    mask = (np.linalg.norm(gap, axis=-1) <= p['radius_um']) & ~np.asarray(c.world['blocked'], bool).reshape(shape)
    if amount and not mask.any(): raise ValueError('finite source has no fluid support at this grid')
    delta = mask.astype(float) * amount / max(1, int(mask.sum()))
    return ModuleProposal({'inventory': stock - amount}, {'inventory': stock - amount},
                         (Effect('field.delta', p['species'], delta),))


def box_geometry(c, initial):
    p = c.parameters; lower = [p['lower_' + a + '_um'] for a in 'xyz']; upper = [p['upper_' + a + '_um'] for a in 'xyz']
    return ModuleProposal({'volume': float(np.prod(np.asarray(upper) - lower))}, {},
        (Effect('geometry.obstacle', c.owner_id, {'kind': 'box', 'lower_um': lower, 'upper_um': upper, 'active': True}),))


def material_box(c, initial):
    p = c.parameters; inventory = float(p['initial_molecules'] if initial else c.state['inventory'])
    lower = [p['lower_' + a + '_um'] for a in 'xyz']; upper = [p['upper_' + a + '_um'] for a in 'xyz']
    return ModuleProposal({'inventory': inventory}, {'inventory': inventory},
        (Effect('geometry.obstacle', c.owner_id, {'kind': 'box', 'lower_um': lower, 'upper_um': upper, 'active': inventory > 0}),))


def surface_enzyme(c, initial):
    return ModuleProposal({'enzyme_copies': np.full(len(c.entity_ids), c.parameters['enzyme_copies'], dtype=float)}, {})


def contact_degradation(source_b=False):
    def evaluate(c, initial):
        from .collision import Capsule, BoxObstacle, capsule_box_gap
        materials = c.world['materials']; cells = c.world['all_cells']; enzymes = c.world['surface_enzymes']
        contacts = {}
        for mid, record in materials.items():
            box = BoxObstacle(mid, tuple(record['lower_um']), tuple(record['upper_um']))
            contacts[mid] = [cid for cid, cell in cells.items() if capsule_box_gap(Capsule(cid, cell['position_um'], cell['heading'], cell['length_um'], cell['diameter_um']), box) <= c.parameters['contact_range_um']]
        memberships = {cid: sum(cid in ids for ids in contacts.values()) for cid in cells}
        transfers = []
        if not initial:
            for mid, record in materials.items():
                copies = sum(enzymes.get(cid, 0.) / memberships[cid] for cid in contacts[mid])
                rate = c.parameters['kcat_s']
                if source_b: rate *= record['inventory'] / record['initial_molecules'] if record['initial_molecules'] else 0.
                amount = min(record['inventory'], copies * rate * c.dt_s)
                if amount:
                    positions = [cells[cid]['position_um'] for cid in contacts[mid]]
                    group_copies = {}
                    for cid in contacts[mid]:
                        gid = cells[cid]['group_id']
                        group_copies[gid] = group_copies.get(gid, 0.) + enzymes.get(cid, 0.) / memberships[cid]
                    transfers.append({'material_node': mid, 'species': record['species'], 'amount': amount, 'positions_um': positions,
                        'contributions_by_group': {gid: amount * value / copies for gid, value in group_copies.items()} if copies else {}})
        return ModuleProposal({'released_amount': sum(t['amount'] for t in transfers)}, {},
                              (Effect('material.release', c.owner_id, transfers),) if transfers else ())
    return evaluate


def adapted_modules():
    import json
    from importlib.resources import files
    declarations = json.loads(files("friskoli_cad.engine").joinpath("declarations", "science.json").read_text(encoding="utf-8"))
    specifications = {
        'field.diffusive_local': ('prepare', field_owner, ('fields', 'grid_shape_zyx', 'spacing_xyz', 'blocked'), ('field.diffusivity', 'field.initial')),
        'pts.capsule_area': ('prepare', capsule_area, ('length_um', 'diameter_um'), ()),
        'field.sample_local': ('prepare', sample_local, ('positions_um', 'spacing_xyz', 'blocked'), ()),
        'pts.capacity_rebuilt': ('prepare', capacity(True), (), ()),
        'pts.capacity_simplified': ('prepare', capacity(False), (), ()),
        'uptake.pts_request': ('prepare', uptake_request(True), (), ()),
        'uptake.saturating_request': ('prepare', uptake_request(False), (), ()),
        'uptake.local_settlement': ('field', uptake_settlement, ('positions_um',), ('field.uptake',)),
        'signal.constant_bias': ('physiology', constant_bias, (), ()),
        'signal.pts_accepted': ('physiology', pts_signal, (), ()),
        'signal.concentration_memory': ('physiology', concentration_memory, (), ()),
        'signal.chey_memory': ('physiology', chey_memory, (), ()),
        'signal.mcp_adaptation': ('physiology', mcp, (), ()),
        'metabolism.reserve_balance': ('physiology', reserve, (), ('inventory.consumption',)),
        'life.starvation_hazard': ('physiology', starvation, (), ('lifecycle.death',)),
        'motion.hazard_run_tumble': ('physiology', motion, ('positions_um', 'headings', 'geometry'), ('motion.paths',)),
        'source.finite_local': ('field', finite_source, ('grid_shape_zyx', 'spacing_xyz', 'blocked'), ('field.delta',)),
        'space.axis_aligned_obstacle': ('prepare', box_geometry, (), ('geometry.obstacle',)),
        'material.degradable_box': ('prepare', material_box, (), ('geometry.obstacle',)),
        'surface.enzyme_activity': ('prepare', surface_enzyme, (), ()),
        'reaction.contact_degradation': ('field', contact_degradation(), ('materials', 'all_cells', 'surface_enzymes'), ('material.release',)),
        'reaction.direct_bulk_hydrolysis': ('field', contact_degradation(True), ('materials', 'all_cells', 'surface_enzymes'), ('material.release',)),
    }
    result = []
    for declaration in declarations:
        identifier = declaration["manifest"]["id"]
        if identifier not in specifications: continue
        stage, function, reads, effects = specifications[identifier]
        manifest = deepcopy(declaration["manifest"])
        manifest['protocol_version'] = '0.2.0'
        if identifier == 'motion.hazard_run_tumble':
            manifest['state']['walks'] = {'shape': 'cell.record', 'unit': '1', 'on_division': 'copy'}
        module = ScientificModule(identifier, declaration['declaration']['label'], stage, manifest['scope'], manifest['inputs'],
            manifest['outputs'], manifest['parameters'], manifest['state'],
            declaration['declaration']['mathematics']['equations'][0]['latex'], manifest['description'], function,
            reads=reads, effects=effects)
        module.execution_contract['uses_rng'] = identifier in ('motion.hazard_run_tumble', 'life.starvation_hazard')
        module.execution_contract['rng_purposes'] = ['run_hazard', 'tumble_direction'] if identifier == 'motion.hazard_run_tumble' else ['death'] if identifier == 'life.starvation_hazard' else []
        module.manifest = manifest
        if identifier == 'pts.capsule_area': module.refresh_after_lifecycle = True
        module.provides_roles = list(declaration['roles'])
        module.default_parameters = deepcopy(declaration['default_parameters'])
        module.object_types = deepcopy(declaration['object_types'])
        if identifier == 'field.diffusive_local': module.provides_roles += ['field.owner']
        if identifier == 'motion.hazard_run_tumble': module.provides_roles += ['motion.owner']
        if identifier == 'metabolism.reserve_balance': module.provides_roles += ['inventory.owner']
        if identifier in ('source.finite_local', 'material.degradable_box'):
            module.snapshot_object_type = 'source.attractant' if identifier == 'source.finite_local' else 'material.degradable_box'
            module.snapshot_outputs = {'remaining_molecules': 'inventory'}
            module.checkpoint_inventory = {'state': 'inventory', 'initial_parameter': 'initial_molecules', 'output': 'inventory'}
        if identifier == 'source.finite_local': module.effect_accounting = {'field.delta': 'internal_net'}
        if identifier == 'material.degradable_box': module.provides_roles += ['material.owner']
        if identifier == 'surface.enzyme_activity':
            module.provides_roles += ['enzyme.surface']
            module.catalyst_resource = ('surface_enzyme', '$owner')
        if identifier in ('reaction.contact_degradation', 'reaction.direct_bulk_hydrolysis'):
            module.consumes_catalyst_role = 'enzyme.surface'
        for spec in module.manifest['state'].values():
            spec['on_migration'] = 'conservative_regrid' if spec['shape'] == 'field.scalar' else 'copy'
            spec['on_death'] = 'discard'
            if spec['shape'].startswith('cell.') and spec.get('unit') in ('molecule', 'um^3'):
                spec['on_division'] = 'split'
        result.append(module)
    return result
