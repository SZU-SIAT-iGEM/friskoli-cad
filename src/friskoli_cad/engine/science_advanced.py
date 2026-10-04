"""Optional material, lifecycle and observation mechanisms, ordinary modules."""
import hashlib
import math
import numpy as np

from friskoli_cad.science import processes as law, physiology
from .module_api import Effect, ModuleProposal, thaw
from .declarations import number, port, SPECIES
from .science_extensions import ScientificModule, array_parameter, RECORD, RECORD_PORT, AMOUNT, CELL, SCALAR, BOX, GRID_READS


def shared_inventory(c, initial):
    p = c.parameters; count = len(c.entity_ids)
    length, diameter = np.asarray(c.world['length_um']), np.asarray(c.world['diameter_um'])
    volume = physiology.capsule_volume_um3(length, diameter)
    reserve = np.full(count, p['initial_molecules'], dtype=float) if initial else c.state['intracellular_molecules']
    accepted = np.zeros(count) if initial else c.inputs['accepted_amount']
    if p['volume_yield_um3_molecule'] <= 0: raise ValueError('volume yield must be positive')
    demand = volume * math.expm1(p['max_growth_per_min'] * c.dt_s / 60) / p['volume_yield_um3_molecule']
    balance = law.advance_compensated_reserve(reserve, accepted, c.dt_s, p['maintenance_molecules_s'],
        np.zeros(count) if initial else c.state['reserve_correction_molecules'])
    maintenance, unmet = balance.used_molecules, balance.unmet_duration_s
    growth = np.minimum(balance.reserve_molecules, demand)
    # Rounded high stock may lie slightly above the exact two-component stock.
    growth = np.where((growth == balance.reserve_molecules) & (balance.correction_molecules < 0), np.nextafter(growth, 0.), growth)
    remaining, correction = law.compensated_amount(balance.reserve_molecules, balance.correction_molecules, -growth)
    outputs = {'intracellular_molecules': remaining, 'volume': volume + growth * p['volume_yield_um3_molecule'],
        'used_molecules': growth, 'maintenance_used': maintenance, 'unmet_duration_s': unmet,
        'growth_rate': np.log1p(growth * p['volume_yield_um3_molecule'] / volume) * (60 / c.dt_s if c.dt_s else 0.),
        'blocked': np.zeros(count)}
    effects = () if initial else (Effect('geometry.growth', c.owner_id, {'volume_delta': growth * p['volume_yield_um3_molecule'],
        'consumed': growth, 'species': p['species'], 'yield_um3_molecule': p['volume_yield_um3_molecule']}),
        Effect('inventory.consumption', c.owner_id, {'species': p['species'], 'maintenance': float(maintenance.sum()), 'growth': 0.}))
    return ModuleProposal(outputs, {'intracellular_molecules': remaining, 'reserve_correction_molecules': correction, 'volume': outputs['volume']}, effects)


def seeded_distribution(c, initial):
    p = c.parameters
    if p['spread'] < 0 or p['minimum'] > p['maximum']: raise ValueError('invalid distribution bounds')
    if not initial: return ModuleProposal({'value': c.state['value']}, c.state)
    values = []
    for cid in c.entity_ids:
        digest = hashlib.sha256(f"{p['seed']}\0{c.owner_id}\0{cid}".encode()).digest()
        rng = np.random.Generator(np.random.PCG64(int.from_bytes(digest[:16], 'little')))
        if p['distribution'] == 'uniform': value = rng.uniform(p['mean'] - p['spread'], p['mean'] + p['spread'])
        elif p['distribution'] == 'normal': value = rng.normal(p['mean'], p['spread'])
        else: raise ValueError('unknown distribution')
        values.append(np.clip(value, p['minimum'], p['maximum']))
    value = np.asarray(values, float)
    return ModuleProposal({'value': value}, {'value': value})


def secretion(c, initial):
    p = c.parameters
    before = p['initial_copies'] if initial else c.state['copies']
    cells = sum(1 for v in c.world['all_cells'].values() if v['group_id'] == p['population_id'])
    rate = p['secretion_copies_cell_s'] * cells
    decay = p['turnover_s']
    copies = before if initial else (before + rate * c.dt_s if decay == 0 else before * math.exp(-decay * c.dt_s) + rate / decay * -math.expm1(-decay * c.dt_s))
    return ModuleProposal({'enzyme_copies': copies}, {'copies': copies})


