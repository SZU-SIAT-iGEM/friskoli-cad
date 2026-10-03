"""Versioned, read-only inputs and checked proposals for registered mechanisms.

This is an execution contract for explicitly trusted Python modules, not a
security sandbox. Only the owning simulation may apply returned effects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from types import MappingProxyType
import math

import numpy as np

from friskoli_cad.protocol import ProtocolError

API_VERSION = "0.1.0"
STAGES = ("prepare", "field", "physiology", "lifecycle", "observation")
BACKENDS = ("numpy-cpu", "numpy-cupy-cuda")


def freeze(value):
    """Detach mutable values; share arrays only when already immutable."""
    if isinstance(value, np.ndarray):
        result = value.copy() if value.flags.writeable else value.view()
        result.flags.writeable = False
        return result
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(child) for key, child in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(freeze(child) for child in value)
    if isinstance(value, (str, int, float, bool, type(None), np.generic)):
        return value
    raise ProtocolError("module.value", "/", f"Unsupported context value {type(value).__name__}")


def thaw(value):
    """Copy a context/proposal into serializable mutable metadata or arrays."""
    if isinstance(value, np.ndarray):
        return value.copy()
    if isinstance(value, Mapping):
        return {key: thaw(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(child) for child in value]
    return value.item() if isinstance(value, np.generic) else value


def check_finite(value, path="/"):
    if isinstance(value, np.ndarray):
        if value.dtype.kind not in "biuf" or not np.isfinite(value).all():
            raise ProtocolError("module.non_finite", path, "Only finite numeric arrays are accepted")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            check_finite(child, path.rstrip("/") + "/" + str(key))
    elif isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            check_finite(child, path.rstrip("/") + f"/{index}")
    elif isinstance(value, (float, np.floating)) and not math.isfinite(value):
        raise ProtocolError("module.non_finite", path, "Values must be finite")


@dataclass(frozen=True, slots=True)
class StepContext:
    time_s: float
    dt_s: float
    step_index: int
    owner_id: str
    entity_ids: tuple[str, ...]
    world: Mapping = field(default_factory=dict)
    inputs: Mapping = field(default_factory=dict)
    parameters: Mapping = field(default_factory=dict)
    state: Mapping = field(default_factory=dict)
    rng: object = None
    backend: str = "numpy-cpu"
    node_id: str = ""
    entity_sets: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if (not math.isfinite(self.time_s) or not math.isfinite(self.dt_s)
                or self.time_s < 0 or self.dt_s < 0 or type(self.step_index) is not int or self.step_index < 0):
            raise ProtocolError("module.time", "/context", "Finite nonnegative committed time is required")
        if self.backend not in BACKENDS:
            raise ProtocolError("module.backend", "/context/backend", "Unknown backend")
        ids = tuple(self.entity_ids)
        if len(ids) != len(set(ids)):
            raise ProtocolError("module.entity_ids", "/context/entity_ids", "Entity IDs must be unique")
        object.__setattr__(self, "entity_ids", ids)
        for name in ("world", "inputs", "parameters", "state", "entity_sets"):
            object.__setattr__(self, name, freeze(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class Effect:
    kind: str
    target: str
    value: object

    def __post_init__(self):
        check_finite(self.value, "/effects")
        object.__setattr__(self, "value", freeze(self.value))


@dataclass(frozen=True, slots=True)
class ModuleProposal:
    outputs: Mapping
    state: Mapping
    effects: tuple[Effect, ...] = ()

    def __post_init__(self):
        for name in ("outputs", "state"):
            check_finite(getattr(self, name), "/" + name)
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, "effects", tuple(self.effects))
        if any(not isinstance(item, Effect) for item in self.effects):
            raise ProtocolError("module.effect", "/effects", "Effects require typed proposals")


def execution_contract(module):
    contract = getattr(module, "execution_contract", None)
    if not isinstance(contract, Mapping):
        raise ProtocolError("module.execution", "/execution", "A registered execution contract is required")
    if contract.get("api_version") != API_VERSION or contract.get("stage") not in STAGES:
        raise ProtocolError("module.execution", "/execution", "Unsupported API version or stage")
    for name in ("reads", "writes", "effects"):
        values = contract.get(name, ())
        if not isinstance(values, (tuple, list)) or any(not isinstance(v, str) or not v for v in values):
            raise ProtocolError("module.execution", f"/execution/{name}", "Expected explicit resource names")
    devices = tuple(contract.get("backends", ("numpy-cpu",)))
    if not devices or any(value not in BACKENDS for value in devices):
        raise ProtocolError("module.backend", "/execution/backends", "Unsupported backend declaration")
    settled = contract.get("settled_outputs", ())
    if (not isinstance(settled, (tuple, list))
            or any(not isinstance(name, str) or name not in module.manifest['outputs'] for name in settled)
            or len(set(settled)) != len(settled)):
        raise ProtocolError("module.settled_outputs", "/execution/settled_outputs",
                            "Settled outputs must name distinct declared output ports")
    workspace = contract.get('workspace_bytes', {})
    if (not isinstance(workspace, Mapping) or set(workspace) - {'fixed', 'per_voxel', 'per_cell'}
            or any(type(value) is not int or value < 0 for value in workspace.values())):
        raise ProtocolError('module.workspace', '/execution/workspace_bytes',
                            'Workspace estimates require nonnegative integer byte counts')
    for name, resources in (('effect_targets', contract.get('effects', ())), ('write_targets', contract.get('writes', ()))):
        targets = contract.get(name, {})
        if not isinstance(targets, Mapping) or set(targets) - set(resources):
            raise ProtocolError('module.target_contract', '/execution/' + name, 'Targets must refer to declared resources')
        for resource, grants in targets.items():
            if not isinstance(grants, (list, tuple)) or not grants or any(not isinstance(v, str) or not v for v in grants):
                raise ProtocolError('module.target_contract', '/execution/' + name, 'Resource grants must be a nonempty list of target names')
            for target in grants:
                if target.startswith('$') and target != '$owner':
                    parameter = module.manifest['parameters'].get(target[1:])
                    if parameter is None or parameter['type'] != 'string':
                        raise ProtocolError('module.target_contract', '/execution/' + name, 'Target bindings require a string parameter')
    for method in ("initialize", "propose"):
        if not callable(getattr(module, method, None)):
            raise ProtocolError("module.implementation", "/execution", f"Missing {method}(context)")
    return contract


def execute_module(module, context: StepContext, *, initialize=False):
    """Evaluate and validate a proposal. No shared world state is committed here."""
    contract = execution_contract(module)
    if context.backend not in contract.get("backends", ("numpy-cpu",)):
        raise ProtocolError("module.backend", "/execution", "Requested backend has no registered implementation")
    allowed_reads = set(contract.get("reads", ()))
    unexpected = set(context.world) - allowed_reads
    missing = allowed_reads - set(context.world)
    if unexpected or missing:
        raise ProtocolError("module.read_access", "/context/world",
                            f"Undeclared resources: {sorted(unexpected)}; missing resources: {sorted(missing)}")
    from .port_semantics import validate_port_value
    manifest = module.manifest
    unknown_inputs = set(context.inputs) - set(manifest['inputs'])
    required = {name for name, port in manifest['inputs'].items() if not port.get('optional')}
    if unknown_inputs or (not initialize and not required <= set(context.inputs)):
        raise ProtocolError('module.inputs', '/inputs', 'Input set differs from the registered contract')
    for name, value in context.inputs.items():
        validate_port_value(value, manifest['inputs'][name], context, f'/inputs/{name}')
    proposal = (module.initialize if initialize else module.propose)(context)
    if not isinstance(proposal, ModuleProposal):
        raise ProtocolError("module.proposal", "/", "Module must return ModuleProposal")
    expected = set(manifest["initial_outputs"] if initialize else manifest["outputs"])
    if not expected <= set(proposal.outputs) or set(proposal.outputs) - set(manifest["outputs"]):
        raise ProtocolError("module.outputs", "/outputs", "Output set differs from the manifest")
    if set(proposal.state) != set(manifest["state"]):
        raise ProtocolError("module.state", "/state", "State set differs from the manifest")
    for effect in proposal.effects:
        if effect.kind not in contract.get("effects", ()) or not effect.target:
            raise ProtocolError("module.effect_access", "/effects", "Effect was not granted by the execution contract")
        grants = contract.get("effect_targets", {}).get(effect.kind)
        if grants is not None:
            resolved = [context.owner_id if value == "$owner" else
                        context.parameters.get(value[1:]) if value.startswith("$") else value for value in grants]
            if effect.target not in resolved:
                raise ProtocolError("module.effect_target", "/effects", "Effect targets an undeclared owner")
    for name, value in proposal.outputs.items():
        validate_port_value(value, manifest["outputs"][name], context, f"/outputs/{name}")
    for name, value in proposal.state.items():
        validate_port_value(value, manifest["state"][name], context, f"/state/{name}")
    return proposal


def validate_state_owners(plan, registry):
    """Exclusive writes are determined at compile time, before any calculation."""
    owners = {}
    for node in plan.nodes:
        module = registry.get(node.module_id, node.module_version)
        if not hasattr(module, "execution_contract"):
            continue
        contract = execution_contract(module)
        for resource in contract.get("writes", ()):
            targets = contract.get('write_targets', {}).get(resource,
                contract.get('effect_targets', {}).get(resource, ['$owner']))
            for target in targets:
                if target == '$owner':
                    owner = node.owner_id
                elif target.startswith('$'):
                    parameter = node.parameters.get(target[1:])
                    if parameter is None or not isinstance(parameter.value, str):
                        raise ProtocolError('module.owner_binding', '/graph', 'Write target must bind an explicit owner or string parameter')
                    owner = parameter.value
                else:
                    owner = target
                key = (owner, resource)
                if key in owners:
                    raise ProtocolError("module.owner_conflict", "/graph", f"{node.id} and {owners[key]} write {resource}")
                owners[key] = node.id
    return owners
