"""Chemotaxis transactions: committed signals drive the next motion interval.

This profile retains finite spatial inventories and conservative collision
guards, while explicitly owning adaptation, hazard and physiological state.
All changes, including lifecycle, are proposals until the final frame validates.
"""
from copy import deepcopy
from dataclasses import replace
import math
from types import MappingProxyType, SimpleNamespace

import numpy as np

from friskoli_cad.protocol import ProtocolError
from friskoli_cad.science import pts
from friskoli_cad.science import chemotaxis as science
from .compiler import compile_graph
from .collision import InitialOverlapError
from .local_fields import FieldSpecies, propose_local_field_step
from .motion import orientation_after_heading
from .growth_system import realize_capsule_growth
from .pts_runtime import parameters, _freeze, _check_transfer_rounding
from .runtime import CellGroup, World, SimulationError, CapsuleGeometry
from .spatial_runtime import SpatialSimulation, MAX_CELLS, MAX_VOXELS, MAX_SPECIES, MAX_TUMBLES, MAX_MOTION_PAIRS, _degradation_providers
from .hazard_walk import HazardWalkState, HazardWalkParameters, advance_hazard_walk

PROFILE = 'chemotaxis-spatial-v1'
BASE_PREPARE = frozenset(('space.axis_aligned_obstacle', 'source.finite_local',
    'material.degradable_box', 'reaction.contact_degradation', 'surface.enzyme_activity',
    'field.diffusive_local', 'pts.capsule_area', 'pts.capacity_rebuilt', 'pts.capacity_simplified',
    'field.sample_local', 'uptake.pts_request', 'uptake.saturating_request'))
SIGNALS = frozenset(('signal.pts_accepted', 'signal.constant_bias', 'signal.concentration_memory',
                     'signal.chey_memory', 'signal.mcp_adaptation'))
GROWTH = frozenset(('growth.nutrient_monod', 'growth.nutrient_yield'))
RESERVE = 'metabolism.reserve_balance'
SURVIVAL = 'life.starvation_hazard'
STOCK = GROWTH | {RESERVE}


def _normal_draw(stream):
    """Uncached Box-Muller: exactly two open-uniform draws per normal sample."""
    return math.sqrt(-2 * math.log(stream.uniform_open())) * math.cos(2 * math.pi * stream.uniform_open())


def _division_threshold(node, geometry, streams, cell_id):
    from friskoli_cad.science import physiology
    p = {key: value.value for key, value in node.parameters.items()}
    draw = _normal_draw(streams.stream(node.id, node.owner_id, cell_id, 'division_threshold')) if p['area_cv'] > 0 else 0.
    volume = float(physiology.capsule_volume_um3(geometry.length_um, geometry.diameter_um))
    return float(physiology.surface_adder_delta(reference_birth_volume_um3=volume,
        target_volume_um3=p['target_volume_um3'], radius_um=geometry.diameter_um / 2,
        cv=p['area_cv'], normal_deviate=draw, minimum_area_um2=p['minimum_area_um2']))


def _error(message, code='chemotaxis.contract'):
    raise ProtocolError(code, '/graph', message)


