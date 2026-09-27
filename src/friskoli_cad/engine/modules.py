"""Small generic modules for the first executable concentration example."""

from __future__ import annotations

import json
import math
from importlib.resources import files
from typing import Mapping

import numpy as np

from .compiler import CompiledNode
from .runtime import ModuleRegistry, ModuleResult, SimulationError, World
from .spatial import box_overlap_weights


def _manifest(name: str) -> dict:
    resource = files("friskoli_cad.engine").joinpath("manifests", f"{name}.json")
    return json.loads(resource.read_text(encoding="utf-8"))


class LocalInventory:
    manifest = _manifest("field.local_inventory")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        level = node.parameters["initial_concentration"].value
        concentration = np.full(world.grid.shape, level, dtype=np.float64)
        return ModuleResult({"concentration": concentration}, {"concentration": concentration})

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        rate = inputs["consumption_rate"]
        if np.any(rate < 0):
            raise SimulationError("field.rate", "consumption rate cannot be negative")
        concentration = previous_state["concentration"] - rate * dt_s
        if np.any(concentration < 0):
            raise SimulationError("field.depleted", "requested uptake exceeds local inventory; reduce dt_s")
        return ModuleResult({"concentration": concentration}, {"concentration": concentration})


class IdealReservoir:
    manifest = _manifest("field.ideal_reservoir")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        concentration = np.full(
            world.grid.shape, node.parameters["initial_concentration"].value, dtype=np.float64
        )
        zeros = np.zeros(world.grid.shape, dtype=np.float64)
        return ModuleResult(
            {"concentration": concentration, "supply_flux": zeros, "cumulative_supply": zeros},
            {"cumulative_supply": zeros},
        )

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        rate = inputs["consumption_rate"]
        if np.any(rate < 0):
            raise SimulationError("field.rate", "consumption rate cannot be negative")
        supply_flux = rate * world.grid.molecules_per_uM_voxel
        cumulative_supply = previous_state["cumulative_supply"] + supply_flux * dt_s
        return ModuleResult(
            {
                "concentration": previous_outputs["concentration"],
                "supply_flux": supply_flux,
                "cumulative_supply": cumulative_supply,
            },
            {"cumulative_supply": cumulative_supply},
        )


class SampleNearest:
    manifest = _manifest("field.sample_nearest")

    def _sample(self, world: World, node: CompiledNode, field: np.ndarray) -> ModuleResult:
        group = world.groups[node.owner_id]
        indices = world.grid.flat_indices(group.positions_um)
        values = field.ravel()[indices]
        return ModuleResult({"local_concentration": values}, {})

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._sample(world, node, inputs["concentration"])

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        return self._sample(world, node, inputs["concentration"])


def _box_support(node: CompiledNode) -> tuple[float, float, float]:
    support = tuple(node.parameters[f"support_{axis}_um"].value for axis in "xyz")
    if not all(math.isfinite(value) and value > 0 for value in support):
        raise SimulationError("spatial.support", "support dimensions must be finite and positive")
    return support


class SampleBoxSupport:
    manifest = _manifest("field.sample_box_support")

    def _sample(self, world: World, node: CompiledNode, field: np.ndarray) -> ModuleResult:
        group = world.groups[node.owner_id]
        flattened = field.ravel()
        values = np.empty(len(group.ids), dtype=np.float64)
        support = _box_support(node)
        for index, position in enumerate(group.positions_um):
            voxels, weights = box_overlap_weights(world.grid, position, support)
            values[index] = np.dot(flattened[voxels], weights)
        return ModuleResult({"local_concentration": values}, {})

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._sample(world, node, inputs["concentration"])

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        return self._sample(world, node, inputs["concentration"])


class LinearUptake:
    manifest = _manifest("uptake.linear")

    def _flux(self, node: CompiledNode, concentration: np.ndarray) -> np.ndarray:
        return node.parameters["rate_constant"].value * concentration

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        flux = self._flux(node, inputs["local_concentration"])
        cumulative = np.zeros_like(flux)
        return ModuleResult(
            {"uptake_flux": flux, "cumulative_uptake": cumulative},
            {"cumulative_uptake": cumulative},
        )

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        flux = self._flux(node, inputs["local_concentration"])
        cumulative = previous_state["cumulative_uptake"] + previous_outputs["uptake_flux"] * dt_s
        return ModuleResult(
            {"uptake_flux": flux, "cumulative_uptake": cumulative},
            {"cumulative_uptake": cumulative},
        )


class DepositNearest:
    manifest = _manifest("field.deposit_nearest")

    def _deposit(self, world: World, node: CompiledNode, flux: np.ndarray) -> ModuleResult:
        if np.any(flux < 0):
            raise SimulationError("cell.flux", "uptake flux cannot be negative")
        group = world.groups[node.owner_id]
        indices = world.grid.flat_indices(group.positions_um)
        molecules_s = np.bincount(indices, weights=flux, minlength=world.grid.voxel_count)
        rate = molecules_s.reshape(world.grid.shape) / world.grid.molecules_per_uM_voxel
        return ModuleResult({"consumption_rate": rate}, {})

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._deposit(world, node, inputs["uptake_flux"])

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        return self._deposit(world, node, inputs["uptake_flux"])


class DepositBoxSupport:
    manifest = _manifest("field.deposit_box_support")

    def _deposit(self, world: World, node: CompiledNode, flux: np.ndarray) -> ModuleResult:
        if np.any(flux < 0):
            raise SimulationError("cell.flux", "uptake flux cannot be negative")
        group = world.groups[node.owner_id]
        molecules_s = np.zeros(world.grid.voxel_count, dtype=np.float64)
        support = _box_support(node)
        for position, cell_flux in zip(group.positions_um, flux):
            voxels, weights = box_overlap_weights(world.grid, position, support)
            np.add.at(molecules_s, voxels, cell_flux * weights)
        rate = molecules_s.reshape(world.grid.shape) / world.grid.molecules_per_uM_voxel
        return ModuleResult({"consumption_rate": rate}, {})

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._deposit(world, node, inputs["uptake_flux"])

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        return self._deposit(world, node, inputs["uptake_flux"])


def default_registry() -> ModuleRegistry:
    return ModuleRegistry([
        LocalInventory(), IdealReservoir(), SampleNearest(), SampleBoxSupport(), LinearUptake(),
        DepositNearest(), DepositBoxSupport(),
    ])
