"""Atomic local-field/PTS transactions followed by synchronous unbiased motion.

Concentration requests use step-start positions and fields. Release, diffusion
and shared settlement finish before motion. Tumbles are instantaneous guarded
rotations; straight runs are guarded together between global event boundaries.
"""
from copy import deepcopy
from dataclasses import replace
import math
from types import MappingProxyType

import numpy as np

from friskoli_cad.protocol import ProtocolError, FrameSequenceValidator
from friskoli_cad.science import pts
from .collision import Capsule, BoxObstacle, guard_motion, InitialOverlapError, capsule_box_gap
from .degradation import DegradableBox, DegradationProposal
from .settlement import SettlementLedger
from .compiler import compile_graph
from .local_fields import (FieldSpecies, LocalSource, SolidAABB, make_local_field_state,
    copy_concentrations, sample_local_fields, propose_local_field_step,
    local_field_state_to_dict, local_field_state_from_dict)
from .motion import heading_from_orientation, orientation_after_heading
from .profiles import SPATIAL_PROFILE, spatial_schedule
from .pts_runtime import parameters, _freeze, _check_transfer_rounding
from .random_streams import RandomStreams
from .random_walk import RandomWalkParameters, RandomWalkState, advance_random_walk
from .runtime import CellGroup, World, SimulationError, Snapshot

MAX_CELLS, MAX_VOXELS, MAX_SPECIES = 256, 10_000, 8
MAX_TUMBLES, MAX_MOTION_PAIRS = 2048, 2_000_000


def _problem(message, path='/graph', code='spatial.contract'):
    raise ProtocolError(code, path, message)


def _degradation_providers(plan, registry):
    providers = []
    for node in plan.nodes:
        module = registry.get(node.module_id, node.module_version)
        if 'material.degradation' not in getattr(module, 'provides_roles', ()):
            continue
        expected = {'shape': 'global.scalar', 'quantity': 'released_amount', 'unit': 'molecule'}
        if (not callable(getattr(module, 'propose_degradation', None)) or node.owner_kind != 'environment'
                or module.manifest['outputs'] != {'released_amount': expected}
                or module.manifest['inputs'] or module.manifest['state'] or node.phase >= 4
                or 'contact_range_um' not in node.parameters):
            _problem('Degradation role requires a registered pure adapter, contact_range_um and standard environment release interface', code='spatial.degradation')
        parameter = module.manifest['parameters']['contact_range_um']
        distance = node.parameters['contact_range_um'].value
        if (parameter.get('type') != 'number' or parameter.get('unit') != 'um'
                or type(distance) not in (int, float) or not math.isfinite(distance) or distance < 0):
            _problem('Degradation contact range must be finite nonnegative numeric um', code='spatial.degradation')
        providers.append(node)
    return providers


