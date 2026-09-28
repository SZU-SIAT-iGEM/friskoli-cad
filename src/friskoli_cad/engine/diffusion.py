"""Conservative finite-volume diffusion on a uniform rectangular grid."""

from __future__ import annotations

import math

import numpy as np

from .runtime import GridDomain, SimulationError


def explicit_no_flux_limit(grid: GridDomain, diffusivity_um2_s: float) -> float:
    """Conservative explicit Euler step bound for nonnegative diffusion weights."""
    if not math.isfinite(diffusivity_um2_s) or diffusivity_um2_s < 0:
        raise SimulationError("diffusion.coefficient", "diffusivity must be finite and nonnegative")
    inverse_square_sum = sum(
        1 / step_um**2
        for count, step_um in (
            (grid.nx, grid.dx_um), (grid.ny, grid.dy_um), (grid.nz, grid.dz_um)
        )
        if count > 1
    )
    if diffusivity_um2_s == 0 or inverse_square_sum == 0:
        return math.inf
    return 1 / (2 * diffusivity_um2_s * inverse_square_sum)


def no_flux_diffusion_rate(
    concentration_uM: np.ndarray, grid: GridDomain, diffusivity_um2_s: float
) -> np.ndarray:
    """Return dC/dt in uM/s, summing equal and opposite transfers over faces."""
    explicit_no_flux_limit(grid, diffusivity_um2_s)
    try:
        concentration = np.asarray(concentration_uM, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise SimulationError("diffusion.field", "concentration must be numeric") from error
    if (
        concentration.shape != grid.shape or not np.isfinite(concentration).all()
        or np.any(concentration < 0)
    ):
        raise SimulationError("diffusion.field", "concentration has invalid shape or values")
    rate = np.zeros(grid.shape, dtype=np.float64)
    for axis, count, step_um in (
        (2, grid.nx, grid.dx_um), (1, grid.ny, grid.dy_um), (0, grid.nz, grid.dz_um)
    ):
        if count < 2:
            continue
        face_rate = np.diff(concentration, axis=axis) * (diffusivity_um2_s / step_um**2)
        low = [slice(None)] * 3
        high = [slice(None)] * 3
        low[axis] = slice(None, -1)
        high[axis] = slice(1, None)
        rate[tuple(low)] += face_rate
        rate[tuple(high)] -= face_rate
    return rate
