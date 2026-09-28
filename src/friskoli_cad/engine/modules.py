"""Small generic modules for the first executable concentration example."""

from __future__ import annotations

import json
import math
from importlib.resources import files
from typing import Mapping

import numpy as np

from friskoli_cad.project import ControlSchedule

from .compiler import CompiledNode
from .diffusion import explicit_no_flux_limit, no_flux_diffusion_rate
from .motion import heading_from_orientation, reflect_in_box, turn_about_z
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


class LocalInventoryWithDiffusion(LocalInventory):
    """Version 2 combines declared diffusion and consumption rates."""

    manifest = _manifest("field.local_inventory.v2")

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        consumption = inputs["consumption_rate"]
        if np.any(consumption < 0):
            raise SimulationError("field.rate", "consumption rate cannot be negative")
        concentration = previous_state["concentration"] + (
            inputs["diffusion_rate"] - consumption
        ) * dt_s
        if np.any(concentration < 0):
            raise SimulationError("field.depleted", "net loss exceeds local inventory; reduce dt_s")
        return ModuleResult({"concentration": concentration}, {"concentration": concentration})


class ScheduledLocalInventory:
    manifest = _manifest("field.local_inventory.v3")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        species = node.parameters["species"].value
        if species not in world.species_initial_uM:
            raise SimulationError("project.species", f"initial concentration missing for {species}")
        concentration = np.full(world.grid.shape, world.species_initial_uM[species], dtype=np.float64)
        zeros = np.zeros(world.grid.shape, dtype=np.float64)
        return ModuleResult(
            {"concentration": concentration, "external_flux": zeros, "cumulative_external": zeros},
            {"concentration": concentration, "cumulative_external": zeros},
        )

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        consumption = inputs.get("consumption_rate", 0)
        if np.any(consumption < 0):
            raise SimulationError("field.rate", "consumption rate cannot be negative")
        external = inputs["external_rate"]
        concentration = previous_state["concentration"] + dt_s * (
            inputs.get("diffusion_rate", 0) - consumption + external
        )
        if np.any(concentration < 0):
            raise SimulationError("field.depleted", "net loss exceeds local inventory; reduce dt_s")
        external_flux = external * world.grid.molecules_per_uM_voxel
        cumulative = previous_state["cumulative_external"] + external_flux * dt_s
        return ModuleResult(
            {"concentration": concentration, "external_flux": external_flux,
             "cumulative_external": cumulative},
            {"concentration": concentration, "cumulative_external": cumulative},
        )


class ScheduledUniformRate:
    manifest = _manifest("source.scheduled_uniform_rate")

    def _schedule(self, world: World, node: CompiledNode) -> ControlSchedule:
        schedule = world.schedules.get(node.parameters["schedule_id"].value)
        if not isinstance(schedule, ControlSchedule) or schedule.species != node.parameters["species"].value:
            raise SimulationError("schedule.missing", "matching project control is required")
        return schedule

    def _result(self, world: World, rate: float, time_s: float) -> ModuleResult:
        field = np.full(world.grid.shape, rate, dtype=np.float64)
        return ModuleResult({"external_rate": field}, {"time_s": np.array(time_s)})

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        self._schedule(world, node)
        return self._result(world, 0, 0)

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        start = float(previous_state["time_s"])
        end = start + dt_s
        if not math.isfinite(end):
            raise SimulationError("time.overflow", "schedule time must remain finite")
        rate, next_change = self._schedule(world, node).rate_and_next_change(start)
        if next_change < end - 8 * math.ulp(end):
            raise SimulationError(
                "schedule.boundary", f"step crosses an input change at {next_change:g} s"
            )
        return self._result(world, rate, end)


class LinearElongation:
    manifest = _manifest("growth.linear_elongation")

    def _current(self, world: World, node: CompiledNode) -> tuple[np.ndarray, np.ndarray]:
        geometry = world.groups[node.owner_id].geometry
        if any(capsule is None for capsule in geometry):
            raise SimulationError("growth.geometry", "every cell needs a declared capsule size")
        return (
            np.array([capsule.length_um for capsule in geometry], dtype=np.float64),
            np.array([capsule.diameter_um for capsule in geometry], dtype=np.float64),
        )

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        length, diameter = self._current(world, node)
        return ModuleResult({"length": length, "diameter": diameter}, {})

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        length, diameter = self._current(world, node)
        length += node.parameters["elongation_rate"].value * dt_s
        return ModuleResult({"length": length, "diameter": diameter}, {})


