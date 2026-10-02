"""Phenomenological maintenance reserve and delayed starvation death.

These are constructed hypotheses, not strain-calibrated metabolic models.
Accepted nutrient is available at the interval start (operator splitting).
"""
from dataclasses import dataclass
from fractions import Fraction
import math
import numpy as np

from .pts import _value, _align, _product


@dataclass(frozen=True)
class ReserveReadout:
    reserve_molecules: np.ndarray
    used_molecules: np.ndarray
    unmet_duration_s: np.ndarray
    correction_molecules: np.ndarray


def advance_reserve(reserve, accepted, dt_s, maintenance_molecules_s, correction=None):
    reserve, accepted = _align(_value('reserve', reserve), _value('accepted', accepted))
    dt = _value('dt_s', dt_s, scalar=True)
    rate = _value('maintenance_molecules_s', maintenance_molecules_s, scalar=True)
    demand = _product('maintenance demand', rate, dt)
    from .chemotaxis import _signed
    low = np.zeros_like(reserve) if correction is None else _signed('reserve correction', correction)
    reserve, accepted, low = _align(reserve, accepted, low)
    after, used, next_low = np.empty_like(reserve), np.empty_like(reserve), np.empty_like(reserve)
    # A two-component amount retains arrivals below the current reserve ULP.
    # Exact binary rationals determine consumption before rounding either part;
    # this changes no biological rates and avoids silently dropping tiny flux.
    for index in np.ndindex(reserve.shape):
        total = Fraction(float(reserve[index])) + Fraction(float(low[index])) + Fraction(float(accepted[index]))
        if total < 0:
            raise ValueError('Corrected reserve must be nonnegative')
        consumed_exact = min(total, Fraction(float(demand)))
        consumed = float(consumed_exact)
        if Fraction(consumed) > consumed_exact:
            consumed = math.nextafter(consumed, 0.)
        remainder = total - Fraction(consumed)
        high = float(remainder)
        if not math.isfinite(high):
            raise ValueError('Reserve overflows')
        tail = float(remainder - Fraction(high))
        residual = abs(remainder - Fraction(high) - Fraction(tail))
        bound = 1e-10 * float(accepted[index]) + 8 * math.ulp(float(accepted[index]))
        if residual > Fraction(bound):
            raise ValueError('Reserve compensation exceeds the transfer precision budget')
        after[index], used[index], next_low[index] = high, consumed, tail
    unmet = np.zeros_like(after) if rate == 0 else np.maximum(0., dt - used / rate)
    return ReserveReadout(after, used, unmet, next_low)


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
