"""Direct-bulk hydrolysis requests with finite solid inventory.

Amounts are soluble-product equivalents, not polymer chains or elemental
carbon. Ownership, simultaneous allocation and detach/death transfers belong
to the surrounding conservative runtime.
"""
import numpy as np

from .pts import _align, _product, _value


def _remaining_fraction(remaining, initial):
    remaining, initial = _align(_value("remaining_molecules", remaining),
                                _value("initial_molecules", initial))
    if np.any(remaining > initial):
        raise ValueError("remaining stock cannot exceed initial stock")
    return np.divide(remaining, initial, out=np.zeros_like(remaining), where=initial > 0)


def direct_hydrolysis_rate(enzyme_copies, remaining_molecules, initial_molecules, *, turnover_s):
    fraction = _remaining_fraction(remaining_molecules, initial_molecules)
    enzyme, fraction = _align(_value("enzyme_copies", enzyme_copies), fraction)
    rate = _value("turnover_s", turnover_s, scalar=True)
    return _product("direct hydrolysis rate", enzyme, fraction, rate)

