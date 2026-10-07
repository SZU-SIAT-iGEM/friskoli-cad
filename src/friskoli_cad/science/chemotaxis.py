"""MCP receptor adaptation, CheY kinetics and shared motor readout."""
from dataclasses import dataclass, fields

import numpy as np

from .pts import _align, _product, _rates, _relax, _value


def _signed(name, value):
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or raw.ndim > 1:
        raise ValueError(f"{name} must be a real scalar or 1D array")
    out = np.asarray(raw, dtype=float)
    if not np.all(np.isfinite(out)):
        raise ValueError(f"{name} must be finite")
    return out


def motor_bias(chey_uM, *, half_uM, hill):
    y = _value("chey_uM", chey_uM)
    half = _value("half_uM", half_uM, positive=True, scalar=True)
    h = _value("hill", hill, positive=True, scalar=True)
    with np.errstate(divide="ignore", over="ignore", under="ignore"):
        z = h * (np.log(y) - np.log(half))
        tail = np.exp(-np.abs(z))
    return np.where(z >= 0, 1 / (1 + tail), tail / (1 + tail))


@dataclass(frozen=True)
class MWCParameters:
    cluster_size: float
    inactive_binding_uM: float
    active_binding_uM: float
    methylation_energy: float
    methylation_reference: float
    adaptation_rate_s: float
    baseline_activity: float

    def __post_init__(self):
        for f in fields(self):
            if f.name == "methylation_reference":
                if _signed(f.name, getattr(self, f.name)).ndim:
                    raise ValueError("methylation_reference must be scalar")
            else:
                _value(f.name, getattr(self, f.name), scalar=True,
                       positive=f.name != "adaptation_rate_s")
        if self.inactive_binding_uM >= self.active_binding_uM:
            raise ValueError("an attractant requires inactive_binding_uM < active_binding_uM")
        if self.baseline_activity >= 1:
            raise ValueError("baseline_activity must lie strictly between zero and one")


def _ligand_energy(ligand, p):
    # logaddexp avoids overflow in ligand / K and retains saturation at high L.
    with np.errstate(divide="ignore"):
        log_l = np.log(ligand)
    return (np.logaddexp(log_l, np.log(p.inactive_binding_uM)) - np.log(p.inactive_binding_uM)
            - np.logaddexp(log_l, np.log(p.active_binding_uM)) + np.log(p.active_binding_uM))


def mcp_activity(ligand_uM, methylation, p: MWCParameters):
    """MWC quasi-equilibrium activity; methylation is a reduced continuous state."""
    if not isinstance(p, MWCParameters):
        raise ValueError("mcp_activity requires MWCParameters")
    ligand, m = _align(_value("ligand_uM", ligand_uM), _signed("methylation", methylation))
    with np.errstate(over="ignore", invalid="ignore"):
        energy = p.cluster_size * (p.methylation_energy * (p.methylation_reference - m)
                                   + _ligand_energy(ligand, p))
    if np.any(np.isnan(energy)):
        raise ValueError("MWC free energy is not representable")
    with np.errstate(under="ignore"):
        tail = np.exp(-np.abs(energy))
    return np.where(energy >= 0, tail / (1 + tail), 1 / (1 + tail))


def mcp_adapted_methylation(ligand_uM, p: MWCParameters):
    if not isinstance(p, MWCParameters):
        raise ValueError("mcp_adapted_methylation requires MWCParameters")
    ligand = _value("ligand_uM", ligand_uM)
    offset = np.log1p(-p.baseline_activity) - np.log(p.baseline_activity)
    return _signed("adapted methylation", p.methylation_reference
                   + (_ligand_energy(ligand, p) - offset / p.cluster_size) / p.methylation_energy)


@dataclass(frozen=True)
class MCPReadout:
    methylation: np.ndarray
    activity: np.ndarray


def advance_mcp_adaptation(ligand_uM, methylation, dt_s, p: MWCParameters):
    """Backward Euler for dm/dt = k(a0-a), with frozen step-start ligand.

    The strictly increasing scalar implicit equation is bracketed by the two
    extreme activity rates. No hidden clipping of methylation or ligand occurs.
    This is a first-order numerical policy, not the fitted F(a) of Tu et al.
    """
    activity = mcp_activity(ligand_uM, methylation, p)
    ligand, old = _align(_value("ligand_uM", ligand_uM), _signed("methylation", methylation))
    dt = _value("dt_s", dt_s, scalar=True)
    amount = _product("adaptation increment bound", p.adaptation_rate_s, dt)
    if amount == 0:
        return MCPReadout(old.copy(), activity)
    lower = _signed("methylation lower bound", old - amount * (1 - p.baseline_activity))
    upper = _signed("methylation upper bound", old + amount * p.baseline_activity)
    for _ in range(64):
        mid = lower / 2 + upper / 2
        residual = (mid - old) - amount * (p.baseline_activity - mcp_activity(ligand, mid, p))
        lower = np.where(residual < 0, mid, lower)
        upper = np.where(residual > 0, mid, upper)
        lower = np.where(residual == 0, mid, lower)
        upper = np.where(residual == 0, mid, upper)
    new = lower / 2 + upper / 2
    return MCPReadout(new, mcp_activity(ligand, new, p))


@dataclass(frozen=True)
class CheYParameters:
    """Only parameters read by activity-to-CheY integration and motor readout."""
    chea_total_uM: float
    chey_total_uM: float
    chey_phos_per_uM_s: float
    chey_dephos_s: float
    motor_hill: float
    motor_half_uM: float

    def __post_init__(self):
        for field in fields(self):
            _value(field.name, getattr(self, field.name), scalar=True,
                   positive=field.name in {'motor_hill', 'motor_half_uM'})


def advance_chey_from_activity(activity, chey_uM, dt_s, p: CheYParameters):
    """Freeze MCP receptor activity, set CheA=A_total*activity, advance CheY."""
    if not isinstance(p, (CheYParameters)):
        raise ValueError("CheY integration requires CheYParameters")
    a, y = _align(_value("activity", activity, upper=1),
                 _value("chey_uM", chey_uM, upper=p.chey_total_uM))
    dt = _value("dt_s", dt_s, scalar=True)
    on = _product("CheY phosphorylation rate", a, p.chea_total_uM, p.chey_phos_per_uM_s)
    fraction, scale, denominator = _rates(on, p.chey_dephos_s)
    return _relax(y, fraction * p.chey_total_uM, scale, denominator, dt)
