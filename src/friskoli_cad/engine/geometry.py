"""Read-only geometry module; re-evaluated after motion, growth and division."""
from __future__ import annotations

import numpy as np

from .runtime import ModuleResult, SimulationError


class CapsuleReadout:
    world_access = "read_only"

    def __init__(self, manifest, declaration):
        self.manifest = manifest
        self.declaration = declaration

    def initialize(self, world, node, inputs):
        group = world.groups[node.owner_id]
        for index, capsule in enumerate(group.geometry):
            if capsule is None:
                pointer = node.owner_id.replace("~", "~0").replace("/", "~1")
                raise SimulationError("geometry.missing", f"{node.id}: cell {group.ids[index]} needs capsule geometry",
                                      f"/groups/{pointer}/initial_geometry/{index}")
        length = np.asarray([g.length_um for g in group.geometry], dtype=np.float64)
        diameter = np.asarray([g.diameter_um for g in group.geometry], dtype=np.float64)
        return ModuleResult({
            "position": group.positions_um.copy(), "length": length, "diameter": diameter,
            "surface_area": np.pi * diameter * length,
            "volume": np.pi * diameter**2 * (length - diameter / 3) / 4,
        }, {})

    def advance(self, world, node, inputs, previous_state, previous_outputs, dt_s):
        return self.initialize(world, node, inputs)

    def refresh(self, world, node, inputs, state, previous_outputs):
        return self.initialize(world, node, inputs)
