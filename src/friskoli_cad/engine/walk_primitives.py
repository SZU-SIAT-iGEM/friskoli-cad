"""Shared random-motion primitives used by the active hazard walk."""
from __future__ import annotations

import math


class RandomWalkBudgetError(ValueError):
    """The candidate must be discarded, including its candidate RNG state."""


def isotropic_heading(stream, dimensions):
    """Draw an isotropic unit heading in two or three dimensions."""
    if dimensions not in (2, 3) or isinstance(dimensions, bool):
        raise ValueError("dimensions must be 2 or 3")
    phi = 2 * math.pi * stream.uniform_open()
    if dimensions == 2:
        return math.cos(phi), math.sin(phi), 0.
    z = 2 * stream.uniform_open() - 1
    radius = math.sqrt(max(0., 1 - z * z))
    return radius * math.cos(phi), radius * math.sin(phi), z
