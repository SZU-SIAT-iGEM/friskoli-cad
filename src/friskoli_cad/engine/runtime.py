"""Array-based execution for typed graph modules.

The runtime supports scalar fields, per-cell scalars, and per-cell vectors
in a thin layer or a full 3D grid. Unsupported port shapes fail explicitly.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Protocol

import numpy as np

from friskoli_cad.protocol import FrameSequenceValidator, validate_manifest, validate_run_metadata

from .compiler import CompiledNode, compile_graph
from .motion import orientation_after_heading


MOLECULES_PER_UM3_PER_UM = 6.02214076e23 * 1e-21


class SimulationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class GridDomain:
    geometry: Literal["thin_layer", "volume"]
    nx: int
    ny: int
    nz: int
    dx_um: float
    dy_um: float
    dz_um: float

    @classmethod
    def thin_layer(
        cls, nx: int, ny: int, dx_um: float, dy_um: float, thickness_um: float
    ) -> GridDomain:
        return cls("thin_layer", nx, ny, 1, dx_um, dy_um, thickness_um)

    @classmethod
    def volume(
        cls, nx: int, ny: int, nz: int, dx_um: float, dy_um: float, dz_um: float
    ) -> GridDomain:
        return cls("volume", nx, ny, nz, dx_um, dy_um, dz_um)

    def __post_init__(self) -> None:
        if self.geometry not in ("thin_layer", "volume"):
            raise SimulationError("domain.geometry", "unknown geometry mode")
        if any(type(n) is not int or n <= 0 for n in (self.nx, self.ny, self.nz)):
            raise SimulationError("domain.grid", "grid counts must be positive integers")
        if (self.geometry == "thin_layer" and self.nz != 1) or (
            self.geometry == "volume" and self.nz < 2
        ):
            raise SimulationError("domain.geometry", "geometry mode and z layer count differ")
        if not all(math.isfinite(step) for step in (self.dx_um, self.dy_um, self.dz_um)):
            raise SimulationError("domain.grid", "grid dimensions must be finite")
        if min(self.dx_um, self.dy_um, self.dz_um) <= 0:
            raise SimulationError("domain.grid", "grid dimensions must be positive")

    @property
    def shape(self) -> tuple[int, int, int]:
        return self.nz, self.ny, self.nx

    @property
    def voxel_count(self) -> int:
        return self.nx * self.ny * self.nz

    @property
    def extent_um(self) -> tuple[float, float, float]:
        return self.nx * self.dx_um, self.ny * self.dy_um, self.nz * self.dz_um

    @property
    def molecules_per_uM_voxel(self) -> float:
        return MOLECULES_PER_UM3_PER_UM * self.dx_um * self.dy_um * self.dz_um

    def flat_indices(self, positions_um: np.ndarray) -> np.ndarray:
        positions = np.asarray(positions_um, dtype=np.float64)
        if positions.ndim != 2 or positions.shape[1] != 3 or not np.isfinite(positions).all():
            raise SimulationError("cell.position", "cell positions must be finite XYZ rows")
        if np.any(positions < 0) or np.any(positions >= self.extent_um):
            raise SimulationError("cell.position", "cell lies outside the grid")
        ix = np.floor(positions[:, 0] / self.dx_um).astype(np.int64)
        iy = np.floor(positions[:, 1] / self.dy_um).astype(np.int64)
        iz = np.floor(positions[:, 2] / self.dz_um).astype(np.int64)
        return (iz * self.ny + iy) * self.nx + ix


@dataclass(frozen=True, slots=True)
class CellGroup:
    id: str
    ids: tuple[str, ...]
    positions_um: np.ndarray
    orientation_xyzw: np.ndarray

    def __post_init__(self) -> None:
        ids = tuple(self.ids)
        positions = np.array(self.positions_um, dtype=np.float64, copy=True)
        orientations = np.array(self.orientation_xyzw, dtype=np.float64, copy=True)
        count = len(ids)
        if (
            type(self.id) is not str or not self.id
            or any(type(cell_id) is not str or not cell_id for cell_id in ids)
            or len(set(ids)) != count
        ):
            raise SimulationError("cell.identity", "group and cell IDs must be nonempty and unique")
        if positions.shape != (count, 3) or not np.isfinite(positions).all():
            raise SimulationError("cell.position", "positions must have one finite XYZ row per cell")
        if orientations.shape != (count, 4) or not np.isfinite(orientations).all():
            raise SimulationError("cell.orientation", "orientations must have one finite XYZW row per cell")
        if not np.allclose(np.sum(orientations**2, axis=1), 1.0, rtol=0, atol=1e-3):
            raise SimulationError("cell.orientation", "orientation quaternions must have unit length")
        positions.setflags(write=False)
        orientations.setflags(write=False)
        object.__setattr__(self, "ids", ids)
        object.__setattr__(self, "positions_um", positions)
        object.__setattr__(self, "orientation_xyzw", orientations)


@dataclass(frozen=True, slots=True)
class World:
    grid: GridDomain
    groups: Mapping[str, CellGroup]

    def __post_init__(self) -> None:
        groups = dict(self.groups)
        seen = set()
        for group_id, group in groups.items():
            if group_id != group.id:
                raise SimulationError("cell.group", "group key differs from its ID")
            if seen.intersection(group.ids):
                raise SimulationError("cell.identity", "cell IDs must be unique across groups")
            seen.update(group.ids)
            self.grid.flat_indices(group.positions_um)
        object.__setattr__(self, "groups", MappingProxyType(groups))


@dataclass(frozen=True, slots=True)
class ModuleResult:
    outputs: Mapping[str, np.ndarray]
    state: Mapping[str, np.ndarray]


class RuntimeModule(Protocol):
    manifest: Mapping[str, object]

    def initialize(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]
    ) -> ModuleResult: ...

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult: ...


class ModuleRegistry:
    def __init__(self, modules: Iterable[RuntimeModule]):
        by_key = {}
        for module in modules:
            validate_manifest(module.manifest)
            key = module.manifest["id"], module.manifest["version"]
            if key in by_key:
                raise SimulationError("module.duplicate", f"module {key} is registered twice")
            by_key[key] = module
        self._modules = MappingProxyType(by_key)

    @property
    def manifests(self) -> tuple[Mapping[str, object], ...]:
        return tuple(module.manifest for module in self._modules.values())

    def get(self, module_id: str, version: str) -> RuntimeModule:
        try:
            return self._modules[(module_id, version)]
        except KeyError as error:
            raise SimulationError("module.missing", f"module {(module_id, version)} has no runtime") from error


@dataclass(frozen=True, slots=True)
class FieldOutput:
    values: np.ndarray
    quantity: str
    unit: str
    species: str | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    cell_frame: Mapping[str, object]
    concentration_fields: Mapping[str, np.ndarray]
    concentration_units: Mapping[str, str]
    environment_fields: Mapping[str, Mapping[str, FieldOutput]]
    domain: GridDomain


class Simulation:
    def __init__(
        self, world: World, graph: Mapping[str, object], run: Mapping[str, object],
        registry: ModuleRegistry,
    ) -> None:
        self.world = world
        self.registry = registry
        self.plan = compile_graph(graph, registry.manifests)
        validate_run_metadata(run, graph, registry.manifests)
        if set(world.groups) != set(run["groups"]):
            raise SimulationError("run.groups", "world groups differ from run metadata")
        self._pose_nodes = set()
        pose_groups = set()
        for node in self.plan.nodes:
            outputs = registry.get(node.module_id, node.module_version).manifest["outputs"]
            pose = {name: outputs.get(name) for name in ("position", "heading")}
            if pose["position"] is not None:
                if (
                    node.owner_kind != "population"
                    or pose["position"] is None or pose["heading"] is None
                    or (pose["position"]["shape"], pose["position"]["quantity"], pose["position"]["unit"])
                    != ("cell.vector", "position", "um")
                    or (pose["heading"]["shape"], pose["heading"]["quantity"], pose["heading"]["unit"])
                    != ("cell.vector", "heading", "1")
                ):
                    raise SimulationError("motion.pose", f"{node.id} has an invalid pose output pair")
                if node.owner_id in pose_groups:
                    raise SimulationError("motion.pose", f"group {node.owner_id} has two pose providers")
                pose_groups.add(node.owner_id)
                self._pose_nodes.add(node.id)
        self.run = deepcopy(run)
        self.time_s = 0.0
        self.frame_index = 0
        self.outputs, self.state, _ = self._execute(initial=True, dt_s=0.0)
        self.frame_validator = FrameSequenceValidator(run)
        initial = self._snapshot(self.outputs, self.world, 0.0, 0)
        self.frame_validator.accept(initial.cell_frame, validate_schema=False)
        self.current = initial

    def _shape(self, node: CompiledNode, shape: str) -> tuple[int, ...]:
        if shape == "field.scalar":
            return self.world.grid.shape
        if shape == "cell.scalar":
            return (len(self.world.groups[node.owner_id].ids),)
        if shape == "cell.vector":
            return (len(self.world.groups[node.owner_id].ids), 3)
        raise SimulationError("runtime.shape", f"port shape {shape} has no executor yet")

    def _apply_pose(
        self, world: World, node: CompiledNode, outputs: Mapping[str, np.ndarray]
    ) -> World:
        positions, heading = outputs["position"], outputs["heading"]
        norms = np.linalg.norm(heading, axis=1)
        if not np.allclose(norms, 1, rtol=0, atol=1e-9):
            raise SimulationError("motion.heading", "headings must have unit length")
        if world.grid.geometry == "thin_layer" and np.any(np.abs(heading[:, 2]) > 1e-9):
            raise SimulationError("motion.plane", "thin-layer headings must lie in XY")
        group = world.groups[node.owner_id]
        updated = CellGroup(
            group.id, group.ids, positions,
            orientation_after_heading(group.orientation_xyzw, heading),
        )
        return World(world.grid, {**world.groups, node.owner_id: updated})

    def _freeze_result(
        self, node: CompiledNode, result: ModuleResult
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        manifest = self.registry.get(node.module_id, node.module_version).manifest
        frozen = []
        for returned, declared, section in (
            (result.outputs, manifest["outputs"], "outputs"),
            (result.state, manifest["state"], "state"),
        ):
            if set(returned) != set(declared):
                raise SimulationError("module.result", f"{node.id} returned the wrong {section}")
            values = {}
            for name, value in returned.items():
                try:
                    array = np.array(value, dtype=np.float64, copy=True)
                except (TypeError, ValueError) as error:
                    raise SimulationError("module.result", f"{node.id}.{name} is not numeric") from error
                expected = self._shape(node, declared[name]["shape"])
                if array.shape != expected or not np.isfinite(array).all():
                    raise SimulationError("module.result", f"{node.id}.{name} has invalid shape or values")
                if (
                    section == "outputs"
                    and declared[name].get("quantity") == "concentration"
                    and np.any(array < 0)
                ):
                    raise SimulationError("module.concentration", f"{node.id}.{name} is negative")
                array.setflags(write=False)
                values[name] = array
            frozen.append(values)
        return frozen[0], frozen[1]

    def _execute(
        self, initial: bool, dt_s: float
    ) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, dict[str, np.ndarray]], World]:
        outputs: dict[str, dict[str, np.ndarray]] = {}
        state: dict[str, dict[str, np.ndarray]] = {}
        working_world = self.world
        for node in self.plan.nodes:
            inputs = {}
            for name, binding in node.inputs.items():
                if initial and binding.timing == "previous_step":
                    continue
                source_outputs = outputs if binding.timing == "same_step" else self.outputs
                inputs[name] = source_outputs[binding.source_node][binding.source_port]
            module = self.registry.get(node.module_id, node.module_version)
            if initial:
                result = module.initialize(working_world, node, MappingProxyType(inputs))
            else:
                result = module.advance(
                    working_world, node, MappingProxyType(inputs),
                    MappingProxyType(self.state[node.id]), MappingProxyType(self.outputs[node.id]), dt_s,
                )
            outputs[node.id], state[node.id] = self._freeze_result(node, result)
            if not initial and node.id in self._pose_nodes:
                working_world = self._apply_pose(working_world, node, outputs[node.id])
        return outputs, state, working_world

    def _snapshot(
        self, outputs: Mapping[str, Mapping[str, np.ndarray]], world: World,
        time_s: float, index: int,
    ) -> Snapshot:
        channels_by_group: dict[str, list[tuple[str, np.ndarray]]] = {key: [] for key in world.groups}
        for channel_id, channel in self.run["channels"].items():
            values = outputs[channel["node"]][channel["port"]]
            channels_by_group[channel["group_id"]].append((channel_id, values))
        cells = []
        for group_id in sorted(world.groups):
            group = world.groups[group_id]
            for index_in_group, cell_id in enumerate(group.ids):
                cells.append({
                    "id": cell_id,
                    "group_id": group_id,
                    "position_um": group.positions_um[index_in_group].tolist(),
                    "orientation_xyzw": group.orientation_xyzw[index_in_group].tolist(),
                    "channels": {
                        channel_id: float(values[index_in_group])
                        for channel_id, values in channels_by_group[group_id]
                    },
                })
        frame = {
            "protocol_version": "0.1.0", "run_id": self.run["run_id"],
            "frame_index": index, "time_s": time_s, "cells": cells, "events": [],
        }
        fields = {}
        field_units = {}
        environment_fields = {}
        for node in self.plan.nodes:
            if node.owner_kind != "environment":
                continue
            manifest = self.registry.get(node.module_id, node.module_version).manifest
            node_fields = {}
            for name, port in manifest["outputs"].items():
                if port["shape"] != "field.scalar":
                    continue
                binding = port.get("species_parameter")
                species = node.parameters[binding].value if binding else None
                node_fields[name] = FieldOutput(
                    outputs[node.id][name], port["quantity"], port["unit"], species
                )
                if port["quantity"] == "concentration":
                    species = node.parameters[binding].value if binding else name
                    if species in fields:
                        raise SimulationError("field.duplicate", f"two concentration fields for {species}")
                    fields[species] = outputs[node.id][name]
                    field_units[species] = port["unit"]
            environment_fields[node.id] = MappingProxyType(node_fields)
        return Snapshot(
            frame, MappingProxyType(fields), MappingProxyType(field_units),
            MappingProxyType(environment_fields), world.grid,
        )

    def step(self, dt_s: float) -> Snapshot:
        if not math.isfinite(dt_s) or dt_s <= 0:
            raise SimulationError("time.step", "dt_s must be positive and finite")
        outputs, state, next_world = self._execute(initial=False, dt_s=dt_s)
        next_time = self.time_s + dt_s
        if not math.isfinite(next_time):
            raise SimulationError("time.overflow", "simulation time must remain finite")
        snapshot = self._snapshot(outputs, next_world, next_time, self.frame_index + 1)
        self.frame_validator.accept(snapshot.cell_frame, validate_schema=False)
        self.outputs, self.state = outputs, state
        self.world = next_world
        self.time_s = next_time
        self.frame_index += 1
        self.current = snapshot
        return snapshot