class DiffusionNoFlux:
    manifest = _manifest("field.diffusion_no_flux")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        explicit_no_flux_limit(world.grid, node.parameters["diffusivity"].value)
        return ModuleResult({"diffusion_rate": np.zeros(world.grid.shape, dtype=np.float64)}, {})

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        coefficient = node.parameters["diffusivity"].value
        limit = explicit_no_flux_limit(world.grid, coefficient)
        if dt_s > limit * (1 + 1e-12):
            raise SimulationError("diffusion.stability", f"dt_s exceeds the {limit:g} s explicit limit")
        return ModuleResult({
            "diffusion_rate": no_flux_diffusion_rate(inputs["concentration"], world.grid, coefficient)
        }, {})


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

    def _sample(
        self, world: World, node: CompiledNode, field: np.ndarray, positions: np.ndarray
    ) -> ModuleResult:
        flattened = field.ravel()
        values = np.empty(len(positions), dtype=np.float64)
        support = _box_support(node)
        for index, position in enumerate(positions):
            voxels, weights = box_overlap_weights(world.grid, position, support)
            values[index] = np.dot(flattened[voxels], weights)
        return ModuleResult({"local_concentration": values}, {})

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._sample(
            world, node, inputs["concentration"], world.groups[node.owner_id].positions_um
        )

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        return self.initialize(world, node, inputs)


class SampleBoxSupportAtPosition(SampleBoxSupport):
    manifest = _manifest("field.sample_box_support.v2")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._sample(world, node, inputs["concentration"], inputs["position"])


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

    def _deposit(
        self, world: World, node: CompiledNode, flux: np.ndarray, positions: np.ndarray
    ) -> ModuleResult:
        if np.any(flux < 0):
            raise SimulationError("cell.flux", "uptake flux cannot be negative")
        molecules_s = np.zeros(world.grid.voxel_count, dtype=np.float64)
        support = _box_support(node)
        for position, cell_flux in zip(positions, flux):
            voxels, weights = box_overlap_weights(world.grid, position, support)
            np.add.at(molecules_s, voxels, cell_flux * weights)
        rate = molecules_s.reshape(world.grid.shape) / world.grid.molecules_per_uM_voxel
        return ModuleResult({"consumption_rate": rate}, {})

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._deposit(
            world, node, inputs["uptake_flux"], world.groups[node.owner_id].positions_um
        )

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        return self.initialize(world, node, inputs)


class DepositBoxSupportAtPosition(DepositBoxSupport):
    manifest = _manifest("field.deposit_box_support.v2")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        return self._deposit(world, node, inputs["uptake_flux"], inputs["position"])


class PeriodicTurn:
    manifest = _manifest("motion.periodic_turn")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        if node.parameters["turn_interval_s"].value <= 0:
            raise SimulationError("motion.turn_interval", "turn interval must be positive")
        zeros = np.zeros(len(world.groups[node.owner_id].ids), dtype=np.float64)
        return ModuleResult({"turn_angle": zeros}, {"elapsed_s": zeros})

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        interval = node.parameters["turn_interval_s"].value
        if dt_s > interval:
            raise SimulationError("motion.turn_step", "dt_s exceeds the turn interval")
        elapsed = previous_state["elapsed_s"] + dt_s
        crossed = elapsed >= interval - 1e-12 * interval
        elapsed = np.where(crossed, np.maximum(0, elapsed - interval), elapsed)
        angle = np.where(crossed, node.parameters["turn_angle_rad"].value, 0.0)
        return ModuleResult({"turn_angle": angle}, {"elapsed_s": elapsed})


class ReflectiveRun:
    manifest = _manifest("motion.reflective_run")

    def initialize(self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray]) -> ModuleResult:
        group = world.groups[node.owner_id]
        positions = group.positions_um
        heading = heading_from_orientation(group.orientation_xyzw)
        if world.grid.geometry == "thin_layer" and np.any(np.abs(heading[:, 2]) > 1e-9):
            raise SimulationError("motion.plane", "thin-layer headings must lie in XY")
        return ModuleResult(
            {"position": positions, "heading": heading},
            {"position": positions, "heading": heading},
        )

    def advance(
        self, world: World, node: CompiledNode, inputs: Mapping[str, np.ndarray],
        previous_state: Mapping[str, np.ndarray], previous_outputs: Mapping[str, np.ndarray],
        dt_s: float,
    ) -> ModuleResult:
        distance = node.parameters["speed_um_s"].value * dt_s
        if not math.isfinite(distance):
            raise SimulationError("motion.distance", "travel distance is not finite")
        heading = turn_about_z(previous_state["heading"], inputs["turn_angle"])
        positions, heading = reflect_in_box(
            previous_state["position"], heading, distance, world.grid.extent_um,
            thin_layer=world.grid.geometry == "thin_layer",
        )
        return ModuleResult(
            {"position": positions, "heading": heading},
            {"position": positions, "heading": heading},
        )


def default_registry() -> ModuleRegistry:
    return ModuleRegistry([
        LocalInventory(), LocalInventoryWithDiffusion(), ScheduledLocalInventory(),
        ScheduledUniformRate(), LinearElongation(), DiffusionNoFlux(), IdealReservoir(),
        SampleNearest(), SampleBoxSupport(), SampleBoxSupportAtPosition(), LinearUptake(),
        DepositNearest(), DepositBoxSupport(), DepositBoxSupportAtPosition(),
        PeriodicTurn(), ReflectiveRun(),
    ])