def validate_chemotaxis_project(project, manifests, registry=None):
    if project.get('execution_profile') != PROFILE or project.get('controls'):
        _error('Chemotaxis profile requires its explicit profile and no external controls')
    plan = compile_graph(project['graph'], manifests)
    if registry is None:
        from .chemotaxis_modules import chemotaxis_registry
        registry = chemotaxis_registry()
    if sum(len(g['ids']) for g in project['groups'].values()) > MAX_CELLS:
        _error('Initial cell budget exceeded')
    if math.prod(project['domain']['counts_xyz']) > MAX_VOXELS or len(project['species']) > MAX_SPECIES:
        _error('Spatial field budget exceeded')
    motion = [n for n in plan.nodes if n.module_id in ChemotaxisSimulation.MOTION_MODULE_IDS]
    if sorted(n.owner_id for n in motion) != sorted(project['groups']):
        _error('Each population requires exactly one motion owner')
    fields = [n for n in plan.nodes if n.module_id in ChemotaxisSimulation.FIELD_MODULE_IDS]
    if registry is not None:
        providers = _degradation_providers(plan, registry)
        materials = [n for n in plan.nodes if n.module_id == 'material.degradable_box']
        enzymes = [n for n in plan.nodes if n.module_id in ('surface.enzyme_activity', 'expression.surface_copies')]
        if len(providers) > 1 or (materials and (len(providers) != 1 or not enzymes)):
            _error('Materials require exactly one registered executable degradation provider and explicit enzyme')
        if len({n.owner_id for n in enzymes}) != len(enzymes):
            _error('A population may have only one surface enzyme owner')
    objects = [n for n in plan.nodes if n.module_id in ('source.finite_local', 'material.degradable_box', 'space.axis_aligned_obstacle')]
    if len({n.owner_id for n in objects}) != len(objects):
        _error('Physical object owner IDs must be unique')
    species = [n.parameters['species'].value for n in fields]
    if len(set(species)) != len(species) or not set(species).issubset(project['species']):
        _error('Each active species requires one unique registered field owner')
    inventories = set()
    for n in plan.nodes:
        p = {key: value.value for key, value in n.parameters.items()}
        if n.owner_kind == 'population' and n.owner_id not in project['groups']:
            _error('Foreign population owner')
        if n.module_id in ('uptake.local_settlement', *STOCK, SURVIVAL, 'expression.surface_copies', 'life.health_balance', 'division.area_adder'):
            role = 'intracellular_stock' if n.module_id in STOCK else 'death_owner' if n.module_id in (SURVIVAL, 'life.health_balance') else n.module_id
            key = (n.owner_id, role, n.parameters['species'].value if role == 'uptake.local_settlement' else '')
            if key in inventories:
                _error('Duplicate physiological or uptake owner')
            inventories.add(key)
        for name, b in n.inputs.items():
            source = plan.by_id[b.source_node]
            expected = 'previous_step' if (source.module_id in ChemotaxisSimulation.FIELD_MODULE_IDS or
                source.module_id in ChemotaxisSimulation.MOTION_MODULE_IDS or
                (n.module_id == 'motion.hazard_run_tumble' and name == 'motor_bias') or
                (n.module_id == 'expression.surface_copies' and name == 'health')) else 'same_step'
            if b.timing != expected:
                _error(f'{n.id}.{name} requires {expected}', 'chemotaxis.timing')
        if n.module_id == 'field.sample_local':
            source = plan.by_id[n.inputs['field'].source_node]
            pose = plan.by_id[n.inputs['position'].source_node]
            if source not in fields or pose.module_id not in ChemotaxisSimulation.MOTION_MODULE_IDS or pose.owner_id != n.owner_id:
                _error('Sampler requires its own motion and registered field')
        if n.module_id == 'uptake.local_settlement':
            request = plan.by_id[n.inputs['requested_flux'].source_node]
            if request.module_id not in ('uptake.pts_request', 'uptake.saturating_request'):
                _error('Settlement requires a registered uptake request')
            sampler = plan.by_id[request.inputs['concentration'].source_node]
            if sampler.module_id != 'field.sample_local' or sampler.inputs['field'].source_node != n.inputs['field'].source_node:
                _error('Request and settlement must read the same field')
        if n.module_id in STOCK and plan.by_id[n.inputs['accepted_amount'].source_node].module_id != 'uptake.local_settlement':
            _error('Intracellular stock must receive the actual accepted uptake amount')
        if n.module_id == SURVIVAL and plan.by_id[n.inputs['unmet_duration_s'].source_node].module_id != RESERVE:
            _error('Starvation survival requires an explicit nutrient reserve owner')
        if n.module_id == 'division.area_adder' and plan.by_id[n.inputs['volume'].source_node].module_id not in GROWTH:
            _error('Division must read its nutrient growth volume owner')
        if n.module_id in ('expression.surface_copies', 'life.health_balance') and plan.by_id[n.inputs['growth_rate'].source_node].module_id not in GROWTH:
            _error('Physiology must read its nutrient growth owner')
        if n.module_id == 'life.health_balance' and plan.by_id[n.inputs['functional_copies'].source_node].module_id not in ('pts.capacity_rebuilt', 'pts.capacity_simplified'):
            _error('Health requires an explicit target/capacity owner')
        if n.module_id in ('source.finite_local', 'material.degradable_box') and p['species'] not in species:
            _error('Finite sources/materials require a registered active field')
        try:
            from friskoli_cad.science import physiology
            from friskoli_cad.science import survival
            empty = np.asarray([], dtype=float)
            if n.module_id == SURVIVAL:
                survival.advance_starvation(empty, empty, 0., grace_s=p['grace_s'], recovery_rate=p['recovery_rate'], death_rate_per_min=p['death_rate_per_min'])
            elif n.module_id == 'motion.hazard_run_tumble':
                science.tumble_hazard(0., minimum_s=p['minimum_tumble_rate_s'], maximum_s=p['maximum_tumble_rate_s'])
                HazardWalkParameters(p['speed_um_s'], p['minimum_tumble_rate_s'], 2,
                    p['tumble_mode'], p['tumble_duration_s'] if p['tumble_mode'] == 'dwell' else 0., p['turn_kernel'])
            elif n.module_id in GROWTH:
                physiology.advance_growth(empty, empty, 0., policy='rebuilt_monod' if n.module_id.endswith('monod') else 'simplified_yield',
                    max_growth_per_min=p['max_growth_per_min'], volume_yield_um3_molecule=p['volume_yield_um3_molecule'], half_saturation_uM=p.get('half_saturation_um'))
            elif n.module_id in ('life.health_balance', 'expression.surface_copies'):
                if p['policy'] not in ('rebuilt', 'simplified') or p['available_fraction'] <= 0:
                    raise ValueError('Known physiological policy and positive available membrane fraction required')
                if n.module_id == 'life.health_balance':
                    if p['reference_pts_copies'] <= 0:
                        raise ValueError('Reference PTS copies must be positive')
                    if p['policy'] == 'rebuilt':
                        parameters(n, physiology.RebuiltHealthParameters)
                    else:
                        physiology.advance_simplified_health(empty, empty, empty, 0., **{k: p[k] for k in (
                            'repair_per_min', 'burden_per_min', 'starvation_per_min', 'max_growth_per_min', 'death_max_per_min', 'death_threshold')})
                elif p['policy'] == 'rebuilt':
                    physiology.rebuilt_expression_rate(empty, empty, empty, expression_copies_min=p['synthesis_copies_min'], **{k: p[k] for k in (
                        'health_floor', 'health_hill', 'metabolic_floor', 'metabolic_half_growth_per_min', 'burden_half_fraction', 'burden_hill')})
            elif n.module_id == 'division.area_adder' and not 0 < p['minimum_fraction'] <= p['maximum_fraction'] < 1:
                raise ValueError('Division fractions must lie strictly inside (0,1), in increasing order')
            if n.module_id == 'division.area_adder' and n.module_version == '2.0.0':
                if p['minimum_area_um2'] <= 0:
                    raise ValueError('Division minimum added area must be positive')
                for geometry in project['groups'][n.owner_id]['initial_geometry']:
                    physiology.capsule_geometry_from_volume(p['target_volume_um3'], geometry['diameter_um'] / 2)
        except ValueError as error:
            _error(f'{n.id}: {error}', 'chemotaxis.parameter')
    reservoirs = {n.parameters['species'].value for n in fields if n.module_id == 'field.ideal_local_reservoir'}
    for n in plan.nodes:
        if n.module_id in ('source.finite_local', 'material.degradable_box') and n.parameters['species'].value in reservoirs:
            _error('Ideal background cannot also own finite source/material stock')
    return plan


