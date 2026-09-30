"""PTS surrogate formulas with explicit units and no global configuration.

Scalars and one-dimensional cell arrays are accepted. Scalars broadcast; two
non-scalar inputs must have exactly the same shape. Entity/species identity is
the caller's contract, not something a numerical array can establish.
"""
from dataclasses import dataclass, fields

import numpy as np


def _value(name, value, *, positive=False, upper=None, scalar=False):
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or raw.ndim > (0 if scalar else 1):
        raise ValueError(f"{name} must be a real numeric {'scalar' if scalar else 'scalar or 1D array'}")
    result = np.asarray(raw, dtype=float)
    if not np.all(np.isfinite(result)) or np.any(result < 0):
        raise ValueError(f"{name} must be finite and non-negative")
    if positive and np.any(result <= 0):
        raise ValueError(f"{name} must be positive")
    if upper is not None and np.any(result > upper):
        raise ValueError(f"{name} must be <= {upper}")
    return result


def _align(*values):
    shapes = {x.shape for x in values if x.ndim}
    if len(shapes) > 1:
        raise ValueError("cell arrays must have identical shapes; only scalars broadcast")
    return np.broadcast_arrays(*values)


def _product(name, *values):
    with np.errstate(over="ignore", invalid="ignore"):
        result = np.asarray(values[0], dtype=float)
        for value in values[1:]:
            result = result * value
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} exceeds finite float64 range")
    return result


@dataclass(frozen=True)
class RebuiltCapacityParameters:
    reference_pts_copies: float
    basal_inner_fraction: float
    pts_max_available_fraction: float
    ascf_area_um2: float

    def __post_init__(self):
        _value("reference_pts_copies", self.reference_pts_copies, positive=True, scalar=True)
        _value("ascf_area_um2", self.ascf_area_um2, positive=True, scalar=True)
        for name in ("basal_inner_fraction", "pts_max_available_fraction"):
            _value(name, getattr(self, name), upper=1, scalar=True)


@dataclass(frozen=True)
class SimplifiedCapacityParameters:
    reference_pts_copies: float
    g_cap: float
    reference_area_um2: float

    def __post_init__(self):
        for name in ("reference_pts_copies", "reference_area_um2"):
            _value(name, getattr(self, name), positive=True, scalar=True)
        _value("g_cap", self.g_cap, scalar=True)


@dataclass(frozen=True)
class CapacityReadout:
    target_copies: np.ndarray
    capacity_copies: np.ndarray
    functional_copies: np.ndarray
    g_effective: np.ndarray
    excess_copies: np.ndarray


def capsule_area_um2(total_length_um, diameter_um):
    """Total pole-to-pole length includes caps; L >= d > 0. Area = pi*d*L."""
    length, diameter = _align(
        _value("total_length_um", total_length_um, positive=True),
        _value("diameter_um", diameter_um, positive=True),
    )
    if np.any(length < diameter):
        raise ValueError("total_length_um must include end caps and be >= diameter_um")
    return _product("capsule area", np.pi, diameter, length)


def _capacity_result(target, capacity, reference):
    functional = np.minimum(target, capacity)
    with np.errstate(over="ignore", invalid="ignore"):
        effective = functional / reference
    return CapacityReadout(target.copy(), capacity.copy(), functional,
                           _value("g_effective", effective), target - functional)


def rebuilt_capacity(area_um2, g_requested, p: RebuiltCapacityParameters):
    """A footprint-limited total transporter copies, not concentration."""
    if not isinstance(p, RebuiltCapacityParameters):
        raise ValueError("rebuilt_capacity requires RebuiltCapacityParameters")
    area, gain = _align(_value("area_um2", area_um2), _value("g_requested", g_requested))
    with np.errstate(over="ignore", invalid="ignore"):
        density = (1 - p.basal_inner_fraction) * p.pts_max_available_fraction / p.ascf_area_um2
    capacity = _product("rebuilt capacity", area, density)
    return _capacity_result(_product("target copies", gain, p.reference_pts_copies),
                            capacity, p.reference_pts_copies)


def simplified_capacity(area_um2, g_requested, p: SimplifiedCapacityParameters):
    """B reference-area-scaled capacity; distinct from A footprint occupancy."""
    if not isinstance(p, SimplifiedCapacityParameters):
        raise ValueError("simplified_capacity requires SimplifiedCapacityParameters")
    area, gain = _align(_value("area_um2", area_um2), _value("g_requested", g_requested))
    with np.errstate(over="ignore", invalid="ignore"):
        ratio = area / p.reference_area_um2
    capacity = _product("simplified capacity", ratio, p.reference_pts_copies, p.g_cap)
    return _capacity_result(_product("target copies", gain, p.reference_pts_copies),
                            capacity, p.reference_pts_copies)


def pts_request(concentration_uM, functional_copies, turnover_s, half_saturation_uM):
    """Bulk-only saturation request (molecule/s per cell), before inventory settlement."""
    concentration, copies = _align(_value("concentration_uM", concentration_uM),
                                    _value("functional_copies", functional_copies))
    turnover = _value("turnover_s", turnover_s, scalar=True)
    half = _value("half_saturation_uM", half_saturation_uM, positive=True, scalar=True)
    # Scaled ratio avoids overflow in S + K for otherwise finite concentrations.
    scale = np.maximum(concentration, half)
    saturation = (concentration / scale) / (concentration / scale + half / scale)
    return _product("requested flux", copies, turnover, saturation)


