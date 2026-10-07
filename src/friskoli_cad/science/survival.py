"""Phenomenological maintenance reserve and delayed starvation death.

These are constructed hypotheses, not strain-calibrated metabolic models.
Accepted nutrient is available at the interval start (operator splitting).
"""
from dataclasses import dataclass
import numpy as np

from .pts import _value, _align, _product


@dataclass(frozen=True)
class ReserveReadout:
    reserve_molecules: np.ndarray
    used_molecules: np.ndarray
    unmet_duration_s: np.ndarray
    correction_molecules: np.ndarray


@dataclass(frozen=True)
class StarvationReadout:
    starvation_time_s: np.ndarray
    health: np.ndarray
    integrated_hazard: np.ndarray
    death_hazard_per_min: np.ndarray


def advance_starvation(previous_s, unmet_duration_s, dt_s, *, grace_s, recovery_rate, death_rate_per_min):
    """Recover while maintenance is met, then accumulate unmet time.

    Hazard is zero up to grace_s, constant afterwards. Integrate the time
    above this threshold in both piecewise-linear exposure segments exactly.
    """
    old, unmet = _align(_value('starvation_time_s', previous_s), _value('unmet_duration_s', unmet_duration_s))
    dt = _value('dt_s', dt_s, scalar=True)
    grace = _value('grace_s', grace_s, scalar=True, positive=True)
    recovery = _value('recovery_rate', recovery_rate, scalar=True)
    rate = _value('death_rate_per_min', death_rate_per_min, scalar=True)
    if np.any(unmet > dt):
        raise ValueError('Unmet maintenance duration exceeds the interval')
    fed = dt - unmet
    recovered = np.maximum(0., old - recovery * fed)
    fed_exposed = np.where(old > grace, fed, 0.) if recovery == 0 else np.minimum(fed, np.maximum(0., (old - grace) / recovery))
    starved_exposed = np.maximum(0., unmet - np.maximum(0., grace - recovered))
    exposure = _value('starvation time', recovered + unmet)
    integrated = _product('integrated death hazard', rate / 60., fed_exposed + starved_exposed)
    average = np.zeros_like(old) if dt == 0 else integrated * 60. / dt
    return StarvationReadout(exposure, np.exp(-exposure / grace), integrated, average)
