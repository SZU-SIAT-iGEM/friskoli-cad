"""Inventory-limited growth, total-copy expression and explicit life proposals.

These are exploratory source models. Health is an empirical state, not a
measured viability fraction. This module neither samples deaths nor owns cells.
"""
from dataclasses import dataclass

import numpy as np

from .pts import _align, _product, _value

def capsule_volume_um3(total_length_um, diameter_um):
    length, diameter = _align(_value("total_length_um", total_length_um, positive=True),
                              _value("diameter_um", diameter_um, positive=True))
    if np.any(length < diameter):
        raise ValueError("total length must include the end caps")
    return _product("capsule volume", np.pi / 4, diameter, diameter, length - diameter / 3)


@dataclass(frozen=True)
class GeometryReadout:
    total_length_um: np.ndarray
    area_um2: np.ndarray


def capsule_geometry_from_volume(volume_um3, radius_um):
    volume, radius = _align(_value("volume_um3", volume_um3, positive=True),
                            _value("radius_um", radius_um, positive=True))
    minimum = _product("minimum sphere volume", 4 * np.pi / 3, radius, radius, radius)
    if np.any(volume < minimum):
        raise ValueError("volume is smaller than the fixed-radius sphere")
    length = _value("total length", (volume - minimum) / (np.pi * radius**2) + 2 * radius)
    return GeometryReadout(length, _product("capsule area", 2 * np.pi, radius, length))


@dataclass(frozen=True)
class GrowthReadout:
    intracellular_molecules: np.ndarray
    volume_um3: np.ndarray
    used_molecules: np.ndarray
    actual_growth_per_min: np.ndarray


