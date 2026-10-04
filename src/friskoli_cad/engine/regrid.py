"""Conservative transfer of voxel-average concentrations between uniform grids."""

from __future__ import annotations

import math

import numpy as np

from .core import GridDomain, SimulationError


def _remap_axis(values: np.ndarray, source_length: float, target_count: int, axis: int) -> np.ndarray:
    """Integrate a piecewise-constant field over each new interval on one axis."""
    moved = np.moveaxis(values, axis, 0)
    source_count = moved.shape[0]
    source_edges = np.linspace(0.0, source_length, source_count + 1)
    target_edges = np.linspace(0.0, source_length, target_count + 1)
    source_index = np.searchsorted(source_edges, target_edges, side="right") - 1
    source_index = np.clip(source_index, 0, source_count - 1)

    source_width = source_length / source_count
    cumulative = np.concatenate(
        (np.zeros_like(moved[:1]), np.cumsum(moved * source_width, axis=0)),
        axis=0,
    )
    offset = (target_edges - source_edges[source_index]).reshape(
        (-1,) + (1,) * (moved.ndim - 1)
    )
    integral_at_edges = cumulative[source_index] + moved[source_index] * offset
    remapped = np.diff(integral_at_edges, axis=0) / (source_length / target_count)
    return np.moveaxis(remapped, 0, axis)


def remap_concentration(
    field_uM: np.ndarray, source: GridDomain, target: GridDomain
) -> np.ndarray:
    """Transfer a concentration field while preserving each species' total molecules.

    Each source voxel is treated as uniformly concentrated within its volume.
    Grids may have unrelated positive integer counts, but must cover the same
    physical XYZ extent and use the same geometry mode. The returned field is
    a new array; this does not migrate a running Simulation or cell state.
    """
    if source.geometry != target.geometry or any(
        not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)
        for a, b in zip(source.extent_um, target.extent_um)
    ):
        raise SimulationError("grid.remap", "grids must cover the same extent and geometry")
    try:
        values = np.asarray(field_uM, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise SimulationError("grid.remap", "concentration field must be numeric") from error
    if values.shape != source.shape or not np.isfinite(values).all() or np.any(values < 0):
        raise SimulationError("grid.remap", "concentration field has invalid shape or values")
    if source == target:
        return values.copy()

    result = values
    for axis, length, count in zip(range(3), source.extent_um, target.shape):
        result = _remap_axis(result, length, count, axis)
    return result
