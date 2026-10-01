"""Version-locked JSON library checkpoints for the fixed-population spatial profile.

Checksums detect corruption, not hostile authorship. This does not implement a
task-service pause/resume operation or portable cross-version RNG replay.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields as dataclass_fields
from fractions import Fraction
import hashlib
import inspect
import math
from pathlib import Path
import platform
from types import MappingProxyType

import numpy as np
import rfc8785

from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import FrameSequenceValidator
from .local_fields import FieldLedger, local_field_state_from_dict, local_field_state_to_dict
from .degradation import DegradableBox
from .collision import Contact
from .settlement import SettlementLedger
from .motion import heading_from_orientation
from .profiles import SPATIAL_PROFILE
from .pts_runtime import _freeze
from .random_streams import RandomStreams
from .random_walk import RandomWalkState
from .runtime import CellGroup, SimulationError, World


CHECKPOINT_VERSION = "spatial-checkpoint/v2"
_REQUIRED_KEYS = frozenset(("version", "execution_profile", "project_sha256", "implementation_lock",
    "seed", "time_s", "frame_index", "world", "local_fields", "materials", "material_ledger",
    "object_states", "walks", "random_streams", "outputs", "state", "current_frame", "ledger",
    "payload_sha256"))
# Both extensions are absent in the original v2 writer. Accept that exact legacy
# shape, but never silently accept a partially missing extended checkpoint.
_EXTENSION_KEYS = frozenset(("frame_validator", "motion_contacts"))


def _reject(message):
    raise SimulationError("spatial.checkpoint", message)


def _hash(value):
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _keys(value, expected, label):
    if type(value) is not dict or set(value) != set(expected):
        _reject(f"{label} has missing or unknown fields")


def _validator_record(validator):
    return {"run_id": validator.run_id, "next_index": validator.next_index,
        "previous_time": validator.previous_time, "frame_version": validator.frame_version,
        "alive": dict(validator.alive), "seen": sorted(validator.seen)}


def _implementation_lock(registry):
    package = Path(__file__).resolve().parents[1]
    paths = [package / "project.py", package / "registry.py"]
    for directory in ("engine", "science", "protocol"):
        paths.extend((package / directory).rglob("*.py"))
    source_hashes = {path.relative_to(package).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted(set(paths))}
    registered = {}
    for (mid, version), module in sorted(registry._modules.items()):
        adapter = getattr(module, 'propose_degradation', None)
        if adapter is not None:
            qualified = getattr(adapter, '__qualname__', '')
            if not qualified or '<locals>' in qualified or '<lambda>' in qualified or getattr(adapter, '__closure__', None):
                _reject('Checkpoint requires a named top-level degradation adapter without a dynamic closure')
            source = inspect.getsourcefile(adapter)
            if source is None:
                _reject('Cannot lock a registered degradation adapter without an inspectable source file')
            registered[mid + '@' + version] = {'module': adapter.__module__, 'qualname': qualified,
                'source_sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest()}
    return {"source_sha256": source_hashes, "catalog_sha256": _hash(registry.catalog),
            'registered_adapter_sha256': registered,
            "numpy_version": np.__version__, "python_version": platform.python_version(),
            "machine": platform.machine(), "system": platform.system()}


def _nested_to_dict(nested):
    return {node: {port: np.asarray(value).tolist() for port, value in ports.items()}
            for node, ports in nested.items()}


def _record(value):
    return {key: list(item) if isinstance(item, tuple) else item for key, item in asdict(value).items()}


def export_checkpoint(sim):
    """Return only standard JSON values, with an exact-version implementation lock."""
    from .spatial_runtime import SpatialSimulation
    if not isinstance(sim, SpatialSimulation):
        _reject("checkpoint requires SpatialSimulation")
    payload = {"version": CHECKPOINT_VERSION, "execution_profile": SPATIAL_PROFILE,
        "project_sha256": _hash(sim.project), "implementation_lock": _implementation_lock(sim.registry),
        "seed": sim.seed, "time_s": sim.time_s, "frame_index": sim.frame_index,
        "world": {gid: {"ids": list(group.ids), "positions_um": group.positions_um.tolist(),
                         "orientation_xyzw": group.orientation_xyzw.tolist()}
                  for gid, group in sim.world.groups.items()},
        "local_fields": local_field_state_to_dict(sim.fields),
        "materials": {mid: _record(material) for mid, material in sim.materials.items()},
        "material_ledger": {mid: _record(ledger) for mid, ledger in sim.material_ledger.items()},
        "object_states": {mid: dict(values) for mid, values in sim.current.object_states.items()},
        "walks": {cid: walk.to_dict() for cid, walk in sim.walks.items()},
        "random_streams": sim.streams.to_dict(), "outputs": _nested_to_dict(sim.outputs),
        "state": _nested_to_dict(sim.state), "current_frame": deepcopy(sim.current.cell_frame),
        "ledger": {species: asdict(ledger) for species, ledger in sim.ledger.items()},
        "frame_validator": _validator_record(sim.frame_validator),
        "motion_contacts": [_record(contact) for contact in sim.motion_contacts]}
    payload["payload_sha256"] = _hash(payload)
    return payload


def _number(value, label, *, nonnegative=True):
    if type(value) not in (float, int) or not math.isfinite(value) or (nonnegative and value < 0):
        _reject(f"{label} must be a finite {'nonnegative ' if nonnegative else ''}number")
    return value


def _array(value, shape, label):
    # Validate numeric leaf types before NumPy can coerce strings or booleans.
    def check(entry, remaining):
        if not remaining:
            _number(entry, label, nonnegative=False)
        elif type(entry) is not list or len(entry) != remaining[0]:
            _reject(f"{label} has incompatible shape")
        else:
            for child in entry:
                check(child, remaining[1:])
    check(value, shape)
    return np.asarray(value, dtype=float).reshape(shape)


def _nested_from_dict(payload, template, label):
    if type(payload) is not dict or set(payload) != set(template):
        _reject(f"{label} node IDs differ from the project")
    result = {}
    for node, ports in template.items():
        given = payload[node]
        if type(given) is not dict or set(given) != set(ports):
            _reject(f"{label}.{node} port IDs differ from the project")
        result[node] = {port: _array(given[port], np.asarray(value).shape, f"{label}.{node}.{port}")
                        for port, value in ports.items()}
    return _freeze(result)


def _same(actual, expected, label):
    if not np.array_equal(actual, expected):
        _reject(f"{label} disagrees with checkpoint state")


def _restore_fields(sim, payload, index, materials):
    from .spatial_runtime import MAX_SPECIES, MAX_VOXELS
    template = local_field_state_to_dict(sim.fields)
    _keys(payload, template, "local_fields")
    _keys(payload["grid"], template["grid"], "local_fields.grid")
    if type(payload["sources"]) is not list:
        _reject("local_fields.sources must be a list")
    for source in payload["sources"]:
        _keys(source, ("id", "species", "center_um", "radius_um", "remaining_molecules",
                       "release_rate_molecules_s"), "local_fields source")
        _array(source["center_um"], (3,), "source center")
        for name in ("radius_um", "remaining_molecules", "release_rate_molecules_s"):
            _number(source[name], "source " + name)
    for species, initial_values in template["concentrations_uM"].items():
        _array(payload["concentrations_uM"][species], (len(initial_values),), "field concentration")
    for value in payload["diffusivities_um2_s"].values():
        _number(value, "diffusivity")
    if type(payload["blocked"]) is not list or any(type(v) is not bool for v in payload["blocked"]):
        _reject("field blocked mask must contain booleans")
    fields = local_field_state_from_dict(payload, max_voxels=MAX_VOXELS,
                                         max_values=MAX_VOXELS * MAX_SPECIES * 2)
    initial = sim.fields
    expected_mask = sim._geometry_for(materials)[1]
    if (fields.grid != initial.grid or fields.blocked != expected_mask
            or dict(fields.diffusivities_um2_s) != dict(initial.diffusivities_um2_s)
            or fields.revision != index):
        _reject("field grid/mask/species/diffusivity/revision differs from project or clock")
    if len(fields.sources) != len(initial.sources):
        _reject("finite source IDs differ from project")
    for now, before in zip(fields.sources, initial.sources):
        a, b = asdict(now), asdict(before)
        if a.pop("remaining_molecules") > b.pop("remaining_molecules") or a != b:
            _reject("finite source configuration changed or inventory exceeds project initial stock")
    if index == 0 and local_field_state_to_dict(fields) != local_field_state_to_dict(initial):
        _reject("frame-zero fields differ from project initialization")
    return fields


def _restore_materials(sim, payload, raw_ledger, index):
    if type(payload) is not dict or set(payload) != set(sim.materials):
        _reject("material IDs differ from project")
    result = {}
    for mid, initial in sim.materials.items():
        values = payload[mid]
        if type(values) is not dict or set(values) != set(_record(initial)):
            _reject("material record shape differs from project")
        for name in ('lower_um', 'upper_um'):
            _array(values[name], (3,), 'material ' + name)
        current = DegradableBox(**values)
        initial_config, actual = _record(initial), _record(current)
        if actual.pop('remaining_molecules') > initial_config.pop('remaining_molecules') or actual != initial_config:
            _reject("material configuration differs or inventory exceeds initial nutrient equivalent stock")
        if index == 0 and current != initial:
            _reject("frame-zero material inventory differs from project")
        result[mid] = current
    if type(raw_ledger) is not dict or set(raw_ledger) != (set(result) if index else set()):
        _reject("material ledger IDs disagree with checkpoint clock")
    ledgers = {}
    ledger_keys = {f.name for f in dataclass_fields(SettlementLedger)}
    for mid, values in raw_ledger.items():
        if type(values) is not dict or set(values) != ledger_keys:
            _reject("material ledger shape is invalid")
        for name in ('reservoir_ids', 'before', 'after', 'accepted_by_reservoir',
                     'conservation_residual', 'conservation_residual_exact', 'conservation_bound'):
            if type(values[name]) is not list or len(values[name]) != 1:
                _reject("material ledger vectors must be one-element lists")
        ledger = SettlementLedger(**{key: tuple(value) if isinstance(value, list) else value for key, value in values.items()})
        material = result[mid]
        if ledger.owner_id != mid or ledger.species != material.species or ledger.reservoir_ids != (mid,):
            _reject("material ledger owner/species differs from project")
        if ledger.after != (material.remaining_molecules,) or ledger.total_after != material.remaining_molecules:
            _reject("material ledger stock disagrees with material inventory")
        for name in ('before', 'after', 'accepted_by_reservoir', 'conservation_residual', 'conservation_bound'):
            vector = getattr(ledger, name)
            if len(vector) != 1:
                _reject("material ledger must have exactly one material reservoir")
            _number(vector[0], 'material ledger ' + name, nonnegative=name != 'conservation_residual')
        for name in ('total_before', 'total_after', 'total_accepted', 'total_conservation_residual', 'total_conservation_bound'):
            _number(getattr(ledger, name), 'material ledger ' + name, nonnegative=name != 'total_conservation_residual')
        if ledger.total_before > sim.materials[mid].remaining_molecules or ledger.total_after > ledger.total_before:
            _reject("material ledger stock increased")
        if abs(ledger.total_conservation_residual) > ledger.total_conservation_bound:
            _reject("material ledger conservation residual exceeds bound")
        for vector, total in (('before', 'total_before'), ('after', 'total_after')):
            if getattr(ledger, vector)[0] != getattr(ledger, total):
                _reject("material ledger totals disagree with reservoir amounts")
        exact = []
        for encoded, rounded, bound in (
            (ledger.conservation_residual_exact[0], ledger.conservation_residual[0], ledger.conservation_bound[0]),
            (ledger.total_conservation_residual_exact, ledger.total_conservation_residual, ledger.total_conservation_bound)):
            if type(encoded) is not str or len(encoded) > 2048:
                _reject("material exact residual must be a bounded rational string")
            value = Fraction(encoded)
            if str(value) != encoded or float(value) != rounded or abs(value) > Fraction(bound):
                _reject("material ledger exact residual disagrees with reported residual/bound")
            exact.append(value)
        debit = Fraction(ledger.before[0]) - Fraction(ledger.after[0]) - exact[0]
        if debit < 0 or float(debit) != ledger.accepted_by_reservoir[0]:
            _reject("material ledger debit disagrees with exact reservoir balance")
        accepted = Fraction(ledger.total_before) - Fraction(ledger.total_after) - exact[1]
        if accepted < 0 or float(accepted) != ledger.total_accepted:
            _reject("material ledger accepted amount disagrees with exact total balance")
        expected_bound = math.ulp(ledger.after[0]) / 2 if debit else 0.
        if ledger.conservation_bound[0] != expected_bound:
            _reject("material ledger reservoir roundoff bound is invalid")
        ledgers[mid] = ledger
    return MappingProxyType(result), MappingProxyType(ledgers)


def _restore_rng(sim, payload, walks, index):
    _keys(payload, ('version', 'numpy_version', 'run_seed', 'streams'), 'random_streams')
    if type(payload['streams']) is not list:
        _reject('random_streams.streams must be a list')
    for entry in payload['streams']:
        _keys(entry, ('key', 'state'), 'RNG stream')
        _keys(entry['state'], ('bit_generator', 'state', 'has_uint32', 'uinteger'), 'RNG state')
        _keys(entry['state']['state'], ('state', 'inc'), 'PCG64 state')
    streams = RandomStreams.from_dict(payload)
    if streams.run_seed != sim.seed:
        _reject("RNG seed differs from project seed")
    expected = set()
    for gid, node in sim._motion_nodes.items():
        active = node.parameters["tumble_rate_s"].value > 0 and index > 0
        for cid in sim.world.groups[gid].ids:
            wait = walks[cid].remaining_wait_s
            if active:
                if wait is None:
                    _reject("advanced random walk has no pending tumble clock")
                expected.update((node.id, gid, cid, purpose) for purpose in ("tumble_wait", "tumble_direction"))
            elif wait is not None:
                _reject("inactive or frame-zero walk has an unexpected tumble clock")
    entries = payload["streams"]
    if {tuple(e["key"]) for e in entries} != expected:
        _reject("RNG stream namespace differs from fixed population motion")
    initial_streams = RandomStreams(sim.seed)
    for entry in entries:
        key = tuple(entry["key"])
        generator = initial_streams.stream(*key).bit_generator
        expected_inc = format(generator.state["state"]["inc"], "032x")
        if entry["state"]["state"]["inc"] != expected_inc:
            _reject("RNG stream increment differs from its seeded namespace")
    return streams


def _restore_contacts(sim, payload, world, index):
    if type(payload) is not list or (index == 0 and payload):
        _reject('motion_contacts must be a list, empty at frame zero')
    ids = {cid for group in world.groups.values() for cid in group.ids}
    # Collision targets identify physical owners, independently of graph node
    # IDs. Include exhausted materials: the last interval still used their box.
    obstacles = {node.owner_id for node in sim._fixed_obstacle_nodes}
    obstacles.update(node.owner_id for node in sim._material_nodes.values())
    contacts = []
    for value in payload:
        _keys(value, ('cell_ids', 'kind', 'target_id', 'reason'), 'motion contact')
        cell_ids, kind, target = value['cell_ids'], value['kind'], value['target_id']
        if (type(cell_ids) is not list or not cell_ids or
                any(type(cid) is not str or cid not in ids for cid in cell_ids) or
                len(set(cell_ids)) != len(cell_ids)):
            _reject('motion contact has invalid cell IDs')
        if value['reason'] not in ('collision', 'numerically_uncertain', 'budget_exhausted'):
            _reject('motion contact has invalid reason')
        if not ((kind == 'wall' and len(cell_ids) == 1 and target == 'domain') or
                (kind == 'obstacle' and len(cell_ids) == 1 and type(target) is str and target in obstacles) or
                (kind == 'cell' and len(cell_ids) == 2 and target == cell_ids[1])):
            _reject('motion contact kind/target disagrees with fixed project entities')
        contacts.append(Contact(tuple(cell_ids), kind, target, value['reason']))
    return tuple(contacts)


def _validate_coherence(sim, fields, materials, material_ledger, world, walks, outputs, state):
    for node in sim.plan.nodes:
        out, memory = outputs[node.id], state[node.id]
        for port, value in out.items():
            if port not in ("heading", "gradient") and np.any(value < 0):
                _reject(f"{node.id}.{port} cannot be negative")
            if port in ("ei_fraction", "motor_bias", "blocked") and np.any(value > 1):
                _reject(f"{node.id}.{port} exceeds one")
        if node.module_id == "field.diffusive_local":
            expected = np.asarray(fields.concentrations_uM[node.parameters["species"].value]).reshape(fields.grid.shape)
            _same(out["concentration"], expected, "field output")
            _same(memory["concentration"], expected, "field memory")
        elif node.module_id == "source.finite_local":
            stock = next(source.remaining_molecules for source in fields.sources if source.id == node.id)
            _same(out["inventory"], stock, "source output")
            _same(memory["inventory"], stock, "source memory")
        elif node.module_id == "material.degradable_box":
            stock = materials[node.id].remaining_molecules
            _same(out['inventory'], stock, 'material output')
            _same(memory['inventory'], stock, 'material memory')
        elif sim._degradation_node is not None and node.id == sim._degradation_node.id:
            _same(out['released_amount'], math.fsum(value.total_accepted for value in material_ledger.values()), 'degradation release output')
        elif node.module_id == "motion.unbiased_run_tumble":
            group = world.groups[node.owner_id]
            headings = np.asarray([walks[cid].heading for cid in group.ids]).reshape(-1, 3)
            _same(out["position"], group.positions_um, "motion position")
            _same(out["heading"], headings, "motion heading")
            _same(memory["heading"], headings, "motion heading memory")
            _same(memory["remaining_wait"], [walks[cid].remaining_wait_s or 0. for cid in group.ids], "motion wait memory")
            if np.any(out["turns"] != np.floor(out["turns"])) or np.any((out["blocked"] != 0) & (out["blocked"] != 1)):
                _reject("motion turns/blocked values must be integral")
        elif node.module_id == "uptake.local_settlement":
            _same(memory["cumulative_uptake"], out["cumulative_uptake"], "uptake memory")
            if np.any(memory["cumulative_uptake"] < node.parameters["initial_molecules"].value):
                _reject("cumulative uptake is below project initialization")
        elif node.module_id == "signal.pts_accepted":
            for port in ("ei_fraction", "chey_p"):
                _same(memory[port], out[port], "signal memory")
        elif node.module_id in ("pts.capsule_area", "pts.capacity_rebuilt", "pts.capacity_simplified", "space.axis_aligned_obstacle", "surface.enzyme_activity"):
            for port in out:
                _same(out[port], sim.outputs[node.id][port], "static project output")


def restore_checkpoint(project, payload, registry=None):
    """Restore only after project, implementation, shapes and state agree."""
    from .spatial_runtime import SpatialSimulation
    try:
        if type(payload) is not dict or payload.get("version") != CHECKPOINT_VERSION or payload.get("execution_profile") != SPATIAL_PROFILE:
            _reject("unsupported spatial checkpoint version/profile")
        if set(payload) not in (_REQUIRED_KEYS, _REQUIRED_KEYS | _EXTENSION_KEYS):
            _reject("checkpoint has missing or unknown fields")
        unsigned = {key: value for key, value in payload.items() if key != "payload_sha256"}
        if payload.get("payload_sha256") != _hash(unsigned):
            _reject("checkpoint payload hash mismatch")
        if payload.get("project_sha256") != _hash(project):
            _reject("checkpoint project hash mismatch")
        # Execution may explicitly override the project's default seed. The
        # complete saved RNG must agree with that actual execution seed.
        seed = payload['seed']
        if type(seed) is not int or not 0 <= seed <= 9007199254740991:
            _reject('checkpoint execution seed must be a nonnegative safe integer')
        sim = simulation_from_project(project, registry, seed=seed)
        if not isinstance(sim, SpatialSimulation):
            _reject("project is not a spatial simulation")
        if payload["implementation_lock"] != _implementation_lock(sim.registry):
            _reject("checkpoint implementation lock mismatch (source/catalog/NumPy/Python/platform)")
        index, time = payload["frame_index"], payload["time_s"]
        if type(index) is not int or index < 0:
            _reject("frame_index must be a nonnegative integer")
        _number(time, "time_s")
        if (index == 0) != (time == 0):
            _reject("frame index and simulation clock disagree")
        materials, material_ledger = _restore_materials(sim, payload['materials'], payload['material_ledger'], index)
        fields = _restore_fields(sim, payload["local_fields"], index, materials)
        poses = payload["world"]
        if type(poses) is not dict or set(poses) != set(sim.world.groups):
            _reject("world group IDs differ from project")
        groups = {}
        for gid, group in sim.world.groups.items():
            pose = poses[gid]
            if type(pose) is not dict or set(pose) != {"ids", "positions_um", "orientation_xyzw"} or pose["ids"] != list(group.ids):
                _reject("world cell IDs/order or pose keys differ from project")
            positions = _array(pose["positions_um"], (len(group.ids), 3), "world positions")
            orientation = _array(pose["orientation_xyzw"], (len(group.ids), 4), "world orientation")
            groups[gid] = CellGroup(gid, group.ids, positions, orientation, group.geometry)
        world = World(sim.world.grid, groups, sim.world.species_initial_uM, sim.world.schedules)
        sim.obstacles, _ = sim._geometry_for(materials)
        capsules = sim._capsules(world)
        sim._guard(capsules, capsules)
        raw_walks = payload["walks"]
        ids = {cid for group in groups.values() for cid in group.ids}
        if type(raw_walks) is not dict or set(raw_walks) != ids:
            _reject("walk IDs differ from fixed project population")
        for value in raw_walks.values():
            _keys(value, ('version', 'heading', 'remaining_wait_s'), 'walk')
            _array(value['heading'], (3,), 'walk heading')
        walks = MappingProxyType({cid: RandomWalkState.from_dict(value) for cid, value in raw_walks.items()})
        for group in groups.values():
            actual = np.asarray([walks[cid].heading for cid in group.ids]).reshape(-1, 3)
            if not np.allclose(actual, heading_from_orientation(group.orientation_xyzw), rtol=0, atol=1e-10):
                _reject("walk heading disagrees with world orientation")
            if world.grid.geometry == "thin_layer" and np.any(actual[:, 2] != 0):
                _reject("thin-layer walks must remain planar")
        streams = _restore_rng(sim, payload["random_streams"], walks, index)
        outputs = _nested_from_dict(payload["outputs"], sim.outputs, "outputs")
        state = _nested_from_dict(payload["state"], sim.state, "state")
        _validate_coherence(sim, fields, materials, material_ledger, world, walks, outputs, state)
        current = sim._snapshot(world, fields, outputs, time, index, materials)
        if _hash(payload["current_frame"]) != _hash(current.cell_frame):
            _reject("current frame disagrees with restored clock/world/outputs/project geometry")
        if _hash(payload['object_states']) != _hash({mid: dict(value) for mid, value in current.object_states.items()}):
            _reject("visible object inventories disagree with material/source state")
        validator = FrameSequenceValidator(sim.run)
        initial_frame = deepcopy(current.cell_frame)
        initial_frame["frame_index"], initial_frame["time_s"] = 0, 0.
        validator.accept(initial_frame)  # Full schema and channels check.
        validator.next_index, validator.previous_time = index + 1, time
        if 'frame_validator' in payload:
            if _hash(payload['frame_validator']) != _hash(_validator_record(validator)):
                _reject('frame validator disagrees with clock/current frame/fixed population')
        contacts = _restore_contacts(sim, payload.get('motion_contacts', []), world, index)
        raw_ledger = payload["ledger"]
        if type(raw_ledger) is not dict or set(raw_ledger) != (set(fields.concentrations_uM) if index else set()):
            _reject("ledger species disagree with the frame index")
        ledger = {}
        names = {f.name for f in dataclass_fields(FieldLedger)}
        for species, values in raw_ledger.items():
            if type(values) is not dict or set(values) != names:
                _reject("invalid field ledger shape")
            for name, value in values.items():
                _number(value, f"ledger.{name}", nonnegative=name != "conservation_residual_molecules")
            entry = FieldLedger(**values)
            if abs(entry.conservation_residual_molecules) > entry.conservation_bound_molecules:
                _reject("ledger conservation residual exceeds reported bound")
            final_mass = math.fsum(float(v) * fields.grid.molecules_per_uM_voxel
                                   for v in fields.concentrations_uM[species])
            source_stock = math.fsum(s.remaining_molecules for s in fields.sources if s.species == species)
            residual = math.fsum((entry.field_before_molecules, entry.source_before_molecules,
                -entry.source_after_molecules, -entry.accepted_uptake_molecules, -entry.field_after_molecules))
            if (entry.field_after_molecules != final_mass or entry.source_after_molecules != source_stock or
                    entry.conservation_residual_molecules != residual):
                _reject('field ledger balance disagrees with field/source inventory')
            stages = (
                (entry.source_before_molecules, -entry.source_after_molecules, -entry.released_molecules),
                (entry.field_before_molecules, entry.released_molecules, -entry.field_after_release_molecules),
                (entry.field_after_release_molecules, -entry.field_after_diffusion_molecules),
                (entry.field_after_diffusion_molecules, -entry.accepted_uptake_molecules, -entry.field_after_molecules))
            if any(abs(math.fsum(stage)) > entry.conservation_bound_molecules for stage in stages):
                _reject('field ledger stage balance exceeds reported bound')
            ledger[species] = entry
        if index == 0:
            if poses != export_checkpoint(sim)["world"] or _nested_to_dict(outputs) != _nested_to_dict(sim.outputs) or _nested_to_dict(state) != _nested_to_dict(sim.state):
                _reject("frame-zero checkpoint differs from project initialization")
        sim.world, sim.fields, sim.walks, sim.streams = world, fields, walks, streams
        sim.materials, sim.material_ledger = materials, material_ledger
        sim.outputs, sim.state, sim.ledger = outputs, state, MappingProxyType(ledger)
        sim.time_s, sim.frame_index = time, index
        sim.current, sim.frame_validator = current, validator
        sim.motion_contacts = contacts
        return sim
    except SimulationError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError, ZeroDivisionError) as exc:
        raise SimulationError("spatial.checkpoint", f"Invalid checkpoint: {exc}") from exc
