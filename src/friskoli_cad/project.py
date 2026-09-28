"""Versioned, self-contained project snapshots and scheduled external inputs."""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Mapping

import numpy as np
from jsonschema import Draft202012Validator

from friskoli_cad.protocol import ProtocolError, validate_graph, validate_run_metadata


@dataclass(frozen=True, slots=True)
class ControlSchedule:
    species: str
    changes: tuple[tuple[float, float], ...]
    repeat_period_s: float | None = None

    def __post_init__(self) -> None:
        changes = tuple((float(time), float(rate)) for time, rate in self.changes)
        times = tuple(time for time, _ in changes)
        if (
            not self.species or not changes or times[0] != 0
            or any(not math.isfinite(time) or not math.isfinite(rate) for time, rate in changes)
            or any(later <= earlier for earlier, later in zip(times, times[1:]))
            or (
                self.repeat_period_s is not None
                and (
                    not math.isfinite(self.repeat_period_s)
                    or self.repeat_period_s <= times[-1]
                )
            )
        ):
            raise ValueError("schedule needs finite, ordered changes starting at zero")
        object.__setattr__(self, "changes", changes)

    def rate_and_next_change(self, time_s: float) -> tuple[float, float]:
        """Return the rate active at a time boundary and the next boundary."""
        times = tuple(change[0] for change in self.changes)
        tolerance = 8 * max(
            math.ulp(time_s), math.ulp(times[-1]),
            math.ulp(self.repeat_period_s) if self.repeat_period_s is not None else 0,
        )
        if self.repeat_period_s is None:
            index = bisect_right(times, time_s + tolerance) - 1
            next_change = times[index + 1] if index + 1 < len(times) else math.inf
            return self.changes[index][1], next_change
        period = self.repeat_period_s
        phase = time_s % period
        if math.isclose(phase, period, rel_tol=0, abs_tol=tolerance):
            phase = 0.0
        index = bisect_right(times, phase + tolerance) - 1
        cycle_start = time_s - phase
        if index + 1 < len(times):
            next_change = cycle_start + times[index + 1]
        else:
            next_change = cycle_start + period
        return self.changes[index][1], next_change


@lru_cache(maxsize=2)
def _project_validator(version: str) -> Draft202012Validator:
    schema_file = {"0.1.0": "project.schema.json", "0.2.0": "project-v0.2.schema.json"}.get(version)
    if schema_file is None:
        _fail("project.version", "/project_version", f"unsupported project version {version}")
    schema = json.loads(
        files("friskoli_cad.protocol").joinpath("schemas", schema_file)
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _fail(code: str, path: str, message: str) -> None:
    raise ProtocolError(code, path, message)


def validate_project(document: Mapping[str, object], manifests: tuple[Mapping[str, object], ...]) -> None:
    """Check one complete project against its exact graph module versions."""
    try:
        json.dumps(document, allow_nan=False)
    except (TypeError, ValueError):
        _fail("project.number", "/", "project must contain finite JSON values")
    version = document.get("project_version") if isinstance(document, Mapping) else None
    error = next(_project_validator(version).iter_errors(document), None)
    if error is not None:
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        _fail("project.schema", path, error.message)
    graph, run = document["graph"], document["run"]
    validate_graph(graph, manifests)
    validate_run_metadata(run, graph, manifests)
    manifest_by_key = {(item["id"], item["version"]): item for item in manifests}
    providers: dict[str, int] = {}
    control_uses: dict[str, int] = {}
    for node in graph["nodes"]:
        manifest = manifest_by_key[(node["module_id"], node["module_version"])]
        if "species" in manifest["parameters"]:
            species = node["parameters"]["species"]["value"]
            if species not in document["species"]:
                _fail("project.species", "/graph", f"undeclared species {species}")
        for port in manifest["outputs"].values():
            if port["shape"] != "field.scalar" or port["quantity"] != "concentration":
                continue
            binding = port.get("species_parameter")
            if binding is None:
                _fail("project.species", "/graph", "concentration output lacks a species binding")
            species = node["parameters"][binding]["value"]
            if species not in document["species"] or port["unit"] != "uM":
                _fail("project.species", "/graph", f"unknown or incompatible concentration for {species}")
            providers[species] = providers.get(species, 0) + 1
            initial = node["parameters"].get("initial_concentration")
            if initial is not None and initial["value"] != (
                document["species"][species]["initial_concentration"]["value"]
            ):
                _fail("project.initial", "/graph", f"initial concentration differs for {species}")
        if "schedule_id" in manifest["parameters"]:
            schedule_id = node["parameters"]["schedule_id"]["value"]
            schedule = document["controls"].get(schedule_id)
            if schedule is None:
                _fail("project.control", "/graph", f"unknown control {schedule_id}")
            if node["parameters"]["species"]["value"] != schedule["species"]:
                _fail("project.control", "/graph", f"species differs for control {schedule_id}")
            control_uses[schedule_id] = control_uses.get(schedule_id, 0) + 1
    if any(count != 1 for count in providers.values()):
        _fail("project.species", "/graph", "an active species needs exactly one concentration field")
    if set(control_uses) != set(document["controls"]) or any(count != 1 for count in control_uses.values()):
        _fail("project.control", "/controls", "each control needs exactly one source node")
    for schedule_id, schedule in document["controls"].items():
        if schedule["species"] not in document["species"]:
            _fail("project.control", f"/controls/{schedule_id}", "control species is undeclared")
        times = [change["time_s"] for change in schedule["changes"]]
        if times[0] != 0 or any(later <= earlier for earlier, later in zip(times, times[1:])):
            _fail("project.control_time", f"/controls/{schedule_id}", "times must start at 0 and increase")
        period = schedule.get("repeat_period_s")
        if period is not None and times[-1] >= period:
            _fail("project.control_time", f"/controls/{schedule_id}", "changes must precede the repeat boundary")


def simulation_from_project(document: Mapping[str, object], registry=None):
    """Build a simulation from a complete snapshot; old graph-only callers remain valid."""
    from friskoli_cad.engine import CapsuleGeometry, CellGroup, GridDomain, Simulation, World, default_registry

    registry = default_registry() if registry is None else registry
    validate_project(document, registry.manifests)
    domain = document["domain"]
    nx, ny, nz = domain["counts_xyz"]
    dx, dy, dz = domain["spacing_um_xyz"]
    grid = GridDomain(domain["geometry"], nx, ny, nz, dx, dy, dz)
    groups = {
        group_id: CellGroup(
            group_id, tuple(group["ids"]),
            np.array(group["positions_um"], dtype=np.float64).reshape((-1, 3)),
            np.array(group["orientation_xyzw"], dtype=np.float64).reshape((-1, 4)),
            tuple(
                None if entry is None else CapsuleGeometry(entry["length_um"], entry["diameter_um"])
                for entry in group["initial_geometry"]
            ) if "initial_geometry" in group else None,
        )
        for group_id, group in document["groups"].items()
    }
    active_inventory_species = {
        node["parameters"]["species"]["value"]
        for node in document["graph"]["nodes"]
        if (node["module_id"], node["module_version"]) == ("field.local_inventory", "3.0.0")
    }
    initial = {
        species: document["species"][species]["initial_concentration"]["value"]
        for species in active_inventory_species
    }
    controls = {
        schedule_id: ControlSchedule(
            entry["species"],
            tuple((change["time_s"], change["rate_uM_s"]) for change in entry["changes"]),
            entry.get("repeat_period_s"),
        )
        for schedule_id, entry in document["controls"].items()
    }
    world = World(grid, groups, initial, controls)
    return Simulation(world, document["graph"], document["run"], registry)
