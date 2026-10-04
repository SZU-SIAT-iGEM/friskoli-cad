"""Public scientific state and errors shared by the modular engine."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping
import numpy as np
from .motion import heading_from_orientation

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
    metrics: Mapping[str, object] = field(default_factory=dict)
    lifecycle_details: Mapping[str, object] = field(default_factory=dict)
