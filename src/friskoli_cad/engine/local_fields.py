"""Pure, finite-inventory local fields on an aligned, impermeable voxel grid.

Reference splitting: release -> diffuse -> sample -> shared uptake. Public
snapshots own immutable tuples or arrays; commit replaces the state.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from fractions import Fraction
import math
from numbers import Real
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from .diffusion import explicit_no_flux_limit
from .runtime import GridDomain, SimulationError

ARRAY_THRESHOLD = 10_000


class FrozenGridArray(np.ndarray):
    """Compact immutable storage backed by bytes, so writeability cannot reset."""
    def __new__(cls, values, dtype=float):
        array = np.ascontiguousarray(values, dtype=dtype).reshape(-1)
        return np.frombuffer(array.tobytes(), dtype=array.dtype).view(cls)


def _stored_values(grid, values, *, boolean=False):
    if grid.voxel_count <= ARRAY_THRESHOLD:
        return tuple(bool(v) if boolean else float(v) for v in np.asarray(values).flat)
    if isinstance(values, FrozenGridArray):
        return values
    return FrozenGridArray(values, bool if boolean else float)


def _fail(message: str) -> None:
    raise SimulationError("local_fields.invalid", message)


def _number(value: float, name: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        _fail(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        _fail(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _budget(value: int, name: str) -> int:
    if type(value) is not int or value < 1:
        _fail(f"{name} must be a positive integer")
    return value


def _xyz(values: Sequence[float], name: str) -> tuple[float, float, float]:
    if len(values) != 3:
        _fail(f"{name} needs XYZ")
    return tuple(_number(v, name) for v in values)


@dataclass(frozen=True)
class FieldSpecies:
    id: str
    initial_uM: object
    diffusivity_um2_s: float


@dataclass(frozen=True)
class LocalSource:
    id: str
    species: str
    center_um: tuple[float, float, float]
    radius_um: float
    remaining_molecules: float
    release_rate_molecules_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.species, str) or not self.species:
            _fail("source id/species must be nonempty strings")
        object.__setattr__(self, "center_um", _xyz(self.center_um, "source center"))
        for name in ("radius_um", "remaining_molecules", "release_rate_molecules_s"):
            object.__setattr__(self, name, _number(getattr(self, name), name))


@dataclass(frozen=True)
class SolidAABB:
    min_um: tuple[float, float, float]
    max_um: tuple[float, float, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_um", _xyz(self.min_um, "obstacle min"))
        object.__setattr__(self, "max_um", _xyz(self.max_um, "obstacle max"))


@dataclass(frozen=True)
class LocalFieldState:
    grid: GridDomain
    concentrations_uM: Mapping[str, Sequence[float]]
    diffusivities_um2_s: Mapping[str, float]
    sources: tuple[LocalSource, ...]
    blocked: Sequence[bool]
    revision: int = 0
    backend: str = 'numpy-cpu'

    def __post_init__(self) -> None:
        if not isinstance(self.grid, GridDomain) or type(self.revision) is not int or self.revision < 0:
            _fail("state needs GridDomain and nonnegative integer revision")
        if self.backend not in ('numpy-cpu', 'numpy-cupy-cuda'):
            _fail('unknown field backend')
        dense = self.grid.voxel_count > ARRAY_THRESHOLD
        mask = np.asarray(self.blocked)
        if len(self.blocked) != self.grid.voxel_count or (mask.dtype != np.bool_ if dense else any(type(v) is not bool for v in self.blocked)):
            _fail("blocked mask must contain one boolean per voxel")
        if set(self.concentrations_uM) != set(self.diffusivities_um2_s):
            _fail("field/diffusivity species must match")
        for species, values in self.concentrations_uM.items():
            if not isinstance(species, str) or not species or len(values) != self.grid.voxel_count:
                _fail("state fields need species IDs and exactly one value per voxel")
            if dense:
                array = np.asarray(values, dtype=float)
                if not np.isfinite(array).all() or np.any(array < 0) or np.any(array[mask] != 0):
                    _fail('state concentration must be finite, nonnegative and zero inside obstacles')
            else:
                for value, blocked in zip(values, self.blocked):
                    value = _number(value, "state concentration")
                    if blocked and value != 0:
                        _fail("obstacle voxels cannot contain concentration")
            _number(self.diffusivities_um2_s[species], "diffusivity_um2_s")
        object.__setattr__(self, "concentrations_uM", MappingProxyType(
            {key: _stored_values(self.grid, value) for key, value in self.concentrations_uM.items()}))
        object.__setattr__(self, "diffusivities_um2_s", MappingProxyType(dict(self.diffusivities_um2_s)))
        object.__setattr__(self, "sources", tuple(self.sources))
        object.__setattr__(self, "blocked", _stored_values(self.grid, self.blocked, boolean=True))


@dataclass(frozen=True)
class FieldSamples:
    concentration_uM: Mapping[str, tuple[float, ...]]
    gradient_uM_um: Mapping[str, tuple[tuple[float, float, float], ...]]
    support_indices: tuple[int, ...]


@dataclass(frozen=True)
class FieldLedger:
    field_before_molecules: float
    source_before_molecules: float
    source_after_molecules: float
    released_molecules: float
    field_after_release_molecules: float
    field_after_diffusion_molecules: float
    accepted_uptake_molecules: float
    field_after_molecules: float
    conservation_residual_molecules: float
    conservation_bound_molecules: float
    representability_shortfall_molecules: float = 0.


@dataclass(frozen=True)
class LocalFieldProposal:
    before: LocalFieldState
    after: LocalFieldState
    samples_before_uptake: FieldSamples
    accepted_uptake_molecules: Mapping[str, tuple[float, ...]]
    ledgers: Mapping[str, FieldLedger]
    source_released_molecules: Mapping[str, float]
    substeps: int


def _source_indices(grid: GridDomain, source: LocalSource, blocked: np.ndarray) -> np.ndarray:
    center_index = int(grid.flat_indices(np.asarray([source.center_um]))[0])
    if blocked.flat[center_index]:
        _fail(f"source {source.id} center lies in solid obstacle")
    if source.radius_um == 0:
        return np.asarray([center_index], dtype=np.int64)
    # Select voxel centers, retaining the center's voxel for sub-grid radii.
    # Source support is local. Never allocate an entire domain per source.
    counts = np.array((grid.nx, grid.ny, grid.nz))
    spacing = np.array((grid.dx_um, grid.dy_um, grid.dz_um))
    with np.errstate(over='ignore'):
        lower = np.floor(np.clip((np.asarray(source.center_um)-source.radius_um)/spacing-.5, 0, counts)).astype(int)
        upper = np.ceil(np.clip((np.asarray(source.center_um)+source.radius_um)/spacing+.5, 0, counts)).astype(int)
    upper = np.minimum(counts, upper+1)
    z, y, x = np.ogrid[lower[2]:upper[2], lower[1]:upper[1], lower[0]:upper[0]]
    # A squared distance underflow is safely inside the source, not lost mass.
    with np.errstate(over="ignore", under="ignore"):
        distance = (((x + .5) * grid.dx_um - source.center_um[0]) / source.radius_um) ** 2
        distance = distance + (((y + .5) * grid.dy_um - source.center_um[1]) / source.radius_um) ** 2
        distance = distance + (((z + .5) * grid.dz_um - source.center_um[2]) / source.radius_um) ** 2
        support = distance <= 1
    iz, iy, ix = np.nonzero(support)
    indices = np.unique(np.append(((iz+lower[2])*grid.ny+iy+lower[1])*grid.nx+ix+lower[0],center_index))
    if np.any(blocked.flat[indices]):
        _fail(f"source {source.id} support intersects solid obstacle")
    return indices


def make_local_field_state(
    grid: GridDomain, species: Sequence[FieldSpecies], *,
    sources: Sequence[LocalSource] = (), obstacles: Sequence[SolidAABB] = (),
    max_voxels: int = 250_000, max_values: int = 1_000_000,
    backend: str = 'numpy-cpu',
) -> LocalFieldState:
    """Validate sizes before allocating. Scalar initial values apply to fluid only."""
    if not isinstance(grid, GridDomain):
        _fail("grid must be GridDomain")
    if grid.voxel_count > _budget(max_voxels, "max_voxels"):
        _fail("grid exceeds max_voxels resource budget")
    if grid.voxel_count * max(1, len(species) + len(sources)) > _budget(max_values, "max_values"):
        _fail("species/source grid exceeds max_values resource budget")
    factor = grid.molecules_per_uM_voxel
    if not math.isfinite(factor) or factor <= 0 or not all(math.isfinite(v) for v in grid.extent_um):
        _fail("grid volume/extent is not representable")
    blocked = np.zeros(grid.shape, dtype=bool)
    spacing = (grid.dx_um, grid.dy_um, grid.dz_um)
    for obstacle in obstacles:
        if not isinstance(obstacle, SolidAABB):
            _fail("obstacles must be SolidAABB")
        indices = []
        for low, high, step, extent in zip(obstacle.min_um, obstacle.max_um, spacing, grid.extent_um):
            if low >= high or high > extent:
                _fail("obstacle must have positive extents inside domain")
            pair = (low / step, high / step)
            if any(abs(v - round(v)) > 8 * math.ulp(max(1., v)) for v in pair):
                _fail("obstacle boundaries must be whole-grid aligned; cut cells unsupported")
            indices.append(slice(round(pair[0]), round(pair[1])))
        blocked[indices[2], indices[1], indices[0]] = True
    fields, diffusivities = {}, {}
    for entry in species:
        if not isinstance(entry, FieldSpecies) or not isinstance(entry.id, str) or not entry.id or entry.id in fields:
            _fail("species IDs must be nonempty and unique")
        diffusivities[entry.id] = _number(entry.diffusivity_um2_s, "diffusivity_um2_s")
        try:
            initial = np.asarray(entry.initial_uM, dtype=float)
        except (ValueError, TypeError) as exc:
            raise SimulationError("local_fields.invalid", "initial concentration must be numeric") from exc
        if initial.ndim == 0:
            field = np.full(grid.shape, float(initial))
            field[blocked] = 0
        elif initial.shape == grid.shape:
            field = initial.copy()
        else:
            _fail("initial field shape must equal grid.shape (z,y,x)")
        if not np.isfinite(initial).all() or np.any(initial < 0) or np.any(field[blocked] != 0):
            _fail("initial field must be finite, nonnegative and zero in obstacles")
        _mass(field, factor)
        fields[entry.id] = _stored_values(grid, field)
    seen = set()
    for source in sources:
        if not isinstance(source, LocalSource) or source.id in seen or source.species not in fields:
            _fail("sources need unique IDs and declared species")
        seen.add(source.id)
        _source_indices(grid, source, blocked)
    _source_total(sources)
    return LocalFieldState(grid, fields, diffusivities, tuple(sources), _stored_values(grid, blocked, boolean=True), backend=backend)


def copy_concentrations(state: LocalFieldState) -> dict[str, np.ndarray]:
    """Independent, writable shaped arrays for callers; cannot change snapshot."""
    return {key: np.array(values).reshape(state.grid.shape) for key, values in state.concentrations_uM.items()}


def local_field_state_to_dict(state: LocalFieldState, *, binary=False) -> dict:
    """JSON-compatible checkpoint; no executable serialization format."""
    grid = state.grid
    return {"format": "local_fields/v1", "grid": {
        "geometry": grid.geometry, "nx": grid.nx, "ny": grid.ny, "nz": grid.nz,
        "dx_um": grid.dx_um, "dy_um": grid.dy_um, "dz_um": grid.dz_um},
        "concentrations_uM": {s: (np.asarray(v) if binary else np.asarray(v).tolist()) for s, v in state.concentrations_uM.items()},
        "diffusivities_um2_s": dict(state.diffusivities_um2_s), "blocked": np.asarray(state.blocked) if binary else np.asarray(state.blocked).tolist(),
        **({'backend':state.backend} if state.backend != 'numpy-cpu' else {}),
        "revision": state.revision, "sources": [dict(id=s.id, species=s.species,
            center_um=list(s.center_um), radius_um=s.radius_um, remaining_molecules=s.remaining_molecules,
            release_rate_molecules_s=s.release_rate_molecules_s) for s in state.sources]}


def local_field_state_from_dict(data: Mapping, *, max_voxels: int = 250_000,
                                max_values: int = 1_000_000) -> LocalFieldState:
    """Validate a checkpoint including every mask, stock and support value."""
    try:
        if data.get("format") != "local_fields/v1":
            _fail("unsupported local field checkpoint format")
        grid = GridDomain(**data["grid"])
        fields, coefficients, sources = data["concentrations_uM"], data["diffusivities_um2_s"], data["sources"]
        if grid.voxel_count > _budget(max_voxels, "max_voxels"):
            _fail("checkpoint exceeds max_voxels")
        if grid.voxel_count * max(1, len(fields) + len(sources)) > _budget(max_values, "max_values"):
            _fail("checkpoint exceeds max_values")
        mask = data['blocked']
        if grid.voxel_count <= ARRAY_THRESHOLD:
            fields = {key: value.tolist() if isinstance(value, np.ndarray) else value for key,value in fields.items()}
            mask = mask.tolist() if isinstance(mask, np.ndarray) else mask
        state = LocalFieldState(grid, fields, coefficients, tuple(LocalSource(**s) for s in sources),
                                mask, data["revision"], data.get('backend','numpy-cpu'))
        blocked = np.asarray(state.blocked).reshape(grid.shape)
        seen = set()
        for source in state.sources:
            if source.id in seen or source.species not in state.concentrations_uM:
                _fail("checkpoint sources need unique IDs and declared species")
            seen.add(source.id)
            _source_indices(grid, source, blocked)
        _source_total(state.sources)
        factor = grid.molecules_per_uM_voxel
        if not math.isfinite(factor) or factor <= 0 or not all(math.isfinite(v) for v in grid.extent_um):
            _fail("checkpoint grid volume/extent is not representable")
        for values in state.concentrations_uM.values():
            _mass(np.asarray(values), factor)
        return state
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise SimulationError("local_fields.invalid", "malformed local field checkpoint") from exc


def _positions(state: LocalFieldState, positions_um: Sequence[Sequence[float]]) -> tuple[np.ndarray, np.ndarray]:
    try:
        positions = np.asarray(positions_um, dtype=float)
    except (TypeError, ValueError) as exc:
        raise SimulationError("local_fields.invalid", "positions must be finite XYZ rows") from exc
    if positions.size == 0:
        positions = np.empty((0, 3))
    indices = state.grid.flat_indices(positions)
    blocked = np.asarray(state.blocked).reshape(state.grid.shape)
    if np.any(blocked.ravel()[indices]):
        _fail("cell sampling/uptake in solid obstacle is unsupported")
    return indices, blocked


def sample_local_fields(state: LocalFieldState, positions_um: Sequence[Sequence[float]]) -> FieldSamples:
    """Containing-voxel samples, with reflective ghost values for gradients."""
    indices, blocked = _positions(state, positions_um)
    return _sample_arrays(state.grid, state.concentrations_uM, indices, blocked)


def _sample_arrays(grid, fields, indices, blocked):
    """Read private work arrays without manufacturing another full snapshot."""
    concentrations, gradients = {}, {}
    for species, flat in fields.items():
        field = np.asarray(flat).reshape(grid.shape)
        concentrations[species] = tuple(float(field.flat[i]) for i in indices)
        rows = []
        for index in indices:
            point = np.unravel_index(index, grid.shape)
            row = []
            for axis, step in ((2, grid.dx_um), (1, grid.dy_um), (0, grid.dz_um)):
                neighbors = []
                for direction in (-1, 1):
                    neighbor = list(point)
                    neighbor[axis] += direction
                    valid = 0 <= neighbor[axis] < grid.shape[axis]
                    neighbors.append(float(field[tuple(neighbor)]) if valid and not blocked[tuple(neighbor)] else float(field[point]))
                row.append((neighbors[1] - neighbors[0]) / (2 * step))
            rows.append(tuple(row))
        gradients[species] = tuple(rows)
    return FieldSamples(MappingProxyType(concentrations), MappingProxyType(gradients), tuple(int(v) for v in indices))


def _mass(field: np.ndarray, factor: float) -> float:
    if field.size > ARRAY_THRESHOLD:
        amounts = np.asarray(field).reshape(-1) * factor
        if not np.isfinite(amounts).all() or np.any((field.reshape(-1)>0) & (amounts==0)):
            _fail('field inventory conversion is not representable')
        # Pairwise block reductions plus compensated summation of block totals.
        complete=amounts.size//1024*1024
        result=math.fsum((*np.sum(amounts[:complete].reshape(-1,1024),axis=1), math.fsum(amounts[complete:])))
        if not math.isfinite(result):
            _fail('field inventory is not finite')
        return result
    try:
        amounts = []
        for value in field.flat:
            amount = float(value) * factor
            if value > 0 and amount == 0:
                _fail("field inventory conversion underflows")
            amounts.append(amount)
        result = math.fsum(amounts)
    except OverflowError as exc:
        raise SimulationError("local_fields.invalid", "field inventory overflows") from exc
    if not math.isfinite(result):
        _fail("field inventory is not finite")
    return result


def _source_total(sources: Sequence[LocalSource]) -> float:
    try:
        return math.fsum(source.remaining_molecules for source in sources)
    except OverflowError as exc:
        raise SimulationError("local_fields.invalid", "source inventory total overflows") from exc


def _audit_stage(before: float, transfer: float, after: float, operations: int, name: str) -> None:
    residual = math.fsum((before, transfer, -after))
    bound = 64 * math.ulp(max(before, abs(transfer), after)) * max(1, operations)
    if not math.isfinite(bound) or abs(residual) > bound:
        _fail(f"{name} conservation exceeds roundoff bound")


def _audit_transfer(before: float, after: float, transferred: float, factor: float = 1.) -> None:
    """A large stock must not hide material error relative to a small transfer."""
    delta = (Fraction(float(after)) - Fraction(float(before))) * Fraction(float(factor))
    bound = 1e-10 * abs(transferred) + 8 * math.ulp(transferred)
    if abs(delta - Fraction(float(transferred))) > Fraction(bound):
        _fail("material transfer exceeds float64 transfer-relative precision budget")


def _floor_nonnegative(value: Fraction) -> float:
    result = float(value)
    return math.nextafter(result, 0.) if Fraction(result) > value else result


def _voxel_uptake(concentration, factor, fluxes, dt):
    """Proportional uptake capped by the field's representable debit.

    A request is not accepted until its field debit can be represented within
    the existing transfer budget. Unfulfilled amount stays in the same voxel.
    No precision exception, time-step retry or artificial nutrient pool is used.
    """
    before, conversion = Fraction(float(concentration)), Fraction(float(factor))
    stock = before * conversion
    requests = tuple(float(flux) * dt for flux in fluxes)
    if any(not math.isfinite(value) for value in requests):
        _fail('uptake flux * dt overflows')
    demand = sum(map(Fraction, requests), Fraction())
    if not demand or not stock:
        return concentration, (0.,) * len(requests), 0.
    scale = min(Fraction(1), stock / demand)
    ideal = tuple(_floor_nonnegative(Fraction(value) * scale) for value in requests)
    ideal_total = sum(map(Fraction, ideal), Fraction())
    if not ideal_total:
        return concentration, ideal, 0.
    # Preserve the ordinary reference path when its unit conversion already
    # satisfies the strict material-transfer budget.
    legacy_stock = float(concentration) * factor
    remaining = max(0., float(Fraction(legacy_stock) - ideal_total))
    candidate = 0. if demand >= stock else remaining / factor
    actual = (before - Fraction(candidate)) * conversion
    accepted_total = math.fsum(ideal)
    bound = Fraction(1e-10 * accepted_total + 8 * math.ulp(accepted_total))
    if 0 <= candidate < concentration and abs(actual - Fraction(accepted_total)) <= bound:
        return candidate, ideal, 0.
    # Round the exact remaining concentration upward: a quantized debit must
    # never exceed the ideal request or remove nutrient that was not accepted.
    target = before - ideal_total / conversion
    candidate = float(target)
    if Fraction(candidate) < target:
        candidate = math.nextafter(candidate, math.inf)
    actual = (before - Fraction(candidate)) * conversion
    if not 0 <= actual <= ideal_total or not 0 <= candidate <= concentration:
        _fail('representable uptake plan exceeds its available inventory')
    accepted = tuple(_floor_nonnegative(Fraction(value) * actual / ideal_total) for value in ideal)
    _audit_transfer(concentration, candidate, -math.fsum(accepted), factor)
    shortfall = float(ideal_total - sum(map(Fraction, accepted), Fraction()))
    return candidate, accepted, shortfall


def _diffuse(field: np.ndarray, grid: GridDomain, diffusion: float, dt: float, blocked: np.ndarray) -> np.ndarray:
    rate = np.zeros(grid.shape)
    for axis, spacing in ((2, grid.dx_um), (1, grid.dy_um), (0, grid.dz_um)):
        if grid.shape[axis] == 1:
            continue
        lo, hi = [slice(None)] * 3, [slice(None)] * 3
        lo[axis], hi[axis] = slice(None, -1), slice(1, None)
        lo, hi = tuple(lo), tuple(hi)
        coefficient = diffusion * dt / spacing**2
        flux = (field[hi] - field[lo]) * coefficient
        flux[blocked[hi] | blocked[lo]] = 0
        rate[lo] += flux
        rate[hi] -= flux
    result = field + rate
    if not np.isfinite(result).all() or np.any(result < 0):
        _fail("diffusion produced unrepresentable/negative concentration; no clipping performed")
    return result


def propose_local_field_step(
    state: LocalFieldState, dt_s: float, *, cell_ids: Sequence[str | int] = (),
    positions_um: Sequence[Sequence[float]] = (),
    requested_uptake_molecules_s: Mapping[str, Sequence[float]] | None = None,
    max_substeps: int = 10_000, max_work_items: int = 2_000_000_000,
) -> LocalFieldProposal:
    """Propose a full step without modifying inputs; no uptake is implicit.

    All species use a common conservative substep (90% of Cartesian CFL).
    The budget is checked before any field-sized work buffers are allocated.
    Uptake is proportional among all cells sharing a containing voxel.
    """
    dt = _number(dt_s, "dt_s", positive=True)
    _budget(max_substeps, "max_substeps")
    _budget(max_work_items, "max_work_items")
    try:
        limits = [explicit_no_flux_limit(state.grid, d) for d in state.diffusivities_um2_s.values()]
    except (OverflowError, ZeroDivisionError) as exc:
        raise SimulationError("local_fields.invalid", "grid/CFL arithmetic is not representable") from exc
    limit = min(limits, default=math.inf) * .9
    ratio = dt / limit if limit else math.inf
    if not math.isfinite(ratio) or ratio > max_substeps:
        _fail("diffusion exceeds max_substeps budget")
    substeps = max(1, math.ceil(ratio))
    work = state.grid.voxel_count * (1 + len(state.concentrations_uM) * (substeps + 3) + len(state.sources))
    work += len(cell_ids) * max(1, len(state.concentrations_uM)) * 8
    if work > max_work_items:
        _fail("step exceeds max_work_items resource budget")
    if len(set(cell_ids)) != len(cell_ids) or any(isinstance(v, bool) or not isinstance(v, (str, int)) or v == "" for v in cell_ids):
        _fail("cell_ids must be unique nonempty string or integer IDs")
    if len(positions_um) != len(cell_ids):
        _fail("cell IDs and positions must have matching lengths")
    indices, blocked = _positions(state, positions_um)
    if len(cell_ids) != len(indices):
        _fail("cell IDs and positions must have matching lengths")
    requests = {} if requested_uptake_molecules_s is None else dict(requested_uptake_molecules_s)
    for species, values in requests.items():
        if species not in state.concentrations_uM or len(values) != len(cell_ids):
            _fail("uptake requests need declared species and one flux per cell")
        requests[species] = tuple(_number(v, "uptake flux") for v in values)
    fields = copy_concentrations(state)
    factor = state.grid.molecules_per_uM_voxel
    before_mass = {s: _mass(f, factor) for s, f in fields.items()}
    new_sources, released_by_source = [], {}
    for source in state.sources:
        if source.remaining_molecules == 0 or source.release_rate_molecules_s == 0:
            released = 0.
        elif source.release_rate_molecules_s >= source.remaining_molecules / dt:
            released = source.remaining_molecules
        else:
            released = source.release_rate_molecules_s * dt
            if not math.isfinite(released) or released == 0:
                _fail("source release rate * dt is not representable")
        remaining = float(Fraction(source.remaining_molecules) - Fraction(released))
        if released and remaining == source.remaining_molecules:
            _fail("source inventory subtraction cannot represent release")
        _audit_transfer(source.remaining_molecules, remaining, -released)
        _audit_stage(source.remaining_molecules, -released, remaining, 1, "source debit")
        new_sources.append(replace(source, remaining_molecules=remaining))
        released_by_source[source.id] = released
        if released:
            support = _source_indices(state.grid, source, blocked)
            increment = released / len(support) / factor
            old = fields[source.species].ravel()[support].copy()
            fields[source.species].ravel()[support] += increment
            if increment == 0 or np.any(fields[source.species].ravel()[support] == old):
                _fail("field cannot represent source release")
            for previous, updated in zip(old, fields[source.species].ravel()[support]):
                _audit_transfer(previous, updated, released / len(support), factor)
    release_mass = {s: _mass(f, factor) for s, f in fields.items()}
    for species in fields:
        release = math.fsum(released_by_source[s.id] for s in state.sources if s.species == species)
        _audit_stage(before_mass[species], release, release_mass[species], len(state.sources) + 1, "source-to-field release")
    if state.backend == 'numpy-cupy-cuda':
        from .field_backend import diffuse_cuda
        for species, field in fields.items():
            fields[species] = diffuse_cuda(field,state.grid,state.diffusivities_um2_s[species],dt,blocked,substeps)
    else:
        for _ in range(substeps):
            for species, field in fields.items():
                fields[species] = _diffuse(field, state.grid, state.diffusivities_um2_s[species], dt / substeps, blocked)
    diffused_mass = {s: _mass(f, factor) for s, f in fields.items()}
    for species in fields:
        _audit_stage(release_mass[species], 0., diffused_mass[species], substeps, "diffusion")
    samples = _sample_arrays(state.grid, fields, indices, blocked)
    accepted = {s: [0.] * len(cell_ids) for s in fields}
    numerical_shortfalls = {s: [] for s in fields}
    groups: dict[int, list[int]] = {}
    for cell_index, voxel_index in enumerate(indices):
        groups.setdefault(int(voxel_index), []).append(cell_index)
    for species, fluxes in requests.items():
        for voxel, members in groups.items():
            new_concentration, amounts, shortfall = _voxel_uptake(
                float(fields[species].flat[voxel]), factor, tuple(fluxes[i] for i in members), dt)
            numerical_shortfalls[species].append(shortfall)
            fields[species].flat[voxel] = new_concentration
            for index, amount in zip(members, amounts):
                accepted[species][index] = amount
    ledgers = {}
    for species, field in fields.items():
        source_before = _source_total([s for s in state.sources if s.species == species])
        source_after = _source_total([s for s in new_sources if s.species == species])
        released = math.fsum(released_by_source[s.id] for s in state.sources if s.species == species)
        consumed = math.fsum(accepted[species])
        after_mass = _mass(field, factor)
        _audit_stage(diffused_mass[species], -consumed, after_mass, len(cell_ids) + 1, "uptake-to-field debit")
        residual = math.fsum((before_mass[species], source_before, -source_after, -consumed, -after_mass))
        scale = max(before_mass[species], source_before, after_mass, consumed)
        bound = 64 * math.ulp(scale) * (substeps + len(state.sources) + len(cell_ids) + 1)
        if not math.isfinite(bound) or abs(residual) > bound:
            _fail("source/field/uptake conservation ledger exceeds roundoff bound")
        ledgers[species] = FieldLedger(before_mass[species], source_before, source_after, released,
            release_mass[species], diffused_mass[species], consumed, after_mass, residual, bound,
            math.fsum(numerical_shortfalls[species]))
    after = LocalFieldState(state.grid, {s: _stored_values(state.grid,f) for s, f in fields.items()},
                            state.diffusivities_um2_s, tuple(new_sources), state.blocked, state.revision + 1, state.backend)
    return LocalFieldProposal(state, after, samples, MappingProxyType({s: tuple(v) for s, v in accepted.items()}),
                              MappingProxyType(ledgers), MappingProxyType(released_by_source), substeps)
