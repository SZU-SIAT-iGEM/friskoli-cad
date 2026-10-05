"""Voxel overlap weights for a cell's fixed physical sampling region."""

from __future__ import annotations

import math

import numpy as np

from .core import GridDomain, SimulationError


def _axis_overlap(
    center_um: float, support_um: float, voxel_um: float, count: int
) -> tuple[np.ndarray, np.ndarray]:
    left = max(0.0, center_um - support_um / 2)
    right = min(count * voxel_um, center_um + support_um / 2)
    if right <= left:
        raise SimulationError("spatial.support", "support has no overlap with the domain")
    indices = np.arange(max(0, math.floor(left / voxel_um)), min(count, math.ceil(right / voxel_um)))
    lengths = np.minimum(right, (indices + 1) * voxel_um) - np.maximum(left, indices * voxel_um)
    positive = lengths > 0
    return indices[positive], lengths[positive]


def box_overlap_weights(
    grid: GridDomain, position_um: np.ndarray, support_xyz_um: tuple[float, float, float]
) -> tuple[np.ndarray, np.ndarray]:
    """Return flattened voxel indices and normalized box-overlap volumes.

    The support is centered at the cell position and clipped to the domain.
    Its dimensions are physical interaction lengths, not a claim about cell size.
    """
    position = np.asarray(position_um, dtype=np.float64)
    support = np.asarray(support_xyz_um, dtype=np.float64)
    if (
        position.shape != (3,) or support.shape != (3,)
        or not np.isfinite(position).all() or not np.isfinite(support).all()
        or np.any(support <= 0) or np.any(position < 0)
        or np.any(position >= grid.extent_um)
    ):
        raise SimulationError("spatial.support", "invalid cell position or support dimensions")

    ix, wx = _axis_overlap(position[0], support[0], grid.dx_um, grid.nx)
    iy, wy = _axis_overlap(position[1], support[1], grid.dy_um, grid.ny)
    iz, wz = _axis_overlap(position[2], support[2], grid.dz_um, grid.nz)
    z, y, x = np.meshgrid(iz, iy, ix, indexing="ij")
    indices = ((z * grid.ny + y) * grid.nx + x).ravel()
    overlap = (wz[:, None, None] * wy[None, :, None] * wx[None, None, :]).ravel()
    weights = overlap / overlap.sum()
    return indices, weights