def validate_spatial_project(project, manifests, registry=None):
    """Enforce the resource ownership and timing the generic port types cannot."""
    if project.get('execution_profile') != SPATIAL_PROFILE or project['controls']:
        _problem('This profile requires spatial-unbiased-v1 and no external schedules')
    if sum(len(g['ids']) for g in project['groups'].values()) > MAX_CELLS:
        _problem('Spatial reference profile supports at most 256 cells', '/groups', 'spatial.resource')
    if math.prod(project['domain']['counts_xyz']) > MAX_VOXELS or len(project['species']) > MAX_SPECIES:
        _problem('Spatial reference grid/species budget exceeded', '/domain', 'spatial.resource')
    plan = compile_graph(project['graph'], manifests)
    if registry is None:
        from .spatial_modules import spatial_registry
        registry = spatial_registry()
    fields = {n.id: n for n in plan.nodes if n.module_id == 'field.diffusive_local'}
    sources = {n.id: n for n in plan.nodes if n.module_id == 'source.finite_local'}
    motions = [n for n in plan.nodes if n.module_id == 'motion.unbiased_run_tumble']
    if sorted(n.owner_id for n in motions) != sorted(project['groups']):
        _problem('Every population requires exactly one registered unbiased motion module')
    if set(n.owner_id for n in plan.nodes if n.owner_kind == 'population') != set(project['groups']):
        _problem('Population ownership differs from project groups')
    species = [n.parameters['species'].value for n in fields.values()]
    if len(set(species)) != len(species) or set(species) != set(project['species']):
        _problem('Every declared species requires exactly one local field', code='spatial.field_owner')
    materials = [n for n in plan.nodes if n.module_id == 'material.degradable_box']
    degradation = _degradation_providers(plan, registry)
    enzymes = [n for n in plan.nodes if n.module_id == 'surface.enzyme_activity']
    if len(degradation) > 1 or (materials and (len(degradation) != 1 or not enzymes)):
        _problem('Material requires one registered global degradation provider and explicit population surface enzyme', code='spatial.degradation')
    if len({n.owner_id for n in enzymes}) != len(enzymes):
        _problem('Each population has at most one surface enzyme budget', code='spatial.enzyme_owner')
    intracellular = set()
    objects = [n for n in plan.nodes if n.module_id in ('source.finite_local', 'space.axis_aligned_obstacle', 'material.degradable_box')]
    if len({n.owner_id for n in objects}) != len(objects):
        _problem('Registered physical object owner IDs must be unique')
    for node in plan.nodes:
        for binding in node.inputs.values():
            source = plan.by_id[binding.source_node]
            expected = 'previous_step' if source.module_id in (
                'field.diffusive_local', 'source.finite_local', 'motion.unbiased_run_tumble') else 'same_step'
            if binding.timing != expected:
                _problem(f'{node.id}: {source.id} must use {expected}', code='spatial.timing')
        p = node.parameters
        try:
            if node.module_id == 'pts.capacity_rebuilt':
                parameters(node, pts.RebuiltCapacityParameters)
            elif node.module_id == 'pts.capacity_simplified':
                parameters(node, pts.SimplifiedCapacityParameters)
            elif node.module_id == 'uptake.pts_request':
                pts.pts_request(0., 0., p['turnover_s'].value, p['half_saturation_um'].value)
            elif node.module_id == 'signal.pts_accepted':
                pts.signal_readout(p['initial_ei_fraction'].value, p['initial_chey_p_um'].value,
                                   parameters(node, pts.SignalParameters))
        except ValueError as error:
            _problem(f'{node.id}: {error}', code='spatial.parameter')
        if node.module_id in ('source.finite_local', 'material.degradable_box'):
            if p['species'].value not in species:
                _problem('Every soluble source/material species requires one registered local field')
        elif node.module_id == 'field.sample_local':
            motion = plan.by_id[node.inputs['position'].source_node]
            if node.inputs['field'].source_node not in fields or motion.module_id != 'motion.unbiased_run_tumble' or motion.owner_id != node.owner_id:
                _problem('Local sample requires a field and its own population motion output')
        elif node.module_id == 'uptake.local_settlement':
            owner = node.owner_id, p['species'].value
            if owner in intracellular:
                _problem('A population/species cannot have multiple uptake owners')
            intracellular.add(owner)
            field_id = node.inputs['field'].source_node
            request = plan.by_id[node.inputs['requested_flux'].source_node]
            if field_id not in fields or request.module_id != 'uptake.pts_request':
                _problem('Local settlement must bind a local field and PTS request')
            sample = plan.by_id[request.inputs['concentration'].source_node]
            if sample.module_id != 'field.sample_local' or sample.inputs['field'].source_node != field_id:
                _problem('Request and settlement must use the same local field', code='spatial.support')
        elif node.module_id == 'signal.pts_accepted':
            if plan.by_id[node.inputs['accepted_flux'].source_node].module_id != 'uptake.local_settlement':
                _problem('PTS signal requires accepted local uptake')


def _xyz(node, prefix):
    return tuple(node.parameters[f'{prefix}_{a}_um'].value for a in 'xyz')


