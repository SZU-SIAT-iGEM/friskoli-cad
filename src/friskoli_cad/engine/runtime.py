"""Array-based execution for typed graph modules.

The runtime supports scalar fields, per-cell scalars, and per-cell vectors
in a thin layer or a full 3D grid. Unsupported port shapes fail explicitly.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Literal, Mapping, Protocol

import numpy as np

from friskoli_cad.protocol import FrameSequenceValidator, validate_manifest, validate_run_metadata
from friskoli_cad.registry import build_catalog

from .compiler import CompiledNode, compile_graph
from .motion import heading_from_orientation, orientation_after_heading


MOLECULES_PER_UM3_PER_UM = 6.02214076e23 * 1e-21


class SimulationError(ValueError):
    def __init__(self, code: str, message: str, path: str = "/"):
        self.code = code
        self.path = path
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
class CapsuleGeometry:
    """Pole-to-pole length and transverse diameter of an axisymmetric capsule."""

    length_um: float
    diameter_um: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.length_um) or not math.isfinite(self.diameter_um)
            or self.diameter_um <= 0 or self.length_um < self.diameter_um
        ):
            raise SimulationError("cell.geometry", "capsule needs length >= diameter > 0")


@dataclass(frozen=True, slots=True)
class CellGroup:
    id: str
    ids: tuple[str, ...]
    positions_um: np.ndarray
    orientation_xyzw: np.ndarray
    geometry: tuple[CapsuleGeometry | None, ...] | None = None
    has_known_geometry: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ids = tuple(self.ids)
        positions = np.array(self.positions_um, dtype=np.float64, copy=True)
        orientations = np.array(self.orientation_xyzw, dtype=np.float64, copy=True)
        count = len(ids)
        geometry = (None,) * count if self.geometry is None else tuple(self.geometry)
        has_known_geometry = False if self.geometry is None else any(entry is not None for entry in geometry)
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
        if len(geometry) != count or any(
            entry is not None and not isinstance(entry, CapsuleGeometry) for entry in geometry
        ):
            raise SimulationError("cell.geometry", "geometry needs one capsule or unknown entry per cell")
        positions.setflags(write=False)
        orientations.setflags(write=False)
        object.__setattr__(self, "ids", ids)
        object.__setattr__(self, "positions_um", positions)
        object.__setattr__(self, "orientation_xyzw", orientations)
        object.__setattr__(self, "geometry", geometry)
        object.__setattr__(self, "has_known_geometry", has_known_geometry)


@dataclass(frozen=True, slots=True)
class World:
    grid: GridDomain
    groups: Mapping[str, CellGroup]
    species_initial_uM: Mapping[str, float] = field(default_factory=dict)
    schedules: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        groups = dict(self.groups)
        seen = set()
        for group_id, group in groups.items():
            if group_id != group.id:
                raise SimulationError("cell.group", "group key differs from its ID")
            if seen.intersection(group.ids):
                raise SimulationError("cell.identity", "cell IDs must be unique across groups")
            seen.update(group.ids)
            try:
                self.grid.flat_indices(group.positions_um)
            except SimulationError as error:
                outside = np.any((group.positions_um < 0) | (group.positions_um >= self.grid.extent_um), axis=1)
                index = int(np.flatnonzero(outside)[0])
                pointer = group_id.replace("~", "~0").replace("/", "~1")
                raise SimulationError(error.code, f"group {group_id}, cell {group.ids[index]} lies outside the grid",
                                      f"/groups/{pointer}/positions_um/{index}") from error
            if self.grid.geometry == "thin_layer" and group.has_known_geometry:
                pointer = group_id.replace("~", "~0").replace("/", "~1")
                axial_z = heading_from_orientation(group.orientation_xyzw)[:, 2]
                for index, capsule in enumerate(group.geometry):
                    if capsule is None:
                        continue
                    half_height = 0.5 * (
                        capsule.diameter_um
                        + (capsule.length_um - capsule.diameter_um) * abs(axial_z[index])
                    )
                    center_z = group.positions_um[index, 2]
                    if center_z - half_height <= 0 or center_z + half_height >= self.grid.dz_um:
                        raise SimulationError(
                            "cell.geometry", f"group {group_id}, capsule {group.ids[index]} does not fit inside the thin layer",
                            f"/groups/{pointer}/initial_geometry/{index}"
                        )
        species = dict(self.species_initial_uM)
        if any(
            type(name) is not str or not name
            or type(level) not in (int, float) or not math.isfinite(level) or level < 0
            for name, level in species.items()
        ):
            raise SimulationError("project.species", "species initial concentrations must be nonnegative")
        object.__setattr__(self, "groups", MappingProxyType(groups))
        object.__setattr__(self, "species_initial_uM", MappingProxyType(species))
        object.__setattr__(self, "schedules", MappingProxyType(dict(self.schedules)))


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


class DivisionRefreshModule(RuntimeModule, Protocol):
    """Additional output refresh required only in a graph with division."""

    def refresh(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
    ) -> ModuleResult: ...


class ModuleRegistry:
    def __init__(self, modules: Iterable[RuntimeModule], execution_semantics="legacy-explicit-v1"):
        by_key = {}
        for module in modules:
            validate_manifest(module.manifest)
            key = module.manifest["id"], module.manifest["version"]
            if key in by_key:
                raise SimulationError("module.duplicate", f"module {key} is registered twice")
            by_key[key] = module
        self._modules = MappingProxyType(by_key)
        self._catalog = build_catalog(by_key.values(), execution_semantics)

    @property
    def catalog(self) -> dict:
        return deepcopy(self._catalog)

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
    object_states: Mapping[str, Mapping[str, object]] = field(default_factory=dict)


class Simulation:
    def __init__(
        self, world: World, graph: Mapping[str, object], run: Mapping[str, object],
        registry: ModuleRegistry, frame_version: str | None = None,
    ) -> None:
        self.world = world
        self.registry = registry
        self.plan = compile_graph(graph, registry.manifests)
        validate_run_metadata(run, graph, registry.manifests)
        if set(world.groups) != set(run["groups"]):
            raise SimulationError("run.groups", "world groups differ from run metadata")
        self._pose_nodes = set()
        self._geometry_nodes = set()
        self._division_nodes = set()
        pose_groups = set()
        geometry_groups = set()
        division_groups = set()
        for node in self.plan.nodes:
            # Explicit readers may expose position/geometry without becoming state writers.
            # Legacy modules retain their original inferred writer semantics.
            if getattr(registry.get(node.module_id, node.module_version), "world_access", "legacy_inferred") == "read_only":
                continue
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
            geometry_ports = {name: outputs.get(name) for name in ("length", "diameter")}
            if any(port is not None for port in geometry_ports.values()):
                if (
                    node.owner_kind != "population"
                    or geometry_ports["length"] is None or geometry_ports["diameter"] is None
                    or (
                        geometry_ports["length"]["shape"], geometry_ports["length"]["quantity"],
                        geometry_ports["length"]["unit"],
                    ) != ("cell.scalar", "capsule_length", "um")
                    or (
                        geometry_ports["diameter"]["shape"], geometry_ports["diameter"]["quantity"],
                        geometry_ports["diameter"]["unit"],
                    ) != ("cell.scalar", "capsule_diameter", "um")
                ):
                    raise SimulationError("growth.geometry", f"{node.id} has an invalid geometry output pair")
                if node.owner_id in geometry_groups:
                    raise SimulationError("growth.geometry", f"group {node.owner_id} has two geometry providers")
                geometry_groups.add(node.owner_id)
                self._geometry_nodes.add(node.id)
            division_port = outputs.get("divide")
            if division_port is not None:
                if (
                    node.owner_kind != "population"
                    or (division_port["shape"], division_port["quantity"], division_port["unit"])
                    != ("cell.scalar", "division_trigger", "1")
                ):
                    raise SimulationError("division.provider", f"{node.id} has an invalid division output")
                if node.owner_id in division_groups:
                    raise SimulationError("division.provider", f"group {node.owner_id} has two division providers")
                division_groups.add(node.owner_id)
                self._division_nodes.add(node.id)
        if self._division_nodes:
            for node in self.plan.nodes:
                if not callable(getattr(registry.get(node.module_id, node.module_version), "refresh", None)):
                    raise SimulationError("division.refresh", f"module {node.id} cannot refresh after division")
        has_known_geometry = any(group.has_known_geometry for group in world.groups.values())
        self.frame_version = frame_version if frame_version is not None else (
            "0.2.0" if has_known_geometry or self._geometry_nodes or self._division_nodes else "0.1.0"
        )
        if self.frame_version not in ("0.1.0", "0.2.0"):
            raise SimulationError("frame.version", "unknown frame version")
        if (has_known_geometry or self._geometry_nodes or self._division_nodes) and self.frame_version != "0.2.0":
            raise SimulationError("frame.geometry", "known or changing geometry requires frame 0.2.0")
        self.run = deepcopy(run)
        self.time_s = 0.0
        self.frame_index = 0
        self._next_cell_serial = 1
        self.outputs, self.state, _ = self._execute(initial=True, dt_s=0.0)
        self.frame_validator = FrameSequenceValidator(run)
        initial = self._snapshot(self.outputs, self.world, 0.0, 0)
        self.frame_validator.accept(initial.cell_frame, validate_schema=False)
        self.current = initial

    def _shape(self, node: CompiledNode, shape: str, world: World) -> tuple[int, ...]:
        if shape == "field.scalar":
            return world.grid.shape
        if shape == "cell.scalar":
            return (len(world.groups[node.owner_id].ids),)
        if shape == "cell.vector":
            return (len(world.groups[node.owner_id].ids), 3)
        if shape == "global.scalar":
            return ()
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
            group.geometry if group.has_known_geometry else None,
        )
        return World(
            world.grid, {**world.groups, node.owner_id: updated},
            world.species_initial_uM, world.schedules,
        )

    def _apply_geometry(
        self, world: World, node: CompiledNode, outputs: Mapping[str, np.ndarray]
    ) -> World:
        group = world.groups[node.owner_id]
        geometry = tuple(
            CapsuleGeometry(float(length), float(diameter))
            for length, diameter in zip(outputs["length"], outputs["diameter"])
        )
        updated = CellGroup(
            group.id, group.ids, group.positions_um, group.orientation_xyzw, geometry,
        )
        return World(
            world.grid, {**world.groups, node.owner_id: updated},
            world.species_initial_uM, world.schedules,
        )

    def _freeze_result(
        self, node: CompiledNode, result: ModuleResult, world: World,
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
                expected = self._shape(node, declared[name]["shape"], world)
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
            outputs[node.id], state[node.id] = self._freeze_result(node, result, working_world)
            if not initial and node.id in self._pose_nodes:
                working_world = self._apply_pose(working_world, node, outputs[node.id])
            if not initial and node.id in self._geometry_nodes:
                working_world = self._apply_geometry(working_world, node, outputs[node.id])
        return outputs, state, working_world

    def _divide(
        self, world: World, outputs: dict[str, dict[str, np.ndarray]],
        state: dict[str, dict[str, np.ndarray]], time_s: float,
    ) -> tuple[World, dict[str, dict[str, np.ndarray]], dict[str, dict[str, np.ndarray]], list[dict], int]:
        requests = {}
        for node in self.plan.nodes:
            if node.id not in self._division_nodes:
                continue
            values = outputs[node.id]["divide"]
            if not np.all((values == 0) | (values == 1)):
                raise SimulationError("division.trigger", f"{node.id} must return zero or one per cell")
            if np.any(values == 1):
                requests[node.owner_id] = values == 1
        if not requests:
            return world, outputs, state, [], self._next_cell_serial

        groups = dict(world.groups)
        sources: dict[str, np.ndarray] = {}
        divided: dict[str, list[tuple[int, int]]] = {}
        events: list[dict] = []
        serial = self._next_cell_serial
        used_ids = set(self.frame_validator.seen)
        for group_id in sorted(requests):
            group = world.groups[group_id]
            ids = list(group.ids)
            positions = group.positions_um.tolist()
            orientations = group.orientation_xyzw.tolist()
            geometry = list(group.geometry)
            source = list(range(len(ids)))
            pairs = []
            headings = heading_from_orientation(group.orientation_xyzw)
            for parent_index in np.flatnonzero(requests[group_id]):
                capsule = geometry[parent_index]
                if capsule is None:
                    raise SimulationError("division.geometry", "a dividing cell needs known capsule geometry")
                daughter_length = (capsule.length_um + capsule.diameter_um / 3) / 2
                if daughter_length < capsule.diameter_um:
                    raise SimulationError("division.geometry", "equal-volume daughters would be shorter than their diameter")
                daughter = CapsuleGeometry(daughter_length, capsule.diameter_um)
                center = group.positions_um[parent_index]
                offset = headings[parent_index] * (daughter_length / 2)
                positions[parent_index] = (center - offset).tolist()
                positions.append((center + offset).tolist())
                orientations.append(group.orientation_xyzw[parent_index].tolist())
                geometry[parent_index] = daughter
                geometry.append(daughter)
                while True:
                    child_id = f"{ids[parent_index]}~{serial}"
                    serial += 1
                    if child_id not in used_ids:
                        break
                used_ids.add(child_id)
                ids.append(child_id)
                child_index = len(ids) - 1
                source.append(int(parent_index))
                pairs.append((int(parent_index), child_index))
                events.append({
                    "type": "division", "time_s": time_s,
                    "parent_id": group.ids[parent_index], "child_id": child_id,
                })
            groups[group_id] = CellGroup(
                group_id, tuple(ids), np.asarray(positions), np.asarray(orientations), tuple(geometry),
            )
            sources[group_id] = np.asarray(source, dtype=np.int64)
            divided[group_id] = pairs
        divided_world = World(world.grid, groups, world.species_initial_uM, world.schedules)

        inherited_state: dict[str, dict[str, np.ndarray]] = {}
        boundary_outputs: dict[str, dict[str, np.ndarray]] = {}
        for node in self.plan.nodes:
            manifest = self.registry.get(node.module_id, node.module_version).manifest
            source = sources.get(node.owner_id) if node.owner_kind == "population" else None
            pairs = divided.get(node.owner_id, [])
            node_state = {}
            for name, values in state[node.id].items():
                definition = manifest["state"][name]
                if source is None or not definition["shape"].startswith("cell."):
                    node_state[name] = values
                    continue
                inherited = values[source].copy()
                for parent_index, child_index in pairs:
                    rule = definition["on_division"]
                    if rule == "split":
                        inherited[parent_index] = values[parent_index] / 2
                        inherited[child_index] = values[parent_index] / 2
                    elif rule == "reset":
                        inherited[parent_index] = 0
                        inherited[child_index] = 0
                    elif rule != "copy":
                        raise SimulationError("division.state", f"unsupported rule {rule} for {node.id}.{name}")
                node_state[name] = inherited
            if node.id in self._pose_nodes and source is not None:
                daughter_group = divided_world.groups[node.owner_id]
                node_state["position"] = daughter_group.positions_um
                node_state["heading"] = heading_from_orientation(daughter_group.orientation_xyzw)
            inherited_state[node.id] = node_state

            node_outputs = {}
            for name, values in outputs[node.id].items():
                definition = manifest["outputs"][name]
                if source is None or not definition["shape"].startswith("cell."):
                    node_outputs[name] = values
                elif name in node_state and node_state[name].shape == values[source].shape:
                    node_outputs[name] = node_state[name]
                else:
                    node_outputs[name] = values[source].copy()
            boundary_outputs[node.id] = node_outputs

        refreshed_outputs: dict[str, dict[str, np.ndarray]] = {}
        refreshed_state: dict[str, dict[str, np.ndarray]] = {}
        for node in self.plan.nodes:
            inputs = {}
            for name, binding in node.inputs.items():
                source_outputs = refreshed_outputs if binding.timing == "same_step" else boundary_outputs
                inputs[name] = source_outputs[binding.source_node][binding.source_port]
            module = self.registry.get(node.module_id, node.module_version)
            result = module.refresh(
                divided_world, node, MappingProxyType(inputs),
                MappingProxyType(inherited_state[node.id]), MappingProxyType(boundary_outputs[node.id]),
            )
            node_outputs, node_state = self._freeze_result(node, result, divided_world)
            if any(not np.array_equal(node_state[name], value) for name, value in inherited_state[node.id].items()):
                raise SimulationError("division.refresh", f"{node.id} changed state while refreshing outputs")
            refreshed_outputs[node.id] = node_outputs
            refreshed_state[node.id] = node_state
        return divided_world, refreshed_outputs, refreshed_state, events, serial

    def _snapshot(
        self, outputs: Mapping[str, Mapping[str, np.ndarray]], world: World,
        time_s: float, index: int, events: list[dict] | None = None,
    ) -> Snapshot:
        channels_by_group: dict[str, list[tuple[str, np.ndarray]]] = {key: [] for key in world.groups}
        for channel_id, channel in self.run["channels"].items():
            values = outputs[channel["node"]][channel["port"]]
            channels_by_group[channel["group_id"]].append((channel_id, values))
        cells = []
        for group_id in sorted(world.groups):
            group = world.groups[group_id]
            for index_in_group, cell_id in enumerate(group.ids):
                cell = {
                    "id": cell_id,
                    "group_id": group_id,
                    "position_um": group.positions_um[index_in_group].tolist(),
                    "orientation_xyzw": group.orientation_xyzw[index_in_group].tolist(),
                    "channels": {
                        channel_id: float(values[index_in_group])
                        for channel_id, values in channels_by_group[group_id]
                    },
                }
                if self.frame_version == "0.2.0":
                    capsule = group.geometry[index_in_group]
                    cell["geometry"] = None if capsule is None else {
                        "shape": "capsule", "length_um": capsule.length_um,
                        "diameter_um": capsule.diameter_um,
                    }
                cells.append(cell)
        frame = {
            "protocol_version": "0.1.0", "run_id": self.run["run_id"],
            "frame_index": index, "time_s": time_s, "cells": cells, "events": events or [],
        }
        if self.frame_version == "0.2.0":
            frame["frame_version"] = self.frame_version
        fields = {}
        field_units = {}
        environment_fields = {}
        for node in self.plan.nodes:
            if node.owner_kind not in ("environment", "source"):
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
        next_world, outputs, state, events, serial = self._divide(next_world, outputs, state, next_time)
        snapshot = self._snapshot(outputs, next_world, next_time, self.frame_index + 1, events)
        self.frame_validator.accept(snapshot.cell_frame, validate_schema=False)
        self.outputs, self.state = outputs, state
        self.world = next_world
        self.time_s = next_time
        self.frame_index += 1
        self._next_cell_serial = serial
        self.current = snapshot
        return snapshot