def cellulose(c, initial):
    p = c.parameters
    before = p['initial_agu_units'] if initial else c.state['agu_units']
    converted = law.cellulose_hydrolysis(before, c.inputs['enzyme_copies'], p['turnover_s'], c.dt_s, route=p['route'])
    shape = tuple(c.world['grid_shape_zyx']); positions = np.asarray(p['release_position_um']).reshape(1, 3)
    weights = law.matched_trilinear_weights(positions, shape, c.world['spacing_xyz'])[0]
    delta = np.zeros(shape)
    blocked = np.asarray(c.world['blocked'], bool).reshape(-1)
    support = {i: w for i, w in weights.items() if not blocked[i]}
    if converted['product_molecules'] and not sum(support.values()): raise ValueError('cellulose release point has no fluid support')
    for i, w in support.items(): delta.flat[i] += converted['product_molecules'] * w / sum(support.values())
    water = (0. if initial else c.state['water_consumed']) + converted['water_consumed_molecules']
    return ModuleProposal({'inventory': converted['remaining_agu'], 'released_amount': converted['product_molecules'],
        'water_consumed': water, 'converted_amount': p['initial_agu_units'] - converted['remaining_agu'],
        'remaining_fraction': 0. if p['initial_agu_units'] == 0 else converted['remaining_agu'] / p['initial_agu_units'],
        'conversion': 0. if p['initial_agu_units'] == 0 else 1 - converted['remaining_agu'] / p['initial_agu_units']},
        {'agu_units': converted['remaining_agu'], 'water_consumed': water},
        (Effect('field.delta', p['species'], delta),))


def contact_enzyme(c, initial):
    from .collision import Capsule, BoxObstacle, capsule_box_gap
    p = c.parameters; box = BoxObstacle(c.node_id, tuple(p['lower_um']), tuple(p['upper_um']))
    total = 0.
    for i, cid in enumerate(c.entity_ids):
        capsule = Capsule(cid, c.world['positions_um'][i], c.world['headings'][i], c.world['length_um'][i], c.world['diameter_um'][i])
        if capsule_box_gap(capsule, box) <= p['contact_range_um']:
            total += p['copies_per_cell']
    return ModuleProposal({'enzyme_copies': total}, {})


def background_death(c, initial):
    probability = 0. if initial else -math.expm1(-c.parameters['hazard_s'] * c.dt_s)
    deaths = [cid for cid in c.entity_ids if probability and c.rng.stream(c.node_id, c.owner_id, cid, 'death').uniform_open() < probability]
    return ModuleProposal({'probability': np.full(len(c.entity_ids), probability, dtype=float)}, {},
                          (Effect('lifecycle.death', c.owner_id, {'ids': deaths}),) if deaths else ())


def erosion(c, initial):
    p = c.parameters; fraction = float(p['initial_fraction'] if initial else c.state['applied_fraction'] if c.dt_s == 0 else c.inputs['remaining_fraction'])
    if not 0 <= fraction <= 1: raise ValueError('erosion fraction must be within [0,1]')
    lower, upper = np.asarray(p['lower_um']), np.asarray(p['upper_um'])
    if np.any(lower >= upper): raise ValueError('erosion box bounds invalid')
    scale = fraction ** (1 / 3); center = (lower + upper) / 2
    new_lower, new_upper = center - (upper - lower) * scale / 2, center + (upper - lower) * scale / 2
    return ModuleProposal({'volume': float(np.prod(new_upper - new_lower))}, {'applied_fraction': fraction},
        (Effect('geometry.obstacle', c.owner_id, {'kind': 'box', 'lower_um': new_lower, 'upper_um': new_upper, 'active': fraction > 0}),))


def triangle_mesh(c, initial):
    p = c.parameters
    vertices, faces, volume = law.mesh_geometry(p['vertices_xyz'], p['faces'], scale_um=p['scale_um'])
    return ModuleProposal({'volume': volume}, {},
        (Effect('geometry.obstacle', c.owner_id, {'kind': 'mesh', 'vertices_xyz': vertices, 'faces': faces, 'active': True}),))