class ChemotaxisSimulation(SpatialSimulation):
    MOTION_MODULE_IDS = ('motion.hazard_run_tumble', 'motion.unbiased_run_tumble')
    FIELD_MODULE_IDS = ('field.diffusive_local', 'field.ideal_local_reservoir')

    def __init__(self, world, project, registry, *, seed=None, field_backend='numpy-cpu'):
        self.supply_totals = {}
        self.uptake_totals = {}
        self.physiology_ledger = {}
        self.next_cell_index = 0
        self.last_dt_s = 0.
        self.dead_material = {}
        super().__init__(world, project, registry, seed=seed, field_backend=field_backend)
        self.uptake_totals = {n.id: 0. for n in self.plan.nodes if n.module_id == 'uptake.local_settlement'}
        from .observations import initial_observation
        self.observation_state = initial_observation(self.project, self.current.cell_frame)
        self.current = self._with_metrics(self.current, self.observation_state)
        self.current = replace(self.current, lifecycle_details={'lifecycle_version': '0.1.0', 'deaths': []})

    def _validate_project(self, project, registry):
        validate_chemotaxis_project(project, registry.manifests, registry)

    def _execution_schedule(self):
        from .profiles import chemotaxis_schedule
        return chemotaxis_schedule(self.plan, self.registry)

    def _initial_walks(self, capsules):
        return {c.cell_id: HazardWalkState(c.heading) for c in capsules}

    def _initial_field_species(self):
        result = []
        grid = self.world.grid
        _, blocked = self._geometry_for(self.materials)
        z, y, x = np.ogrid[:grid.shape[0], :grid.shape[1], :grid.shape[2]]
        coordinates = ((x + .5) * grid.dx_um, (y + .5) * grid.dy_um, (z + .5) * grid.dz_um)
        for node in self._field_nodes.values():
            species = node.parameters['species'].value
            initial = self.project['species'][species]['initial_concentration']['value']
            diffusion = 0.
            if node.module_id == 'field.diffusive_local':
                diffusion = node.parameters['diffusivity_um2_s'].value
                initial = np.full(grid.shape, float(initial))
                for i, axis in enumerate('xyz'):
                    key = f'gradient_{axis}_um_per_um'
                    if key in node.parameters and node.parameters[key].value != 0:
                        initial += node.parameters[key].value * (coordinates[i] - grid.extent_um[i] / 2)
                initial[np.asarray(blocked).reshape(grid.shape)] = 0.
            result.append(FieldSpecies(species, initial, diffusion))
            if node.module_id == 'field.ideal_local_reservoir':
                self.supply_totals[species] = 0.
        return result

    def _prepare_node_ids(self):
        return [n.id for n in self.plan.nodes if n.module_id in BASE_PREPARE or
                (self._degradation_node is not None and n.id == self._degradation_node.id)]

    def _prepare(self):
        outputs, state = super()._prepare()
        for node in self._field_nodes.values():
            if node.module_id == 'field.ideal_local_reservoir':
                species = node.parameters['species'].value
                outputs[node.id] = {'concentration': np.asarray(self.fields.concentrations_uM[species]).reshape(self.world.grid.shape),
                                    'cumulative_supply': self.supply_totals[species]}
                state[node.id] = {'cumulative_supply': self.supply_totals[species]}
        self._physiology_initial_outputs(outputs, state)
        return outputs, state

    def _input(self, node, name, outputs):
        binding = node.inputs[name]
        return outputs[binding.source_node][binding.source_port]

    def _contact_enzyme_copies(self):
        result = super()._contact_enzyme_copies()
        for node in self.plan.nodes:
            if node.module_id == 'expression.surface_copies':
                for cid, value in zip(self.world.groups[node.owner_id].ids, self.state[node.id]['enzyme_copies'], strict=True):
                    result[cid] = float(value)
        return result

    def _degradation_parameters(self, node, material_id):
        result = super()._degradation_parameters(node, material_id)
        if node.module_id == 'reaction.direct_bulk_hydrolysis':
            result['initial_molecules'] = self._material_nodes[material_id].parameters['initial_molecules'].value
        return result

    def _physiology_initial_outputs(self, outputs, state):
        from friskoli_cad.science import physiology
        for node in self.plan.nodes:
            m, nid = node.module_id, node.id
            if m not in STOCK | {SURVIVAL, 'expression.surface_copies', 'life.health_balance', 'division.area_adder'}:
                continue
            group = self.world.groups[node.owner_id]
            count = len(group.ids)
            p = {k: v.value for k, v in node.parameters.items()}
            old = self.state.get(nid)
            if m == RESERVE:
                available = old['intracellular_molecules'].copy() if old else np.full(count, p['initial_molecules'])
                outputs[nid] = {'intracellular_molecules': available, 'used_molecules': np.zeros(count), 'unmet_duration_s': np.zeros(count)}
                correction = old['reserve_correction_molecules'].copy() if old else np.zeros(count)
                state[nid] = {'intracellular_molecules': available, 'reserve_correction_molecules': correction}
            elif m == SURVIVAL:
                exposure = old['starvation_time_s'].copy() if old else np.zeros(count)
                outputs[nid] = {'starvation_time_s': exposure, 'health': np.exp(-exposure / p['grace_s']), 'death_hazard': np.zeros(count)}
                state[nid] = {'starvation_time_s': exposure}
            elif m in GROWTH:
                volume = physiology.capsule_volume_um3([g.length_um for g in group.geometry], [g.diameter_um for g in group.geometry])
                available = old['intracellular_molecules'].copy() if old else np.full(count, p['initial_molecules'])
                outputs[nid] = {'intracellular_molecules': available, 'volume': volume,
                    'used_molecules': np.zeros(count), 'growth_rate': np.zeros(count), 'blocked': np.zeros(count)}
                state[nid] = {'intracellular_molecules': available, 'volume': volume}
            elif m == 'expression.surface_copies':
                copies = old['enzyme_copies'].copy() if old else np.full(count, p['initial_copies'])
                outputs[nid], state[nid] = {'enzyme_copies': copies}, {'enzyme_copies': copies}
            elif m == 'life.health_balance':
                health = old['health'].copy() if old else np.full(count, p['initial_health'])
                outputs[nid], state[nid] = {'health': health, 'death_hazard': np.zeros(count)}, {'health': health}
            else:
                area = np.asarray([math.pi * g.length_um * g.diameter_um for g in group.geometry])
                birth = old['birth_area'].copy() if old else area
                outputs[nid], state[nid] = {'divide': np.zeros(count), 'birth_area': birth, 'blocked': np.zeros(count)}, {'birth_area': birth}
                if node.module_version == '2.0.0':
                    required = old['required_area'].copy() if old else np.asarray([
                        _division_threshold(node, geometry, self.streams, cid)
                        for cid, geometry in zip(group.ids, group.geometry, strict=True)])
                    outputs[nid]['required_area'] = state[nid]['required_area'] = required

    def _physiology(self, world, walks, outputs, state, dt, streams, next_time):
        from friskoli_cad.science import physiology
        from friskoli_cad.science import survival
        groups = dict(world.groups)
        records = deepcopy(self.physiology_ledger)
        dead_material = deepcopy(self.dead_material)
        growth_proposals, available_before = {}, {}
        for node in self.plan.nodes:
            if node.module_id != RESERVE:
                continue
            p = {k: v.value for k, v in node.parameters.items()}
            before = state[node.id]['intracellular_molecules']
            accepted = self._input(node, 'accepted_amount', outputs)
            value = survival.advance_reserve(before, accepted, dt, p['maintenance_molecules_s'], state[node.id]['reserve_correction_molecules'])
            outputs[node.id] = {'intracellular_molecules': value.reserve_molecules,
                                'used_molecules': value.used_molecules, 'unmet_duration_s': value.unmet_duration_s}
            state[node.id] = {'intracellular_molecules': value.reserve_molecules, 'reserve_correction_molecules': value.correction_molecules}
            record = records.setdefault(p['species'], {'growth_consumed_molecules': 0., 'removed_residual_molecules': 0.})
            record['maintenance_consumed_molecules'] = record.get('maintenance_consumed_molecules', 0.) + math.fsum(float(v) for v in value.used_molecules)
        for node in self.plan.nodes:
            if node.module_id not in GROWTH:
                continue
            gid, p = node.owner_id, {k: v.value for k, v in node.parameters.items()}
            group = groups[gid]
            amount = self._input(node, 'accepted_amount', outputs)
            available = state[node.id]['intracellular_molecules'] + amount
            for before, after, added in zip(state[node.id]['intracellular_molecules'], available, amount, strict=True):
                _check_transfer_rounding(before, after, added)
            proposed = physiology.advance_growth(available, state[node.id]['volume'], dt,
                policy='rebuilt_monod' if node.module_id.endswith('monod') else 'simplified_yield',
                max_growth_per_min=p['max_growth_per_min'], volume_yield_um3_molecule=p['volume_yield_um3_molecule'],
                half_saturation_uM=p.get('half_saturation_um'))
            proposed, realized_lengths = realize_capsule_growth(available,
                [g.length_um for g in group.geometry], [g.diameter_um for g in group.geometry],
                proposed.used_molecules, dt, p['volume_yield_um3_molecule'])
            geometry = tuple(CapsuleGeometry(float(length), g.diameter_um)
                             for length, g in zip(realized_lengths, group.geometry, strict=True))
            groups[gid] = CellGroup(gid, group.ids, group.positions_um, group.orientation_xyzw, geometry)
            growth_proposals[gid], available_before[gid] = (node, proposed), available
        blocked_growth = set()
        # Growth enlarges nested capsules at fixed centers. A valid final shape
        # therefore validates the whole geometric growth path. Reject every
        # body involved in an invalid joint proposal, not a random winner.
        for _ in range(len(growth_proposals) + 2):
            candidate_world = SimpleNamespace(grid=world.grid, groups=groups)
            capsules = self._capsules(candidate_world)
            try:
                self._guard(capsules, capsules)
                break
            except InitialOverlapError as error:
                affected = {cid for contact in error.contacts for cid in contact.cell_ids}
                changed = False
                for gid, (node, proposed) in growth_proposals.items():
                    group, original = groups[gid], world.groups[gid]
                    geom = list(group.geometry)
                    for i, cid in enumerate(group.ids):
                        if cid in affected and cid not in blocked_growth:
                            geom[i] = original.geometry[i]
                            blocked_growth.add(cid)
                            changed = True
                    groups[gid] = CellGroup(gid, group.ids, group.positions_um, group.orientation_xyzw, tuple(geom))
                if not changed:
                    raise
        else:
            raise SimulationError('chemotaxis.growth_geometry', 'Growth placement could not resolve collisions')
        for gid, (node, proposed) in growth_proposals.items():
            mask = np.asarray([cid in blocked_growth for cid in groups[gid].ids], dtype=bool)
            used = np.where(mask, 0., proposed.used_molecules)
            available = available_before[gid] - used
            volume = np.where(mask, state[node.id]['volume'], proposed.volume_um3)
            growth = np.where(mask, 0., proposed.actual_growth_per_min)
            outputs[node.id] = {'intracellular_molecules': available, 'volume': volume, 'used_molecules': used,
                                'growth_rate': growth, 'blocked': mask.astype(float)}
            state[node.id] = {'intracellular_molecules': available, 'volume': volume}
            species = node.parameters['species'].value
            record = records.setdefault(species, {'growth_consumed_molecules': 0., 'removed_residual_molecules': 0.})
            record['growth_consumed_molecules'] += math.fsum(float(v) for v in used)
        # Read geometric area after accepted growth for burden and division.
        for node in self.plan.nodes:
            if node.module_id == 'pts.capsule_area':
                outputs[node.id] = {'surface_area': np.asarray([math.pi * g.length_um * g.diameter_um for g in groups[node.owner_id].geometry])}
        self._capacity_outputs(outputs)
        deaths, death_details = set(), {}
        for node in self.plan.nodes:
            gid, nid, m = node.owner_id, node.id, node.module_id
            if m not in ('expression.surface_copies', 'life.health_balance', SURVIVAL):
                continue
            p = {k: v.value for k, v in node.parameters.items()}
            rate = self._input(node, 'growth_rate', outputs) if m != SURVIVAL else None
            if m == SURVIVAL:
                value = survival.advance_starvation(state[nid]['starvation_time_s'], self._input(node, 'unmet_duration_s', outputs), dt,
                    grace_s=p['grace_s'], recovery_rate=p['recovery_rate'], death_rate_per_min=p['death_rate_per_min'])
                outputs[nid] = {'starvation_time_s': value.starvation_time_s, 'health': value.health, 'death_hazard': value.death_hazard_per_min}
                state[nid] = {'starvation_time_s': value.starvation_time_s}
            elif m == 'expression.surface_copies':
                synthesis = p['synthesis_copies_min']
                if p.get('policy') == 'rebuilt':
                    health = self._input(node, 'health', self.outputs)
                    area = self._input(node, 'surface_area', outputs)
                    occupancy = np.minimum(state[nid]['enzyme_copies'] * p['enzyme_footprint_um2'] / (area * p.get('available_fraction', 1.)), 1.)
                    synthesis = physiology.rebuilt_expression_rate(health, rate, occupancy, expression_copies_min=synthesis,
                        **{key: p[key] for key in ('health_floor', 'health_hill', 'metabolic_floor', 'metabolic_half_growth_per_min', 'burden_half_fraction', 'burden_hill')})
                copies = physiology.advance_copy_number(state[nid]['enzyme_copies'], synthesis, p['turnover_per_min'], dt)
                outputs[nid], state[nid] = {'enzyme_copies': copies}, {'enzyme_copies': copies}
            else:
                functional = self._input(node, 'functional_copies', outputs)
                enzyme = self._input(node, 'enzyme_copies', outputs)
                area = self._input(node, 'surface_area', outputs)
                if p['policy'] == 'rebuilt':
                    phi_pts = np.minimum(functional * p['carrier_footprint_um2'] / (area * p['available_fraction']), 1.)
                    phi_inp = np.minimum(enzyme * p['enzyme_footprint_um2'] / (area * p['available_fraction']), 1.)
                    capacity_node = self.plan.by_id[node.inputs['functional_copies'].source_node]
                    target = capacity_node.parameters['g_requested'].value * capacity_node.parameters['reference_pts_copies'].value
                    value = physiology.advance_rebuilt_health(state[nid]['health'], rate, phi_pts, phi_inp,
                        np.maximum(target - functional, 0), dt, parameters(node, physiology.RebuiltHealthParameters))
                elif p['policy'] == 'simplified':
                    capacity_node = self.plan.by_id[node.inputs['functional_copies'].source_node]
                    if capacity_node.module_id != 'pts.capacity_simplified':
                        raise ValueError('Simplified health requires simplified capacity target and ceiling')
                    capacity = pts.simplified_capacity(area, capacity_node.parameters['g_requested'].value,
                                                       parameters(capacity_node, pts.SimplifiedCapacityParameters))
                    reference = p['reference_pts_copies']
                    burden = np.maximum(capacity.target_copies - reference, 0) / np.maximum(capacity.capacity_copies - reference, 1e-12)
                    value = physiology.advance_simplified_health(state[nid]['health'], rate, burden, dt,
                        **{k: p[k] for k in ('repair_per_min', 'burden_per_min', 'starvation_per_min', 'max_growth_per_min', 'death_max_per_min', 'death_threshold')})
                else:
                    raise ValueError('Unknown health policy')
                outputs[nid], state[nid] = {'health': value.health, 'death_hazard': value.death_hazard_per_min}, {'health': value.health}
            if m in ('life.health_balance', SURVIVAL):
                for cid, hazard in zip(groups[gid].ids, value.death_hazard_per_min, strict=True):
                    if hazard > 0:
                        draw = streams.stream(nid, gid, cid, 'death').uniform_open()
                        probability = -math.expm1(-float(hazard) * dt / 60)
                        if draw < probability:
                            deaths.add(cid)
                            i = groups[gid].ids.index(cid)
                            death_details[cid] = {'node_id': nid, 'module_id': m, 'policy': p.get('policy', 'reserve_starvation'),
                                'health': float(value.health[i]), 'death_hazard_per_min': float(hazard),
                                'probability': probability, 'random_draw': draw}
        events, inherited, next_id = [], {}, self.next_cell_index
        next_walks = dict(walks)
        for gid in sorted(groups):
            group = groups[gid]
            keep = [i for i, cid in enumerate(group.ids) if cid not in deaths]
            inherited[gid] = [(i, 1.) for i in keep]
            for i, cid in enumerate(group.ids):
                if cid not in deaths:
                    continue
                residual, copies = {}, 0.
                for node in self.plan.nodes:
                    if node.owner_id != gid:
                        continue
                    if node.module_id in STOCK:
                        species = node.parameters['species'].value
                        residual[species] = math.fsum((float(state[node.id]['intracellular_molecules'][i]),
                            float(state[node.id]['reserve_correction_molecules'][i]) if node.module_id == RESERVE else 0.))
                        records.setdefault(species, {'growth_consumed_molecules': 0., 'removed_residual_molecules': 0.})['removed_residual_molecules'] += residual[species]
                    if node.module_id == 'expression.surface_copies':
                        copies = float(state[node.id]['enzyme_copies'][i])
                dead_material[cid] = {'time_s': next_time, 'group_id': gid, 'residual_molecules': residual,
                                      'removed_copies': copies, 'death_rule': death_details[cid]}
                events.append({'type': 'death', 'cell_id': cid, 'time_s': next_time})
                next_walks.pop(cid)
            groups[gid] = CellGroup(gid, tuple(group.ids[i] for i in keep), group.positions_um[keep].reshape(-1, 3),
                                    group.orientation_xyzw[keep].reshape(-1, 4), tuple(group.geometry[i] for i in keep))
        # Divide in stable ID order. A blocked placement delays this event only;
        # committed environment and other accepted physiology still advance.
        division_resets = {}
        seen = set(self.frame_validator.seen)
        for node in self.plan.nodes:
            if node.module_id != 'division.area_adder':
                continue
            gid, group = node.owner_id, groups[node.owner_id]
            p = {k: v.value for k, v in node.parameters.items()}
            for cid in sorted(group.ids):
                group = groups[gid]
                index = group.ids.index(cid)
                old_index = inherited[gid][index][0]
                geometry = group.geometry[index]
                area = math.pi * geometry.length_um * geometry.diameter_um
                threshold = state[node.id]['required_area'][old_index] if node.module_version == '2.0.0' else p['added_area_um2']
                if area - state[node.id]['birth_area'][old_index] < threshold:
                    continue
                outputs[node.id]['blocked'][old_index] = 1.
                if sum(len(g.ids) for g in groups.values()) >= MAX_CELLS:
                    continue  # declared capacity delays division; never kills a cell
                volume = float(physiology.capsule_volume_um3(geometry.length_um, geometry.diameter_um))
                try:
                    lo, hi = physiology.division_split_bounds(volume, geometry.diameter_um / 2)
                except ValueError:
                    continue
                lo, hi = max(float(lo), p['minimum_fraction']), min(float(hi), p['maximum_fraction'])
                if lo > hi:
                    continue
                if node.module_version == '2.0.0':
                    draw = _normal_draw(streams.stream(node.id, gid, cid, 'division_fraction')) if p['split_sd'] > 0 else 0.
                    fraction = float(np.clip(p['split_mean'] + p['split_sd'] * draw, lo, hi))
                else:
                    fraction = lo + (hi - lo) * streams.stream(node.id, gid, cid, 'division_fraction').uniform_open()
                volumes = volume * fraction, volume * (1 - fraction)
                geometry_pair = tuple(CapsuleGeometry(float(physiology.capsule_geometry_from_volume(v, geometry.diameter_um / 2).total_length_um), geometry.diameter_um) for v in volumes)
                separation = (geometry_pair[0].length_um + geometry_pair[1].length_um) / 2
                heading = np.asarray(next_walks[cid].heading)
                center = group.positions_um[index]
                pos1, pos2 = center - (1 - fraction) * separation * heading, center + fraction * separation * heading
                while True:
                    child_id = f'{cid}__child_{next_id}'
                    if child_id not in seen:
                        break
                    next_id += 1
                ids = group.ids + (child_id,)
                positions = np.vstack((group.positions_um, pos2))
                positions[index] = pos1
                orientations = np.vstack((group.orientation_xyzw, group.orientation_xyzw[index]))
                geom = list(group.geometry) + [geometry_pair[1]]
                geom[index] = geometry_pair[0]
                try:
                    proposed_group = CellGroup(gid, ids, positions, orientations, tuple(geom))
                    proposed_world = World(world.grid, {**groups, gid: proposed_group}, world.species_initial_uM, world.schedules)
                    capsules = self._capsules(proposed_world)
                    self._guard(capsules, capsules)
                except (InitialOverlapError, SimulationError):
                    continue
                groups[gid] = proposed_group
                inherited[gid][index] = (old_index, fraction)
                inherited[gid].append((old_index, 1 - fraction))
                next_walks[child_id] = HazardWalkState(next_walks[cid].heading)
                next_walks[cid] = HazardWalkState(next_walks[cid].heading)
                seen.add(child_id)
                next_id += 1
                division_resets[cid] = math.pi * geometry_pair[0].length_um * geometry.diameter_um
                division_resets[child_id] = math.pi * geometry_pair[1].length_um * geometry.diameter_um
                events.append({'type': 'division', 'parent_id': cid, 'child_id': child_id, 'time_s': next_time})
                outputs[node.id]['blocked'][old_index] = 0.
        # Apply declared state inheritance. Extensive observed interval outputs
        # follow the same volume split; intensive signals remain copied.
        extensive = {'accepted_amount', 'accepted_flux', 'cumulative_uptake', 'intracellular_molecules', 'reserve_correction_molecules', 'volume', 'used_molecules', 'enzyme_copies'}
        for node in self.plan.nodes:
            if node.owner_kind != 'population':
                continue
            gid = node.owner_id
            source = np.asarray([i for i, _ in inherited[gid]], dtype=int)
            weights = np.asarray([w for _, w in inherited[gid]])
            for collection in (outputs, state):
                for name, value in collection[node.id].items():
                    value = np.asarray(value)
                    fresh = value[source].copy()
                    if name in extensive:
                        fresh = fresh * weights.reshape((-1,) + (1,) * (fresh.ndim - 1))
                    collection[node.id][name] = fresh
            if node.module_id == 'division.area_adder':
                for i, cid in enumerate(groups[gid].ids):
                    if cid in division_resets:
                        state[node.id]['birth_area'][i] = division_resets[cid]
                        if node.module_version == '2.0.0':
                            state[node.id]['required_area'][i] = _division_threshold(node, groups[gid].geometry[i], streams, cid)
                outputs[node.id]['birth_area'] = state[node.id]['birth_area']
                if node.module_version == '2.0.0':
                    outputs[node.id]['required_area'] = state[node.id]['required_area']
                outputs[node.id]['divide'] = np.asarray([float(cid in division_resets) for cid in groups[gid].ids])
            if node.module_id in GROWTH:
                actual_volume = np.asarray([float(physiology.capsule_volume_um3(g.length_um, g.diameter_um)) for g in groups[gid].geometry])
                state[node.id]['volume'] = outputs[node.id]['volume'] = actual_volume
            if node.module_id == 'pts.capsule_area':
                outputs[node.id]['surface_area'] = np.asarray([math.pi * g.length_um * g.diameter_um for g in groups[gid].geometry])
        self._capacity_outputs(outputs)
        return (World(world.grid, groups, world.species_initial_uM, world.schedules), MappingProxyType(next_walks),
                events, records, dead_material, next_id)

    def _capacity_outputs(self, outputs):
        for node in self.plan.nodes:
            if node.module_id not in ('pts.capacity_rebuilt', 'pts.capacity_simplified'):
                continue
            rebuilt = node.module_id.endswith('rebuilt')
            parameter_class = pts.RebuiltCapacityParameters if rebuilt else pts.SimplifiedCapacityParameters
            function = pts.rebuilt_capacity if rebuilt else pts.simplified_capacity
            result = function(self._input(node, 'surface_area', outputs), node.parameters['g_requested'].value, parameters(node, parameter_class))
            outputs[node.id] = {'functional_copies': result.functional_copies, 'g_effective': result.g_effective}

    def _signal_outputs(self, outputs, state, dt):
        # Plan order is topological among same-boundary signal dependencies.
        for node in self.plan.nodes:
            n, m = node.id, node.module_id
            if m not in SIGNALS:
                continue
            count = len(self.world.groups[node.owner_id].ids)
            p = {k: v.value for k, v in node.parameters.items()}
            if m == 'signal.pts_accepted':
                par = parameters(node, pts.SignalParameters)
                if dt is None:
                    value = pts.signal_readout(np.full(count, p['initial_ei_fraction']), np.full(count, p['initial_chey_p_um']), par)
                else:
                    value = pts.advance_accepted_signal(self._input(node, 'accepted_flux', outputs), self.state[n]['ei_fraction'], self.state[n]['chey_p'], dt, par)
                outputs[n] = {'ei_fraction': value.ei_fraction, 'chey_p': value.chey_p_uM,
                              'chea_active': value.chea_active_uM, 'motor_bias': value.motor_bias}
                state[n] = {'ei_fraction': value.ei_fraction, 'chey_p': value.chey_p_uM}
            elif m == 'signal.constant_bias':
                outputs[n], state[n] = {'motor_bias': np.full(count, p['bias'])}, {}
            elif m == 'signal.concentration_memory':
                concentration = self._input(node, 'concentration', outputs)
                memory = np.full(count, p['initial_memory_um']) if dt is None else science.advance_concentration_memory(
                    self.state[n]['memory'], concentration, dt, memory_tau_s=p['memory_tau_s'])
                bias = science.rebuilt_motor_bias(self._input(node, 'motor_bias', outputs), memory, concentration,
                                                  gradient_strength_per_uM=p['gradient_strength_per_um'])
                outputs[n], state[n] = {'memory': memory, 'motor_bias': bias}, {'memory': memory}
            elif m == 'signal.chey_memory':
                chey = self._input(node, 'chey_p', outputs)
                memory = np.full(count, p['initial_memory_um']) if dt is None else science.advance_chey_memory(
                    self.state[n]['memory'], chey, dt, adaptation_tau_s=p['adaptation_tau_s'])
                effective = science.adapted_chey_signal(chey, memory, baseline_uM=p['baseline_um'], total_uM=p['total_um'])
                bias = science.motor_bias(effective, half_uM=p['motor_half_um'], hill=p['motor_hill'])
                outputs[n], state[n] = {'memory': memory, 'effective_chey': effective, 'motor_bias': bias}, {'memory': memory}
            elif m == 'signal.mcp_adaptation':
                ligand = self._input(node, 'concentration', outputs)
                par = parameters(node, science.MWCParameters)
                old = science.mcp_adapted_methylation(ligand, par) if dt is None else self.state[n]['adaptation']
                value = science.advance_mcp_adaptation(ligand, old, 0. if dt is None else dt, par)
                chey_old = np.full(count, p['initial_chey_p_um']) if dt is None else self.state[n]['chey_p']
                # v1 retains validation of its legacy EI parameters; v2 exposes
                # only the parameters that the MCP branch actually consumes.
                chey_parameters = pts.SignalParameters if node.module_version == '1.0.0' else science.CheYParameters
                chey = science.advance_chey_from_activity(value.activity, chey_old, 0. if dt is None else dt, parameters(node, chey_parameters))
                outputs[n] = {'activity': value.activity, 'adaptation': value.methylation, 'chey_p': chey,
                              'motor_bias': science.motor_bias(chey, half_uM=p['motor_half_um'], hill=p['motor_hill'])}
                state[n] = {'adaptation': value.methylation, 'chey_p': chey}

    def _motion_outputs(self, outputs, state, world, walks, blocked, counts):
        for gid, node in self._motion_nodes.items():
            group = world.groups[gid]
            heading = np.asarray([walks[c].heading for c in group.ids]).reshape(-1, 3)
            phase = np.asarray([float(walks[c].phase == 'tumble') for c in group.ids])
            hazard = np.asarray([walks[c].remaining_hazard or 0. for c in group.ids])
            outputs[node.id] = {'position': group.positions_um, 'heading': heading,
                'blocked': np.asarray([float(c in blocked) for c in group.ids]),
                'turns': np.asarray([counts.get(c, 0) for c in group.ids]),
                'tumble_phase': phase, 'hazard_remaining': hazard}
            state[node.id] = {'heading': heading, 'tumble_phase': phase, 'remaining_hazard': hazard,
                              'dwell_remaining': np.asarray([walks[c].dwell_remaining_s for c in group.ids])}
            manifest = self.registry.get(node.module_id, node.module_version).manifest
            outputs[node.id] = {k: v for k, v in outputs[node.id].items() if k in manifest['outputs']}
            state[node.id] = {k: v for k, v in state[node.id].items() if k in manifest['state']}

    def _move(self, dt, streams):
        start, proposals, speeds, phases, events = self._capsules(self.world), {}, {}, {}, {}
        kernels, references = {}, {}
        remaining = MAX_TUMBLES
        for gid in sorted(self.world.groups):
            node = self._motion_nodes[gid]
            p = {k: v.value for k, v in node.parameters.items()}
            bias = self._input(node, 'motor_bias', self.outputs) if 'motor_bias' in node.inputs else np.zeros(len(self.world.groups[gid].ids))
            rates = science.tumble_hazard(bias, minimum_s=p.get('minimum_tumble_rate_s', p.get('tumble_rate_s', 0)),
                                          maximum_s=p.get('maximum_tumble_rate_s', p.get('tumble_rate_s', 0)))
            for i, cid in enumerate(self.world.groups[gid].ids):
                par = HazardWalkParameters(p['speed_um_s'], float(rates[i]), 2 if self.world.grid.geometry == 'thin_layer' else 3,
                    p.get('tumble_mode', 'instant'), p.get('tumble_duration_s', 0) if p.get('tumble_mode') == 'dwell' else 0., p.get('turn_kernel', 'isotropic'))
                proposal = advance_hazard_walk(self.world.groups[gid].positions_um[i], self.walks[cid], dt, par, streams,
                    node_id=node.id, group_id=gid, cell_id=cid, max_events=remaining)
                remaining -= proposal.event_count
                proposals[cid], speeds[cid], phases[cid] = proposal, par.speed_um_s, self.walks[cid].phase
                kernels[cid], references[cid] = par.turn_kernel, self.walks[cid].heading
                for event in proposal.events:
                    events.setdefault(event.time_s, {})[cid] = event
        pairs = len(start) * (len(start) + 1) // 2 + len(start) * len(self.obstacles)
        if pairs * (2 * len(events) + 1) > MAX_MOTION_PAIRS:
            raise SimulationError('chemotaxis.motion_budget', 'Motion pair budget exceeded')
        current, elapsed, blocked, contacts = start, 0., set(), []
        for boundary in sorted(set(events) | {dt}):
            interval = boundary - elapsed
            if interval > 0:
                end = tuple(replace(c, position_um=tuple(x + (speeds[c.cell_id] if phases[c.cell_id] == 'run' else 0.) * interval * h
                    for x, h in zip(c.position_um, c.heading))) for c in current)
                guarded = self._guard(current, end)
                current = guarded.capsules
                blocked.update(guarded.blocked_ids)
                contacts.extend(guarded.contacts)
            if boundary in events:
                rotated = []
                for capsule in current:
                    cid = capsule.cell_id
                    if cid not in events[boundary]:
                        rotated.append(capsule)
                        continue
                    event = events[boundary][cid]
                    if phases[cid] == 'tumble':
                        heading = capsule.heading  # dwell exit does not turn again
                    elif kernels[cid] == 'isotropic':
                        heading = event.heading
                    else:
                        heading = self._relative_turn(capsule.heading, references[cid], event.heading)
                    references[cid] = event.heading
                    rotated.append(replace(capsule, heading=heading))
                end = tuple(rotated)
                guarded = self._guard(current, end)
                current = guarded.capsules
                blocked.update(guarded.blocked_ids)
                contacts.extend(guarded.contacts)
                for cid, event in events[boundary].items():
                    phases[cid] = event.phase
            elapsed = boundary
        by_id = {c.cell_id: c for c in current}
        walks = {cid: replace(proposal.state, heading=by_id[cid].heading) for cid, proposal in proposals.items()}
        groups = {}
        for gid, group in self.world.groups.items():
            headings = np.asarray([by_id[c].heading for c in group.ids]).reshape(-1, 3)
            groups[gid] = CellGroup(gid, group.ids, np.asarray([by_id[c].position_um for c in group.ids]).reshape(-1, 3),
                orientation_after_heading(group.orientation_xyzw, headings), group.geometry)
        return (World(self.world.grid, groups, self.world.species_initial_uM, self.world.schedules), MappingProxyType(walks), blocked,
                {cid: proposal.event_count for cid, proposal in proposals.items()}, tuple(contacts))

    def _relative_turn(self, actual, reference, proposed):
        """Apply a sampled angle/azimuth to the collision-accepted heading.

        The clock proposal cannot know which prior rotations the guard blocks.
        Preserve its random angle and azimuth, not its unguarded world vector.
        """
        if self.world.grid.geometry == 'thin_layer':
            cosine = reference[0] * proposed[0] + reference[1] * proposed[1]
            sine = reference[0] * proposed[1] - reference[1] * proposed[0]
            value = np.asarray((cosine * actual[0] - sine * actual[1], sine * actual[0] + cosine * actual[1], 0.))
        else:
            def basis(heading):
                heading = np.asarray(heading)
                axis = np.asarray((1., 0., 0.) if abs(heading[0]) < .8 else (0., 1., 0.))
                v = axis - np.dot(axis, heading) * heading
                v /= np.linalg.norm(v)
                return v, np.cross(heading, v)
            v, w = basis(reference)
            a, b = basis(actual)
            value = np.dot(reference, proposed) * np.asarray(actual) + np.dot(v, proposed) * a + np.dot(w, proposed) * b
        value /= np.linalg.norm(value)
        return tuple(value)

    @staticmethod
    def _with_metrics(snapshot, observation):
        from .observations import observation_metrics
        return replace(snapshot, metrics=observation_metrics(observation, snapshot.cell_frame))

    def step(self, dt_s):
        if type(dt_s) not in (int, float) or not math.isfinite(dt_s) or dt_s <= 0:
            raise SimulationError('time.step', 'dt_s must be positive and finite')
        next_time = self.time_s + dt_s
        if not math.isfinite(next_time) or next_time <= self.time_s:
            raise SimulationError('time.overflow', 'Simulation time must advance')
        try:
            outputs, state = self._prepare()
            materials, material_ledger, contact_sources, released = self._degrade(dt_s)
            ids, positions = [], []
            for gid in sorted(self.world.groups):
                ids.extend(self.world.groups[gid].ids)
                positions.extend(self.world.groups[gid].positions_um)
            indices = {cid: i for i, cid in enumerate(ids)}
            requested = {s: np.zeros(len(ids)) for s in self.fields.concentrations_uM}
            for node in self.plan.nodes:
                if node.module_id == 'uptake.local_settlement':
                    species = node.parameters['species'].value
                    for cid, value in zip(self.world.groups[node.owner_id].ids, self._input(node, 'requested_flux', outputs), strict=True):
                        requested[species][indices[cid]] = value
            finite_requested = {s: values if s not in self.supply_totals else np.zeros(len(ids)) for s, values in requested.items()}
            field_input = replace(self.fields, sources=self.fields.sources + contact_sources)
            field_proposal = propose_local_field_step(field_input, dt_s, cell_ids=ids,
                positions_um=np.asarray(positions).reshape(-1, 3), requested_uptake_molecules_s=finite_requested)
            if any(s.remaining_molecules != 0 for s in field_proposal.after.sources if s.id not in self._source_nodes):
                raise SimulationError('chemotaxis.release', 'Contact products failed to enter bulk')
            accepted = dict(field_proposal.accepted_uptake_molecules)
            supplies = dict(self.supply_totals)
            for species in supplies:
                amount = requested[species] * dt_s
                if not np.isfinite(amount).all() or np.any((requested[species] > 0) & (amount == 0)):
                    raise SimulationError('profile.precision', 'Reservoir uptake is not representable')
                total = math.fsum(float(v) for v in amount)
                after = supplies[species] + total
                _check_transfer_rounding(supplies[species], after, total)
                supplies[species] = after
                accepted[species] = tuple(float(v) for v in amount)
            field_proposal = replace(field_proposal, accepted_uptake_molecules=MappingProxyType(accepted))
            committed_fields = replace(field_proposal.after, sources=tuple(s for s in field_proposal.after.sources if s.id in self._source_nodes))
            self._settle_outputs(outputs, state, field_proposal, dt_s)
            uptake_totals = dict(self.uptake_totals)
            for nid, total in uptake_totals.items():
                moved = math.fsum(float(v) for v in outputs[nid]['accepted_amount'])
                uptake_totals[nid] = total + moved
                _check_transfer_rounding(total, uptake_totals[nid], moved)
            self._signal_outputs(outputs, state, dt_s)
            streams = self.streams.clone()
            world, walks, blocked, turns, contacts = self._move(dt_s, streams)
            self._motion_outputs(outputs, state, world, walks, blocked, turns)
            world, walks, events, biology, dead_material, next_id = self._physiology(world, walks, outputs, state, dt_s, streams, next_time)
            next_obstacles, next_mask = self._geometry_for(materials)
            committed_fields = replace(committed_fields, blocked=next_mask)
            if supplies:
                concentration = dict(committed_fields.concentrations_uM)
                for species in supplies:
                    fixed = self.project['species'][species]['initial_concentration']['value']
                    target = tuple(0. if solid else float(fixed) for solid in next_mask)
                    added = math.fsum(new - old for new, old in zip(target, concentration[species])) * world.grid.molecules_per_uM_voxel
                    if added < 0:
                        raise SimulationError('chemotaxis.reservoir', 'Ideal background would discard material')
                    before = supplies[species]
                    supplies[species] += added
                    _check_transfer_rounding(before, supplies[species], added)
                    concentration[species] = target
                committed_fields = replace(committed_fields, concentrations_uM=concentration)
            self._motion_outputs(outputs, state, world, walks, blocked, turns)
            for node in self._field_nodes.values():
                species = node.parameters['species'].value
                outputs[node.id] = {'concentration': np.asarray(committed_fields.concentrations_uM[species]).reshape(world.grid.shape)}
                if species in supplies:
                    outputs[node.id]['cumulative_supply'] = supplies[species]
                    state[node.id] = {'cumulative_supply': supplies[species]}
                else:
                    state[node.id] = dict(outputs[node.id])
            for source in committed_fields.sources:
                outputs[source.id] = state[source.id] = {'inventory': source.remaining_molecules}
            for mid, material in materials.items():
                outputs[mid] = state[mid] = {'inventory': material.remaining_molecules}
            if self._degradation_node is not None:
                outputs[self._degradation_node.id] = {'released_amount': released}
            self._validate_arrays(world, outputs, state)
            outputs, state = _freeze(outputs), _freeze(state)
            snapshot = self._snapshot(world, committed_fields, outputs, next_time, self.frame_index + 1, materials)
            snapshot.cell_frame['events'] = events
            snapshot = replace(snapshot, lifecycle_details={'lifecycle_version': '0.1.0',
                'deaths': [{'cell_id': event['cell_id'], 'time_s': next_time,
                            'group_id': dead_material[event['cell_id']]['group_id'], **dead_material[event['cell_id']]['death_rule']}
                           for event in events if event['type'] == 'death']})
            validator = deepcopy(self.frame_validator)
            validator.accept(snapshot.cell_frame)
            from .observations import advance_observation
            observation = advance_observation(self.observation_state, snapshot.cell_frame)
            snapshot = self._with_metrics(snapshot, observation)
        except SimulationError:
            raise
        except (ValueError, OverflowError, FloatingPointError) as error:
            raise SimulationError('chemotaxis.step_rejected', str(error)) from error
        self.world, self.fields, self.walks, self.streams = world, committed_fields, walks, streams
        self.materials, self.material_ledger, self.obstacles = materials, material_ledger, next_obstacles
        self.outputs, self.state = outputs, state
        self.ledger = MappingProxyType({s: value for s, value in field_proposal.ledgers.items() if s not in supplies})
        self.supply_totals, self.physiology_ledger = supplies, biology
        self.uptake_totals = uptake_totals
        self.dead_material, self.next_cell_index = dead_material, next_id
        self.motion_contacts, self.observation_state = contacts, observation
        self.current, self.frame_validator = snapshot, validator
        self.time_s, self.frame_index = next_time, self.frame_index + 1
        self.last_dt_s = dt_s
        return snapshot

    def _validate_arrays(self, world, outputs, state):
        if set(outputs) != set(self.plan.by_id) or set(state) != set(self.plan.by_id):
            raise SimulationError('chemotaxis.outputs', 'Every graph node must own its declared state and output')
        for node in self.plan.nodes:
            manifest = self.registry.get(node.module_id, node.module_version).manifest
            for values, declaration in ((outputs[node.id], manifest['outputs']), (state[node.id], manifest['state'])):
                if set(values) != set(declaration):
                    raise SimulationError('chemotaxis.outputs', f'{node.id}: output/state ports differ from manifest')
                for name, raw in values.items():
                    array = np.asarray(raw)
                    shape = declaration[name]['shape']
                    count = len(world.groups[node.owner_id].ids) if node.owner_kind == 'population' else None
                    expected = world.grid.shape if shape == 'field.scalar' else (count, 3) if shape == 'cell.vector' else (count,) if shape.startswith('cell.') else ()
                    if array.shape != expected or not np.isfinite(array).all():
                        raise SimulationError('chemotaxis.outputs', f'{node.id}.{name}: invalid shape or nonfinite values')

    def checkpoint(self):
        from .chemotaxis_checkpoint import export_checkpoint
        return export_checkpoint(self)

    @classmethod
    def from_checkpoint(cls, project, payload, registry=None):
        from .chemotaxis_checkpoint import restore_checkpoint
        return restore_checkpoint(project, payload, registry)