class SpatialSimulation:
    MOTION_MODULE_IDS = ('motion.unbiased_run_tumble',)
    FIELD_MODULE_IDS = ('field.diffusive_local',)

    def _validate_project(self, project, registry):
        validate_spatial_project(project, registry.manifests, registry)

    def _execution_schedule(self):
        return spatial_schedule(self.plan, self.registry)

    def _initial_walks(self, capsules):
        return {c.cell_id: RandomWalkState(c.heading) for c in capsules}

    def _prepare_node_ids(self):
        return self.schedule['prepare_nodes']

    def _initial_field_species(self):
        return [FieldSpecies(n.parameters['species'].value,
            self.project['species'][n.parameters['species'].value]['initial_concentration']['value'],
            n.parameters['diffusivity_um2_s'].value) for n in self._field_nodes.values()]

    def __init__(self, world, project, registry, *, seed=None):
        self._validate_project(project, registry)
        self.project, self.run = deepcopy(project), deepcopy(project['run'])
        self.world, self.registry = world, registry
        self.plan = compile_graph(project['graph'], registry.manifests)
        self.schedule = self._execution_schedule()
        self.seed = project['random_seed'] if seed is None else seed
        self.streams = RandomStreams(self.seed)
        self.time_s, self.frame_index = 0., 0
        self._motion_nodes = {n.owner_id: n for n in self.plan.nodes if n.module_id in self.MOTION_MODULE_IDS}
        self._field_nodes = {n.id: n for n in self.plan.nodes if n.module_id in self.FIELD_MODULE_IDS}
        self._source_nodes = {n.id: n for n in self.plan.nodes if n.module_id == 'source.finite_local'}
        self._material_nodes = {n.id: n for n in self.plan.nodes if n.module_id == 'material.degradable_box'}
        self._degradation_node = next(iter(_degradation_providers(self.plan, registry)), None)
        self._enzyme_nodes = {n.owner_id: n for n in self.plan.nodes if n.module_id == 'surface.enzyme_activity'}
        obstacle_nodes = [n for n in self.plan.nodes if n.module_id in ('space.axis_aligned_obstacle', 'material.degradable_box')]
        self._fixed_obstacle_nodes = tuple(n for n in obstacle_nodes if n.module_id == 'space.axis_aligned_obstacle')
        self.materials = MappingProxyType({n.id: DegradableBox(n.id, n.parameters['species'].value,
            _xyz(n, 'lower'), _xyz(n, 'upper'), n.parameters['initial_molecules'].value) for n in self._material_nodes.values()})
        self.material_ledger = MappingProxyType({})
        # Validate even initially exhausted material bounds before omitting them.
        make_local_field_state(world.grid, [], obstacles=[SolidAABB(_xyz(n, 'lower'), _xyz(n, 'upper')) for n in obstacle_nodes])
        self.obstacles, _ = self._geometry_for(self.materials)
        active_nodes = list(self._fixed_obstacle_nodes) + [n for n in self._material_nodes.values() if self.materials[n.id].remaining_molecules > 0]
        fields = self._initial_field_species()
        sources = [LocalSource(n.id, n.parameters['species'].value, _xyz(n, 'center'),
            n.parameters['radius_um'].value, n.parameters['initial_molecules'].value,
            n.parameters['release_rate'].value) for n in self._source_nodes.values()]
        self.fields = make_local_field_state(world.grid, fields, sources=sources,
            obstacles=[SolidAABB(_xyz(n, 'lower'), _xyz(n, 'upper')) for n in active_nodes],
            max_voxels=MAX_VOXELS, max_values=MAX_VOXELS * MAX_SPECIES * 2)
        capsules = self._capsules(world)
        try:
            self._guard(capsules, capsules)
        except InitialOverlapError as error:
            details = '; '.join(f'{c.kind}: {",".join(c.cell_ids)} / {c.target_id}' for c in error.contacts)
            raise SimulationError('spatial.initial_overlap', details, '/groups') from error
        except ValueError as error:
            raise SimulationError('spatial.geometry', str(error), '/domain') from error
        self.walks = MappingProxyType(self._initial_walks(capsules))
        self.outputs, self.state, self.ledger, self.motion_contacts = {}, {}, MappingProxyType({}), ()
        outputs, state = self._prepare()
        self._settle_outputs(outputs, state, None, None)
        self._signal_outputs(outputs, state, None)
        self._motion_outputs(outputs, state, world, self.walks, set(), {})
        self.outputs, self.state = _freeze(outputs), _freeze(state)
        self.current = self._snapshot(world, self.fields, self.outputs, 0., 0)
        self.frame_validator = FrameSequenceValidator(self.run)
        self.frame_validator.accept(self.current.cell_frame)

    def _capsules(self, world):
        result = []
        for gid in sorted(world.groups):
            group = world.groups[gid]
            headings = heading_from_orientation(group.orientation_xyzw)
            for i, cid in enumerate(group.ids):
                geometry = group.geometry[i]
                if geometry is None:
                    raise SimulationError('geometry.missing', 'Spatial motion requires capsule geometry', f'/groups/{gid}')
                heading = headings[i]
                if world.grid.geometry == 'thin_layer':
                    if abs(heading[2]) > 1e-12:
                        raise SimulationError('spatial.planar_heading', 'Thin-layer motion requires a planar initial heading', f'/groups/{gid}')
                    heading = np.array([heading[0], heading[1], 0.])
                    heading /= np.linalg.norm(heading)
                result.append(Capsule(cid, group.positions_um[i], heading, geometry.length_um, geometry.diameter_um))
        return tuple(result)

    def _guard(self, start, end):
        return guard_motion(start, end, extent_um=self.world.grid.extent_um,
                            obstacles=self.obstacles, geometry=self.world.grid.geometry)

    def _geometry_for(self, materials):
        """Partial materials keep their box; exhausted boxes disappear at commit."""
        nodes = list(self._fixed_obstacle_nodes) + [n for n in self._material_nodes.values() if materials[n.id].remaining_molecules > 0]
        obstacles = tuple(BoxObstacle(n.owner_id, _xyz(n, 'lower'), _xyz(n, 'upper')) for n in nodes)
        mask = make_local_field_state(self.world.grid, [],
            obstacles=[SolidAABB(_xyz(n, 'lower'), _xyz(n, 'upper')) for n in nodes], max_voxels=MAX_VOXELS).blocked
        return obstacles, mask

    def _prepare(self):
        outputs, state = {}, {}
        samples = {gid: sample_local_fields(self.fields, g.positions_um) for gid, g in self.world.groups.items()}
        stocks = {s.id: s.remaining_molecules for s in self.fields.sources}
        for nid in self._prepare_node_ids():
            n = self.plan.by_id[nid]
            p, m = n.parameters, n.module_id
            if m == 'space.axis_aligned_obstacle':
                value = {'volume': math.prod(b - a for a, b in zip(_xyz(n, 'lower'), _xyz(n, 'upper')))}
            elif m == 'source.finite_local':
                value = {'inventory': stocks[nid]}
                state[nid] = value
            elif m == 'material.degradable_box':
                value = {'inventory': self.materials[nid].remaining_molecules}
                state[nid] = value
            elif self._degradation_node is not None and nid == self._degradation_node.id:
                value = {'released_amount': 0.}
            elif m == 'surface.enzyme_activity':
                value = {'enzyme_copies': np.full(len(self.world.groups[n.owner_id].ids), p['enzyme_copies'].value)}
            elif m == 'field.diffusive_local':
                value = {'concentration': np.asarray(self.fields.concentrations_uM[p['species'].value]).reshape(self.world.grid.shape)}
                state[nid] = value
            elif m == 'pts.capsule_area':
                group = self.world.groups[n.owner_id]
                value = {'surface_area': pts.capsule_area_um2([c.length_um for c in group.geometry], [c.diameter_um for c in group.geometry])}
            elif m in ('pts.capacity_rebuilt', 'pts.capacity_simplified'):
                binding = n.inputs['surface_area']
                area = outputs[binding.source_node][binding.source_port]
                cls, fn = (pts.RebuiltCapacityParameters, pts.rebuilt_capacity) if m.endswith('rebuilt') else (pts.SimplifiedCapacityParameters, pts.simplified_capacity)
                cap = fn(area, p['g_requested'].value, parameters(n, cls))
                value = {'functional_copies': cap.functional_copies, 'g_effective': cap.g_effective}
            elif m == 'field.sample_local':
                s = samples[n.owner_id]
                value = {'concentration': s.concentration_uM[p['species'].value],
                         'gradient': np.asarray(s.gradient_uM_um[p['species'].value]).reshape(-1, 3)}
            elif m == 'uptake.pts_request':
                inputs = {name: outputs[b.source_node][b.source_port] for name, b in n.inputs.items()}
                value = {'requested_flux': pts.pts_request(inputs['concentration'], inputs['functional_copies'],
                          p['turnover_s'].value, p['half_saturation_um'].value)}
            else:
                raise SimulationError('spatial.module', f'Unsupported spatial mechanism {m}')
            outputs[nid] = value
            state.setdefault(nid, {})
        return outputs, state

    def _settle_outputs(self, outputs, state, proposal, dt):
        cell_ids = [cid for gid in sorted(self.world.groups) for cid in self.world.groups[gid].ids]
        index = {cid: i for i, cid in enumerate(cell_ids)}
        for n in self.plan.nodes:
            if n.module_id != 'uptake.local_settlement':
                continue
            ids = self.world.groups[n.owner_id].ids
            species = n.parameters['species'].value
            amount = np.zeros(len(ids)) if proposal is None else np.asarray([proposal.accepted_uptake_molecules[species][index[cid]] for cid in ids])
            before = np.full(len(ids), n.parameters['initial_molecules'].value) if dt is None else self.state[n.id]['cumulative_uptake']
            after = before + amount
            for old, new, moved in zip(before, after, amount, strict=True):
                if not math.isfinite(float(new)):
                    raise SimulationError('profile.precision', 'Cumulative uptake overflows')
                _check_transfer_rounding(old, new, moved)
            outputs[n.id] = {'accepted_amount': amount, 'accepted_flux': amount if dt is None else amount / dt,
                             'cumulative_uptake': after}
            state[n.id] = {'cumulative_uptake': after}

    def _signal_outputs(self, outputs, state, dt):
        for nid in self.schedule['signal_nodes']:
            n = self.plan.by_id[nid]
            count = len(self.world.groups[n.owner_id].ids)
            p = parameters(n, pts.SignalParameters)
            if dt is None:
                result = pts.signal_readout(np.full(count, n.parameters['initial_ei_fraction'].value),
                    np.full(count, n.parameters['initial_chey_p_um'].value), p)
            else:
                binding = n.inputs['accepted_flux']
                result = pts.advance_accepted_signal(outputs[binding.source_node][binding.source_port],
                    self.state[nid]['ei_fraction'], self.state[nid]['chey_p'], dt, p)
            outputs[nid] = {'ei_fraction': result.ei_fraction, 'chey_p': result.chey_p_uM,
                           'chea_active': result.chea_active_uM, 'motor_bias': result.motor_bias}
            state[nid] = {'ei_fraction': result.ei_fraction, 'chey_p': result.chey_p_uM}

    def _motion_outputs(self, outputs, state, world, walks, blocked, counts):
        for gid, n in self._motion_nodes.items():
            group = world.groups[gid]
            headings = np.asarray([walks[cid].heading for cid in group.ids]).reshape(-1, 3)
            outputs[n.id] = {'position': group.positions_um, 'heading': headings,
                'blocked': np.asarray([float(cid in blocked) for cid in group.ids]),
                'turns': np.asarray([counts.get(cid, 0) for cid in group.ids])}
            # The manifest's finite numeric state uses zero for an undrawn clock;
            # exact None/zero distinction lives in the versioned walk checkpoint.
            state[n.id] = {'heading': headings, 'remaining_wait': np.asarray([
                walks[cid].remaining_wait_s or 0. for cid in group.ids])}

    def _move(self, dt, streams):
        start = self._capsules(self.world)
        motions, events, speeds = {}, {}, {}
        remaining = MAX_TUMBLES
        for gid in sorted(self.world.groups):
            node = self._motion_nodes[gid]
            par = RandomWalkParameters(node.parameters['speed_um_s'].value, node.parameters['tumble_rate_s'].value,
                                       2 if self.world.grid.geometry == 'thin_layer' else 3)
            for i, cid in enumerate(self.world.groups[gid].ids):
                proposal = advance_random_walk(self.world.groups[gid].positions_um[i], self.walks[cid], dt, par,
                    streams, node_id=node.id, group_id=gid, cell_id=cid, max_events=remaining)
                remaining -= proposal.event_count
                motions[cid], speeds[cid] = proposal, par.speed_um_s
                for time, heading in proposal.events:
                    events.setdefault(time, {})[cid] = heading
        pairs = len(start) * (len(start) + 1) // 2 + len(start) * len(self.obstacles)
        if pairs * (2 * len(events) + 1) > MAX_MOTION_PAIRS:
            raise SimulationError('spatial.motion_budget', 'Motion event/pair budget exceeded; reduce dt or cell count')
        current, blocked, contacts = start, set(), []
        elapsed = 0.
        for boundary in sorted(set(events) | {dt}):
            interval = boundary - elapsed
            if interval > 0:
                end = tuple(replace(c, position_um=tuple(x + speeds[c.cell_id] * interval * h
                    for x, h in zip(c.position_um, c.heading))) for c in current)
                guarded = self._guard(current, end)
                current = guarded.capsules
                blocked.update(guarded.blocked_ids)
                contacts.extend(guarded.contacts)
            if boundary in events:
                end = tuple(replace(c, heading=events[boundary].get(c.cell_id, c.heading)) for c in current)
                guarded = self._guard(current, end)
                current = guarded.capsules
                blocked.update(guarded.blocked_ids)
                contacts.extend(guarded.contacts)
            elapsed = boundary
        by_id = {c.cell_id: c for c in current}
        walks = {cid: RandomWalkState(by_id[cid].heading, p.state.remaining_wait_s) for cid, p in motions.items()}
        groups = {}
        for gid, group in self.world.groups.items():
            headings = np.asarray([by_id[cid].heading for cid in group.ids]).reshape(-1, 3)
            positions = np.asarray([by_id[cid].position_um for cid in group.ids]).reshape(-1, 3)
            groups[gid] = CellGroup(gid, group.ids, positions,
                orientation_after_heading(group.orientation_xyzw, headings), group.geometry)
        world = World(self.world.grid, groups, self.world.species_initial_uM, self.world.schedules)
        return world, MappingProxyType(walks), blocked, {cid: p.event_count for cid, p in motions.items()}, tuple(contacts)

    def _degrade(self, dt):
        """Pure material proposals, sharing each cell's enzyme across real contacts.

        Products enter the contacting cell's step-start fluid voxel. The capsule
        center is the grid's existing sampling/uptake support; no intracellular
        transfer occurs here. Exhausted boxes disappear only after this step.
        """
        if not self.materials:
            return self.materials, MappingProxyType({}), (), 0.
        capsules = self._capsules(self.world)
        node = self._degradation_node
        distance = node.parameters['contact_range_um'].value
        adapter = self.registry.get(node.module_id, node.module_version).propose_degradation
        copies = self._contact_enzyme_copies()
        contacts = {capsule.cell_id: [] for capsule in capsules}
        for mid in sorted(self.materials):
            material = self.materials[mid]
            if material.remaining_molecules == 0:
                continue
            box = BoxObstacle(material.id, material.lower_um, material.upper_um)
            for capsule in capsules:
                if copies[capsule.cell_id] > 0 and capsule_box_gap(capsule, box) <= distance:
                    contacts[capsule.cell_id].append(mid)
        remaining, ledgers, temporary = {}, {}, []
        positions = {c.cell_id: c.position_um for c in capsules}
        released = []
        for mid in sorted(self.materials):
            budgets = {cid: copies[cid] / len(targets) if mid in targets else 0. for cid, targets in contacts.items()}
            parameters = self._degradation_parameters(node, mid)
            proposal = adapter(self.materials[mid], capsules, MappingProxyType(budgets), MappingProxyType(parameters), dt)
            self._validate_degradation(proposal, self.materials[mid], budgets, contacts, mid)
            remaining[mid], ledgers[mid] = proposal.material, proposal.ledger
            released.append(proposal.released_molecules)
            for cid in sorted(proposal.contributions_molecules):
                amount = proposal.contributions_molecules[cid]
                if amount:
                    # Tuple IDs are encoded by repr to avoid separator collisions.
                    sid = '__contact_release__' + repr((mid, cid))
                    if sid in self._source_nodes:
                        raise SimulationError('spatial.source_id', 'Source ID collides with internal contact release namespace')
                    temporary.append(LocalSource(sid, proposal.material.species, positions[cid], 0., amount, amount / dt))
        return MappingProxyType(remaining), MappingProxyType(ledgers), tuple(temporary), math.fsum(released)

    def _contact_enzyme_copies(self):
        return {cid: (self._enzyme_nodes[gid].parameters['enzyme_copies'].value if gid in self._enzyme_nodes else 0.)
                for gid, group in self.world.groups.items() for cid in group.ids}

    def _degradation_parameters(self, node, material_id):
        return {key: value.value for key, value in node.parameters.items()}

    @staticmethod
    def _validate_degradation(proposal, material, budgets, contacts, mid):
        """Validate adapter transfers before they can enter any runtime inventory."""
        if not isinstance(proposal, DegradationProposal) or not isinstance(proposal.material, DegradableBox):
            raise SimulationError('spatial.degradation', 'Registered adapter returned an invalid proposal type')
        result = proposal.material
        if ((result.id, result.species, result.lower_um, result.upper_um) !=
                (material.id, material.species, material.lower_um, material.upper_um)
                or result.remaining_molecules > material.remaining_molecules):
            raise SimulationError('spatial.degradation', 'Adapter changed material identity/configuration or increased stock')
        values = proposal.contributions_molecules
        if set(values) != set(budgets) or set(proposal.requested_molecules) != set(budgets):
            raise SimulationError('spatial.degradation', 'Adapter contribution IDs differ from cell IDs')
        for cid, value in values.items():
            request = proposal.requested_molecules[cid]
            if (type(value) not in (float, int) or not math.isfinite(value) or value < 0
                    or type(request) not in (float, int) or not math.isfinite(request) or request < value
                    or (value > 0 and (budgets[cid] <= 0 or mid not in contacts[cid]))):
                raise SimulationError('spatial.degradation', 'Adapter returned invalid or non-contact nutrient contributions')
        total = math.fsum(values.values())
        if (type(proposal.released_molecules) not in (float, int)
                or not math.isfinite(proposal.released_molecules) or proposal.released_molecules != total):
            raise SimulationError('spatial.degradation', 'Adapter release differs from its cell contributions')
        ledger = proposal.ledger
        if (not isinstance(ledger, SettlementLedger) or ledger.owner_id != mid or ledger.species != material.species
                or ledger.before != (material.remaining_molecules,) or ledger.after != (result.remaining_molecules,)
                or ledger.total_before != material.remaining_molecules or ledger.total_after != result.remaining_molecules
                or ledger.total_accepted != total):
            raise SimulationError('spatial.degradation', 'Adapter material ledger disagrees with transfer')
        _check_transfer_rounding(result.remaining_molecules, material.remaining_molecules, total)

    def _snapshot(self, world, fields, outputs, time, index, materials=None):
        cells = []
        for gid in sorted(world.groups):
            group = world.groups[gid]
            for i, cid in enumerate(group.ids):
                channels = {key: np.asarray(outputs[c['node']][c['port']][i]).tolist()
                    for key, c in self.run['channels'].items() if c['group_id'] == gid}
                geom = group.geometry[i]
                cells.append({'id': cid, 'group_id': gid, 'position_um': group.positions_um[i].tolist(),
                    'orientation_xyzw': group.orientation_xyzw[i].tolist(), 'geometry': {
                        'shape': 'capsule', 'length_um': geom.length_um, 'diameter_um': geom.diameter_um}, 'channels': channels})
        frame = {'protocol_version': '0.1.0', 'frame_version': '0.2.0', 'run_id': self.run['run_id'],
                 'frame_index': index, 'time_s': time, 'cells': cells, 'events': []}
        concentrations = copy_concentrations(fields)
        for value in concentrations.values():
            value.setflags(write=False)
        materials = self.materials if materials is None else materials
        objects = {source.id: MappingProxyType({'object_type': 'source.attractant',
            'remaining_molecules': source.remaining_molecules}) for source in fields.sources}
        objects.update({mid: MappingProxyType({'object_type': 'material.degradable_box',
            'remaining_molecules': material.remaining_molecules}) for mid, material in materials.items()})
        return Snapshot(frame, MappingProxyType(concentrations), MappingProxyType({s: 'uM' for s in concentrations}),
                        MappingProxyType({}), world.grid, object_states=MappingProxyType(objects))

    def step(self, dt_s):
        if type(dt_s) not in (int, float) or not math.isfinite(dt_s) or dt_s <= 0:
            raise SimulationError('time.step', 'dt_s must be positive and finite')
        next_time = self.time_s + dt_s
        if not math.isfinite(next_time) or next_time <= self.time_s:
            raise SimulationError('time.overflow', 'Simulation time must advance within float64 range')
        try:
            outputs, state = self._prepare()
            materials, material_ledger, contact_sources, contact_released = self._degrade(dt_s)
            ids, positions = [], []
            for gid in sorted(self.world.groups):
                ids.extend(self.world.groups[gid].ids)
                positions.extend(self.world.groups[gid].positions_um)
            index = {cid: i for i, cid in enumerate(ids)}
            requested = {s: np.zeros(len(ids)) for s in self.fields.concentrations_uM}
            for n in self.plan.nodes:
                if n.module_id != 'uptake.local_settlement':
                    continue
                b = n.inputs['requested_flux']
                flux = outputs[b.source_node][b.source_port]
                for cid, amount in zip(self.world.groups[n.owner_id].ids, flux, strict=True):
                    requested[n.parameters['species'].value][index[cid]] = amount
            field_input = replace(self.fields, sources=self.fields.sources + contact_sources)
            field_proposal = propose_local_field_step(field_input, dt_s, cell_ids=ids,
                positions_um=np.asarray(positions).reshape(-1, 3), requested_uptake_molecules_s=requested)
            if any(s.remaining_molecules != 0 for s in field_proposal.after.sources if s.id not in self._source_nodes):
                raise SimulationError('spatial.release', 'Contact release did not fully enter the soluble field')
            committed_fields = replace(field_proposal.after,
                sources=tuple(s for s in field_proposal.after.sources if s.id in self._source_nodes))
            self._settle_outputs(outputs, state, field_proposal, dt_s)
            self._signal_outputs(outputs, state, dt_s)
            streams = self.streams.clone()
            world, walks, blocked, turns, contacts = self._move(dt_s, streams)
            next_obstacles, next_mask = self._geometry_for(materials)
            committed_fields = replace(committed_fields, blocked=next_mask)
            self._motion_outputs(outputs, state, world, walks, blocked, turns)
            for n in self._field_nodes.values():
                outputs[n.id] = {'concentration': np.asarray(committed_fields.concentrations_uM[n.parameters['species'].value]).reshape(world.grid.shape)}
                state[n.id] = outputs[n.id]
            for source in committed_fields.sources:
                outputs[source.id] = {'inventory': source.remaining_molecules}
                state[source.id] = outputs[source.id]
            for mid, material in materials.items():
                outputs[mid] = {'inventory': material.remaining_molecules}
                state[mid] = outputs[mid]
            if self._degradation_node is not None:
                outputs[self._degradation_node.id] = {'released_amount': contact_released}
            outputs, state = _freeze(outputs), _freeze(state)
            snapshot = self._snapshot(world, committed_fields, outputs, next_time, self.frame_index + 1, materials)
            validator = deepcopy(self.frame_validator)
            validator.accept(snapshot.cell_frame)
        except SimulationError:
            raise
        except (ValueError, OverflowError, FloatingPointError) as error:
            raise SimulationError('spatial.step_rejected', str(error)) from error
        self.world, self.fields, self.walks, self.streams = world, committed_fields, walks, streams
        self.materials, self.material_ledger = materials, material_ledger
        self.obstacles = next_obstacles
        self.outputs, self.state, self.ledger = outputs, state, field_proposal.ledgers
        self.motion_contacts = contacts
        self.current, self.frame_validator = snapshot, validator
        self.time_s, self.frame_index = next_time, self.frame_index + 1
        return snapshot

    def checkpoint(self):
        """JSON library snapshot; task-service pause/resume remains unsupported."""
        from .spatial_checkpoint import export_checkpoint
        return export_checkpoint(self)

    @classmethod
    def from_checkpoint(cls, project, payload, registry=None):
        from .spatial_checkpoint import restore_checkpoint
        return restore_checkpoint(project, payload, registry)