def adhesion(c, initial):
    p = c.parameters; count = len(c.entity_ids)
    distance = np.asarray(c.inputs['distance_um'])
    bound = np.zeros(count, bool) if initial else np.asarray(c.state['bound']) > .5
    if not initial:
        for i, cid in enumerate(c.entity_ids):
            rate = p['detachment_rate_s'] if bound[i] else p['attachment_rate_s'] if distance[i] <= p['capture_distance_um'] else 0.
            if rate and c.rng.stream(c.node_id, c.owner_id, cid, 'adhesion').uniform_open() < -math.expm1(-rate * c.dt_s): bound[i] = not bound[i]
    value = bound.astype(float)
    return ModuleProposal({'bound': value}, {'bound': value}, (Effect('motion.scale', c.owner_id, 1 - value),))


def wall_contact(c, initial):
    p = c.parameters; positions = np.asarray(c.world['positions_um'])
    extent = np.asarray(c.world['spacing_xyz']) * np.asarray(c.world['grid_shape_zyx'][::-1])
    radius = np.asarray(c.world['diameter_um'])[:, None] / 2
    lower_gap, upper_gap = positions - radius, extent - positions - radius
    force = p['stiffness_pn_um'] * (np.maximum(p['interaction_range_um'] - lower_gap, 0.) - np.maximum(p['interaction_range_um'] - upper_gap, 0.))
    distance = np.min(np.minimum(lower_gap, upper_gap), axis=1)
    displacement = force * p['mobility_um_pn_s'] * c.dt_s
    return ModuleProposal({'force': force, 'distance_um': distance}, {},
                         () if initial else (Effect('motion.displacement', c.owner_id, displacement),))


def residue_release(c, initial):
    p = c.parameters; records = {} if initial else thaw(c.state['released_by_id'])
    shape = tuple(c.world['grid_shape_zyx']); delta = np.zeros(shape); bodies = []
    for cid, record in c.world['dead_material'].items():
        amount = record['residual_molecules'].get(p['species'], 0.)
        released = records.get(cid, 0.)
        remaining = max(0., amount - released)
        transferred = 0. if initial else remaining * -math.expm1(-p['release_rate_s'] * c.dt_s)
        weights = law.matched_trilinear_weights([record['position_um']], shape, c.world['spacing_xyz'])[0]
        # Residual collision bodies do not exclude solvent; released molecules
        # enter the surrounding fluid support, never a second intracellular pool.
        for index, weight in weights.items(): delta.flat[index] += transferred * weight
        records[cid] = released + transferred
        if remaining - transferred > p['removal_threshold_molecules']:
            bodies.append({'id': cid, 'position_um': record['position_um'], 'length_um': record['length_um'], 'diameter_um': record['diameter_um']})
    total = float(delta.sum())
    return ModuleProposal({'released_amount': total, 'remaining_amount': sum(record['residual_molecules'].get(p['species'], 0.) - records.get(cid, 0.) for cid, record in c.world['dead_material'].items())},
        {'released_by_id': records}, (Effect('field.delta', p['species'], delta), Effect('geometry.residues', c.owner_id, bodies)))


def conversion_lineage(c, initial):
    p = c.parameters; lineage = {} if initial else thaw(c.state['lineage'])
    cells = c.world['all_cells']
    for cid, item in cells.items():
        lineage.setdefault(cid, {'parent_id': None, 'root_id': cid, 'generation': 0, 'birth_time_s': 0., 'death_time_s': None, 'group_id': item['group_id']})
    for event in c.world['events']:
        if event['type'] == 'division':
            parent, child = event['parent_id'], event['child_id']; ancestor = lineage[parent]
            lineage[child] = {'parent_id': parent, 'root_id': ancestor['root_id'], 'generation': ancestor['generation'] + 1,
                              'birth_time_s': event['time_s'], 'death_time_s': None, 'group_id': ancestor['group_id']}
        elif event['type'] == 'death' and event['cell_id'] in lineage:
            lineage[event['cell_id']]['death_time_s'] = event['time_s']
    converted = float(c.inputs['converted_amount'])
    denominator = p['initial_substrate_molecules'] + p['external_substrate_molecules']
    fraction = law.conversion_fraction(converted, p['initial_substrate_molecules'], p['external_substrate_molecules'])
    within = p['window_start_s'] <= c.time_s + c.dt_s <= p['window_end_s']
    metrics = {'conversion_fraction': fraction if within else None, 'denominator_molecules': denominator,
        'converted_amount_molecules': converted, 'window_includes_time': within, 'lineage': lineage,
        'living_count': len(cells), 'ever_born_count': len(lineage), 'missing_policy': 'null for zero denominator or outside window'}
    return ModuleProposal({'metrics': metrics}, {'lineage': lineage}, (Effect('observer.metrics', c.owner_id, metrics),))


