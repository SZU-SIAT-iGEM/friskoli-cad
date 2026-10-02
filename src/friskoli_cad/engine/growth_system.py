"""System growth realization: conservative geometry and resource accounting.

Scientific modules propose an ideal nutrient-limited growth amount. This shared
system realizes it on the actual capsule geometry; it contains no model IDs or
A/B parameter choices. Collision admission and atomic commit remain in runtime.
"""
from fractions import Fraction
import math

import numpy as np

from friskoli_cad.science.pts import _align, _value
from friskoli_cad.science.physiology import GrowthReadout, capsule_volume_um3, capsule_geometry_from_volume


def realize_capsule_growth(stock_molecules, old_length_um, diameter_um, requested_used_molecules,
                           dt_s, volume_yield_um3_molecule):
    """Realize an ideal growth proposal on the float64 capsule geometry lattice.

    Return (GrowthReadout, lengths). Growth that cannot enlarge the represented
    capsule stays in the existing intracellular inventory; no extra pool exists.
    Nutrient consumption and growth rate use the actual geometric volume change.
    """
    stock, length, diameter, requested = _align(
        _value('stock_molecules', stock_molecules), _value('old_length_um', old_length_um, positive=True),
        _value('diameter_um', diameter_um, positive=True), _value('requested_used_molecules', requested_used_molecules))
    if np.any(requested > stock):
        raise ValueError('Growth cannot consume more than available nutrient')
    dt = float(_value('dt_s', dt_s, positive=True, scalar=True)) / 60
    yield_v = float(_value('volume_yield_um3_molecule', volume_yield_um3_molecule, positive=True, scalar=True))
    old_volume = np.asarray(capsule_volume_um3(length, diameter))
    volumes, lengths, used = old_volume.copy(), length.copy(), np.zeros_like(stock)
    for index in np.ndindex(stock.shape):
        amount, before, old_l, d = map(float, (requested[index], old_volume[index], length[index], diameter[index]))
        if amount == 0:
            continue
        budget = Fraction(amount) * Fraction(yield_v)
        target = float(Fraction(before) + budget)
        if not math.isfinite(target):
            raise ValueError('Realized growth volume exceeds float64 range')
        candidate = max(old_l, float(capsule_geometry_from_volume(target, d / 2).total_length_um))
        # Inverse and forward geometry each round. Walk down to a capsule whose
        # exact represented volume increment cannot exceed the nutrient budget.
        for _ in range(16):
            actual_volume = float(capsule_volume_um3(candidate, d))
            increment = Fraction(actual_volume) - Fraction(before)
            if increment <= budget:
                break
            candidate = max(old_l, math.nextafter(candidate, -math.inf))
        else:
            raise ValueError('Could not realize conservative capsule growth')
        if candidate <= old_l or increment <= 0:
            continue
        consumed = min(amount, float(increment / Fraction(yield_v)))
        if consumed == 0:
            continue
        lengths[index], volumes[index], used[index] = candidate, actual_volume, consumed
    actual = (volumes - old_volume) / old_volume / dt
    return GrowthReadout(stock - used, volumes, used, actual), lengths