@dataclass(frozen=True)
class SignalParameters:
    ei_total_uM: float
    ei_dephos_per_molecule: float
    ei_rephos_s: float
    ei_chea_inhibition_uM: float
    chea_total_uM: float
    chey_total_uM: float
    chey_phos_per_uM_s: float
    chey_dephos_s: float
    motor_hill: float
    motor_half_uM: float

    def __post_init__(self):
        for field in fields(self):
            _value(field.name, getattr(self, field.name), scalar=True,
                   positive=field.name in {"ei_chea_inhibition_uM", "motor_hill", "motor_half_uM"})


@dataclass(frozen=True)
class SignalReadout:
    ei_fraction: np.ndarray
    chey_p_uM: np.ndarray
    chea_active_uM: np.ndarray
    motor_bias: np.ndarray


def _check_signal_parameters(p):
    if not isinstance(p, SignalParameters):
        raise ValueError("signal formulas require SignalParameters")


def _chea(e, p):
    inhibited = _product("dephosphorylated EI concentration", e, p.ei_total_uM)
    scale = np.maximum(inhibited, p.ei_chea_inhibition_uM)
    fraction = (p.ei_chea_inhibition_uM / scale) / (
        p.ei_chea_inhibition_uM / scale + inhibited / scale)
    return _product("CheA activity", p.chea_total_uM, fraction)


def signal_readout(ei_fraction, chey_p_uM, p: SignalParameters):
    """Read state without adaptation or motion. motor_bias is a single-motor surrogate."""
    _check_signal_parameters(p)
    e, y = _align(_value("ei_fraction", ei_fraction, upper=1),
                  _value("chey_p_uM", chey_p_uM, upper=p.chey_total_uM))
    # Stable Hill evaluation; exactly zero signal gives exactly zero bias.
    with np.errstate(divide="ignore", over="ignore", under="ignore"):
        z = p.motor_hill * (np.log(y) - np.log(p.motor_half_uM))
        tail = np.exp(-np.abs(z))
    bias = np.where(z >= 0, 1 / (1 + tail), tail / (1 + tail))
    return SignalReadout(e.copy(), y.copy(), _chea(e, p), bias)


def _rates(on, off):
    # Scale to compute equilibrium fraction without overflowing on + off.
    scale = np.maximum(on, off)
    on_scaled = np.divide(on, scale, out=np.zeros_like(on), where=scale != 0)
    off_scaled = np.divide(off, scale, out=np.zeros_like(on), where=scale != 0)
    denominator = on_scaled + off_scaled
    equilibrium = np.divide(on_scaled, denominator, out=np.zeros_like(on), where=denominator != 0)
    return equilibrium, scale, denominator


def _relax(old, target, scale, denominator, dt):
    with np.errstate(over="ignore", under="ignore"):
        amount = -np.expm1(-(scale * dt) * denominator)
    # Interpolate from the nearer endpoint. This preserves equal endpoints
    # exactly and avoids an outward ulp from summing two rounded products.
    return np.where(amount <= .5, old + amount * (target - old),
                    target + (1 - amount) * (old - target))


def advance_accepted_signal(accepted_flux_molecules_s, ei_fraction, chey_p_uM,
                            dt_s, p: SignalParameters):
    """Constant accepted influx: exact EI, then CheY with endpoint CheA frozen.

    This is a first-order coupled integrator, not an exact solution of the full
    time-varying CheA/CheY system. dt must be positive and finite.
    """
    _check_signal_parameters(p)
    dt = _value("dt_s", dt_s, positive=True, scalar=True)
    flux, e, y = _align(_value("accepted_flux_molecules_s", accepted_flux_molecules_s),
                        _value("ei_fraction", ei_fraction, upper=1),
                        _value("chey_p_uM", chey_p_uM, upper=p.chey_total_uM))
    on = _product("EI rate", p.ei_dephos_per_molecule, flux)
    equilibrium, scale, denominator = _rates(on, p.ei_rephos_s)
    new_e = _relax(e, equilibrium, scale, denominator, dt)
    on_y = _product("CheY rate", p.chey_phos_per_uM_s, _chea(new_e, p))
    fraction, scale_y, denominator_y = _rates(on_y, p.chey_dephos_s)
    new_y = _relax(y, fraction * p.chey_total_uM, scale_y, denominator_y, dt)
    return signal_readout(new_e, new_y, p)


def steady_state_signal(accepted_flux_molecules_s, p: SignalParameters):
    """Constant accepted-flux equilibrium; reject nonunique zero-rate equilibria."""
    _check_signal_parameters(p)
    flux = _value("accepted_flux_molecules_s", accepted_flux_molecules_s)
    on = _product("EI rate", p.ei_dephos_per_molecule, flux)
    e, scale, _ = _rates(on, p.ei_rephos_s)
    if np.any(scale == 0):
        raise ValueError("EI steady state is not unique when both rates are zero")
    on_y = _product("CheY rate", p.chey_phos_per_uM_s, _chea(e, p))
    fraction, scale_y, _ = _rates(on_y, p.chey_dephos_s)
    if p.chey_total_uM != 0 and np.any(scale_y == 0):
        raise ValueError("CheY steady state is not unique when both rates are zero")
    return signal_readout(e, fraction * p.chey_total_uM, p)