def surface_volume(c, initial):
    p = c.parameters; positions = np.asarray(p['positions_xyz_um']); amounts = np.asarray(c.inputs['surface_amount'])
    if len(positions) != len(amounts): raise ValueError('surface entity order must match supplied positions')
    field = np.zeros(tuple(c.world['grid_shape_zyx']))
    for amount, row in zip(amounts, law.matched_trilinear_weights(positions, field.shape, c.world['spacing_xyz'])):
        for index, weight in row.items(): field.flat[index] += amount * weight
    return ModuleProposal({'volume_amount': field}, {})


def volume_division(c, initial):
    p = c.parameters; count = len(c.entity_ids)
    volume = np.asarray(c.inputs.get('volume', physiology.capsule_volume_um3(c.world['length_um'], c.world['diameter_um'])))
    birth = volume.copy() if initial else np.asarray(c.state['birth_volume'])
    if not 0 < p['daughter_fraction'] < 1: raise ValueError('daughter fraction must lie within (0,1)')
    eligible = np.zeros(count, bool) if initial else volume >= np.maximum(p['minimum_volume_um3'], birth + p['added_volume_um3'])
    requests = [{'parent_id': cid, 'fraction': p['daughter_fraction'], 'threshold_volume': max(p['minimum_volume_um3'], float(birth[i]) + p['added_volume_um3'])}
                for i, (cid, yes) in enumerate(zip(c.entity_ids, eligible)) if yes]
    return ModuleProposal({'divide': np.zeros(count), 'birth_volume': birth, 'blocked': np.zeros(count)}, {'birth_volume': birth},
                          (Effect('lifecycle.division', c.owner_id, requests),) if requests else ())


def linear_elongation(c, initial):
    length = np.asarray(c.world['length_um'], float)
    proposed = length + c.parameters['elongation_rate'] * c.dt_s
    return ModuleProposal({'length': proposed, 'diameter': np.asarray(c.world['diameter_um'], float)}, {},
        () if initial else (Effect('geometry.elongation', c.owner_id, proposed),))


def viscosity_motion(c, initial):
    viscosity = float(c.inputs['viscosity'])
    if viscosity <= 0: raise ValueError('dynamic viscosity must be positive')
    scale = c.parameters['reference_viscosity_pa_s'] / viscosity
    return ModuleProposal({'speed_scale': np.full(len(c.entity_ids), scale)}, {},
        (Effect('motion.scale', c.owner_id, np.full(len(c.entity_ids), scale)),))


def advanced_modules():
    result = []
    def add(*a, **kw): result.append(ScientificModule(*a, **kw)); return result[-1]
    add('control.seeded_distribution', '群内参数分布 / Seeded population distribution', 'prepare', 'population', {}, {'value': CELL},
        {'distribution': {'type': 'string'}, 'seed': {'type': 'integer', 'unit': '1', 'minimum': 0}, 'mean': number(minimum=-1e300),
         'spread': number(), 'minimum': number(minimum=-1e300), 'maximum': number(minimum=-1e300)}, {'value': CELL},
        r'x_i\sim\mathrm{clip}(P(\mu,\sigma),a,b)', 'Stable cell-ID-seeded distribution, sampled only at initialization; daughters inherit parent values.', seeded_distribution)
    stock = add('metabolism.shared_inventory', '维持与增长库存 / Shared maintenance and growth', 'physiology', 'population',
        {'accepted_amount': port('cell.scalar', 'accepted_amount', 'molecule', True)},
        {'intracellular_molecules': port('cell.scalar', 'intracellular_amount', 'molecule', True), 'volume': port('cell.scalar', 'cell_volume', 'um^3'),
         'used_molecules': port('cell.scalar', 'consumed_amount', 'molecule', True), 'maintenance_used': port('cell.scalar', 'consumed_amount', 'molecule', True),
         'unmet_duration_s': port('cell.scalar', 'unmet_maintenance_duration', 's'), 'growth_rate': port('cell.scalar', 'specific_growth_rate', '1/min'), 'blocked': CELL},
        {'species': SPECIES, 'initial_molecules': number('molecule'), 'maintenance_molecules_s': number('molecule/s'),
         'max_growth_per_min': number('1/min'), 'volume_yield_um3_molecule': number('um^3/molecule', minimum=1e-300)},
        {'intracellular_molecules': port('cell.scalar', 'intracellular_amount', 'molecule'), 'reserve_correction_molecules': port('cell.scalar', 'correction', 'molecule'), 'volume': port('cell.scalar', 'cell_volume', 'um^3')},
        r'R+U=M+G+R^{new}', 'One uptake owner; maintenance first, growth second. Rejected geometry refunds only growth. Extensive states split by actual daughter volume.',
        shared_inventory, reads=('length_um', 'diameter_um'), effects=('geometry.growth', 'inventory.consumption'), writes=('intracellular_inventory',))
    stock.provides_roles = ['inventory.owner', 'growth.owner']
    stock.manifest['state']['intracellular_molecules']['on_division'] = 'split'
    stock.manifest['state']['volume']['on_division'] = 'split'
    stock.manifest['state']['reserve_correction_molecules']['on_division'] = 'split'
    division = add('division.volume_adder', '体积增量分裂 / Volume adder division', 'lifecycle', 'population',
        {'volume': port('cell.scalar', 'cell_volume', 'um^3')}, {'divide': CELL, 'birth_volume': port('cell.scalar', 'cell_volume', 'um^3'), 'blocked': CELL},
        {'added_volume_um3': number('um^3', minimum=1e-300), 'minimum_volume_um3': number('um^3'), 'daughter_fraction': number(maximum=1)},
        {'birth_volume': port('cell.scalar', 'cell_volume', 'um^3')}, r'V\ge V_{birth}+\Delta V',
        'Volume-conserving capsule division; finite geometry guard can delay division. New membrane area is implicitly available without inventing membrane material.',
        volume_division, reads=('length_um', 'diameter_um'), effects=('lifecycle.division',), writes=('division',))
    division.execution_contract['division_reset_states'] = ['birth_volume']
    elongation = add('growth.linear_elongation', '线性几何伸长 / Linear geometric elongation', 'physiology', 'population', {},
        {'length': port('cell.scalar', 'capsule_length', 'um'), 'diameter': port('cell.scalar', 'capsule_diameter', 'um')},
        {'elongation_rate': number('um/s')}, {}, r'L^{new}=L+r\Delta t',
        'Illustrative prescribed geometry only. No nutrient or biomass synthesis claim; collision-constrained length proposal. Legacy profile remains unchanged.',
        linear_elongation, reads=('length_um', 'diameter_um'), effects=('geometry.elongation',), writes=('geometry.growth',))
    elongation.provides_roles = ['growth.owner']
    elongation.geometry_outputs = {'length': 'length_um', 'diameter': 'diameter_um'}
    add('medium.viscosity_motion', '黏度运动响应 / Viscosity propulsion response', 'physiology', 'population',
        {'viscosity': port('global.scalar', 'dynamic_viscosity', 'Pa*s')}, {'speed_scale': CELL},
        {'reference_viscosity_pa_s': number('Pa*s', minimum=1e-300)}, {}, r'v/v_r=\eta_r/\eta',
        'Explicit fixed-force Newtonian drag approximation. Reads environment-owned viscosity; not a calibrated bacterial motor law.', viscosity_motion, effects=('motion.scale',))
    add('enzyme.secreted', '分泌酶池 / Secreted enzyme pool', 'prepare', 'environment', {}, {'enzyme_copies': port('global.scalar', 'enzyme_copies', 'molecule')},
        {'population_id': {'type': 'string'}, 'initial_copies': number('molecule'), 'secretion_copies_cell_s': number('molecule/s'), 'turnover_s': number('1/s')},
        {'copies': AMOUNT}, r'\dot E=qN-kE', 'Explicit well-mixed extracellular enzyme pool; synthesis is a declared external protein supply, not cost-free intracellular synthesis.', secretion, reads=('all_cells',))
    contact_provider = add('enzyme.contact_provider', '接触酶提供者 / Contact enzyme provider', 'prepare', 'population', {},
        {'enzyme_copies': port('global.scalar', 'enzyme_copies', 'molecule')},
        {**BOX, 'copies_per_cell': number('molecule'), 'contact_range_um': number('um')}, {}, r'E_{contact}=\sum_{i:g_i\le r}E_i',
        'Capsule-to-box physical gap selects finite surface catalyst copies. Alternative provider to the explicit secreted pool.',
        contact_enzyme, reads=('positions_um', 'headings', 'length_um', 'diameter_um'))
    contact_provider.catalyst_resource = ('surface_enzyme', '$owner')
    chemistry = add('reaction.cellulose_hydrolysis', '纤维水解计量 / Cellulose hydrolysis stoichiometry', 'field', 'environment',
        {'enzyme_copies': port('global.scalar', 'enzyme_copies', 'molecule')},
        {'inventory': AMOUNT, 'released_amount': AMOUNT, 'water_consumed': AMOUNT, 'converted_amount': AMOUNT,
         'conversion': SCALAR, 'remaining_fraction': SCALAR},
        {'species': SPECIES, 'initial_agu_units': number('molecule'), 'turnover_s': number('1/s'), 'route': {'type': 'string'},
         'release_position_um': array_parameter('um')}, {'agu_units': AMOUNT, 'water_consumed': AMOUNT},
        r'AGU+H_2O\to glucose;\quad2AGU+H_2O\to cellobiose', 'Lumped infinite-chain AGU convention; solvent water uptake explicit; catalyst is not consumed; no crystallinity or chain-end kinetics.',
        cellulose, reads=GRID_READS, effects=('field.delta',), sources=('https://pmc.ncbi.nlm.nih.gov/articles/PMC8173519/',))
    chemistry.provides_roles = ['reaction.enzyme_consumer']
    chemistry.checkpoint_inventory = {'state': 'agu_units', 'initial_parameter': 'initial_agu_units', 'output': 'inventory'}
    chemistry.catalytic_inputs = ('enzyme_copies',)
    chemistry.effect_accounting = {'field.delta': 'reaction_net'}
    add('material.isotropic_erosion', '连续侵蚀 / Isotropic erosion', 'prepare', 'environment', {'remaining_fraction': SCALAR},
        {'volume': port('global.scalar', 'volume', 'um^3')}, {**BOX, 'initial_fraction': number(maximum=1)}, {'applied_fraction': SCALAR}, r'L=L_0f^{1/3}',
        'Uniform-density isotropic shrinking box driven by a separately accounted material fraction; not mechanical fracture.', erosion, effects=('geometry.obstacle',))
    mesh = add('geometry.triangle_mesh', '闭合三角网格 / Closed triangle mesh', 'prepare', 'environment', {}, {'volume': port('global.scalar', 'volume', 'um^3')},
        {'vertices_xyz': array_parameter('1', array_parameter()), 'faces': array_parameter('1', {'type': 'array', 'items': {'type': 'integer'}}), 'scale_um': number('um', minimum=1e-300)}, {},
        r'V=\frac16\sum_f v_0\cdot(v_1\times v_2)', 'Embedded closed oriented triangle shells, including nonconvex solids and inward cavity surfaces; explicit physical scale and conservative triangle-box voxel contact.', triangle_mesh, effects=('geometry.obstacle',))
    # Includes bounded winding/SAT scratch and the conservative worst case of
    # one Python collision box per voxel, retained across a transaction.
    mesh.execution_contract['workspace_bytes'] = {'fixed': 128 * 1024 * 1024, 'per_voxel': 1024}
    add('surface.adhesion', '黏附与脱附 / Adhesion and detachment', 'physiology', 'population', {'distance_um': port('cell.scalar', 'distance', 'um')}, {'bound': CELL},
        {'attachment_rate_s': number('1/s'), 'detachment_rate_s': number('1/s'), 'capture_distance_um': number('um')}, {'bound': CELL},
        r'P=1-e^{-k\Delta t}', 'Two-state phenomenological attachment clock; attached cells have zero propulsion; detached cells resume their existing motion clock.', adhesion, effects=('motion.scale',))
    add('contact.wall_penalty', '壁面排斥响应 / Wall contact response', 'prepare', 'population', {},
        {'force': port('cell.vector', 'force', 'pN'), 'distance_um': port('cell.scalar', 'distance', 'um')},
        {'stiffness_pn_um': number('pN/um'), 'interaction_range_um': number('um'), 'mobility_um_pn_s': number('um/(pN*s)')}, {},
        r'F=k\max(r-g,0),\quad\Delta x=\mu F\Delta t', 'Overdamped phenomenological near-wall repulsion using transverse radius; final capsule guard remains authoritative, not calibrated contact mechanics.',
        wall_contact, reads=('positions_um', 'diameter_um', 'spacing_xyz', 'grid_shape_zyx'), effects=('motion.displacement',))
    add('life.residue_release', '残体释放 / Dead residual release', 'field', 'environment', {}, {'released_amount': AMOUNT, 'remaining_amount': AMOUNT},
        {'species': SPECIES, 'release_rate_s': number('1/s'), 'removal_threshold_molecules': number('molecule')}, {'released_by_id': RECORD_PORT},
        r'\Delta R=R(1-e^{-k\Delta t})', 'Transfers only recorded dead intracellular residual. Collidable porous residue persists until declared threshold; undegraded remainder stays in residual accounting.',
        residue_release, reads=(*GRID_READS, 'dead_material'), effects=('field.delta', 'geometry.residues'))
    add('life.background_hazard', '背景死亡风险 / Background mortality hazard', 'physiology', 'population', {}, {'probability': CELL},
        {'hazard_s': number('1/s')}, {}, r'P=1-e^{-h\Delta t}',
        'Explicit memoryless background hazard with user-supplied evidence; no hazard is implicitly installed by the system.',
        background_death, effects=('lifecycle.death',), writes=('death',))
    add('observer.conversion_lineage', '转化与谱系 / Conversion and lineage', 'observation', 'environment', {'converted_amount': AMOUNT}, {'metrics': RECORD_PORT},
        {'initial_substrate_molecules': number('molecule'), 'external_substrate_molecules': number('molecule'), 'window_start_s': number('s'), 'window_end_s': number('s')},
        {'lineage': RECORD_PORT}, r'X=N_{converted}/(N_0+N_{external})', 'Every committed lifecycle event; explicit denominator and time window; null when undefined; parent IDs remain traceable after death.',
        conversion_lineage, reads=('all_cells', 'events'), effects=('observer.metrics',))
    add('mapping.surface_volume', '表面到体场 / Surface-to-volume mapping', 'prepare', 'environment',
        {'surface_amount': {**port('global.tensor', 'amount', 'molecule'), 'tensor_shape': ['parameter:surface_count']}}, {'volume_amount': port('field.scalar', 'amount', 'molecule')},
        {'surface_count': {'type': 'integer', 'unit': '1', 'minimum': 1}, 'positions_xyz_um': array_parameter('um', array_parameter())}, {}, r'N_j=\sum_iw_{ij}N_i,\quad\sum_jw_{ij}=1',
        'Explicit ordered surface entities at physical positions; same clipped trilinear weights support the adjoint sample/deposit pair.', surface_volume, reads=('grid_shape_zyx', 'spacing_xyz'))
    for module in result:
        if module.manifest['id'] == 'life.residue_release': module.effect_accounting = {'field.delta': 'internal_net'}
    for module in result:
        module.execution_contract['rng_purposes'] = ['death'] if module.manifest['id'] == 'life.background_hazard' else ['adhesion'] if module.manifest['id'] == 'surface.adhesion' else []
        module.execution_contract['uses_rng'] = module.manifest['id'] in ('control.seeded_distribution', 'life.background_hazard', 'surface.adhesion')
    return result
