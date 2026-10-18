"""Versioned, self-contained project snapshots."""

from __future__ import annotations

import json
import math
from functools import lru_cache
from importlib.resources import files
from typing import Mapping

import numpy as np
from jsonschema import Draft202012Validator

from friskoli_cad.protocol import ProtocolError, validate_graph, validate_run_metadata


@lru_cache(maxsize=1)
def _project_validator(version: str) -> Draft202012Validator:
    if version != "0.6.0":
        _fail("project.version", "/project_version", "Only Project 0.6.0 is supported; historical projects are not migrated.")
    schema = json.loads(files("friskoli_cad.protocol").joinpath("schemas", "project-v0.6.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _fail(code: str, path: str, message: str) -> None:
    raise ProtocolError(code, path, message)


def validate_project(document: Mapping[str, object], manifests: tuple[Mapping[str, object], ...], *, registry=None) -> None:
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
    if version == "0.6.0":
        from friskoli_cad.engine.limits import MAX_CELLS, MAX_VOXELS, MAX_SPECIES
        cells = sum(len(group["ids"]) for group in document["groups"].values())
        voxels = math.prod(document["domain"]["counts_xyz"])
        if cells > MAX_CELLS or voxels > MAX_VOXELS or len(document["species"]) > MAX_SPECIES:
            _fail("spatial.resource_limit", "/", f"spatial profile supports at most {MAX_CELLS} cells, {MAX_VOXELS} voxels and {MAX_SPECIES} species; task admission applies a separate memory budget")
    graph, run = document["graph"], document["run"]
    validate_graph(graph, manifests)
    validate_run_metadata(run, graph, manifests)
    from friskoli_cad.engine.modular_runtime import validate_modular_project
    validate_modular_project(document, manifests, registry=registry)
    manifest_by_key = {(item["id"], item["version"]): item for item in manifests}
    providers: dict[str, int] = {}
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
    if any(count != 1 for count in providers.values()):
        _fail("project.species", "/graph", "an active species needs exactly one concentration field")


def simulation_from_project(document: Mapping[str, object], registry=None, *, seed=None, field_backend='numpy-cpu'):
    """Build the sole supported simulation from a self-contained Project 0.6."""
    from friskoli_cad.engine.core import CapsuleGeometry, CellGroup, GridDomain, SimulationError, World

    from friskoli_cad.engine.profiles import registry_for_project
    registry = registry_for_project(document) if registry is None else registry
    validate_project(document, registry.manifests, registry=registry)
    domain = document["domain"]
    nx, ny, nz = domain["counts_xyz"]
    dx, dy, dz = domain["spacing_um_xyz"]
    grid = GridDomain(domain["geometry"], nx, ny, nz, dx, dy, dz)
    def capsule(group_id, group, index, entry):
        if entry is None:
            return None
        try:
            return CapsuleGeometry(entry["length_um"], entry["diameter_um"])
        except SimulationError as error:
            pointer = group_id.replace("~", "~0").replace("/", "~1")
            cell_id = group['ids'][index] if index < len(group['ids']) else f"unmatched geometry entry {index}"
            raise SimulationError(error.code, f"group {group_id}, cell {cell_id}: {error}",
                                  f"/groups/{pointer}/initial_geometry/{index}") from error

    groups = {
        group_id: CellGroup(
            group_id, tuple(group["ids"]),
            np.array(group["positions_um"], dtype=np.float64).reshape((-1, 3)),
            np.array(group["orientation_xyzw"], dtype=np.float64).reshape((-1, 4)),
            tuple(
                capsule(group_id, group, index, entry)
                for index, entry in enumerate(group["initial_geometry"])
            ) if "initial_geometry" in group else None,
        )
        for group_id, group in document["groups"].items()
    }
    initial = {species: entry["initial_concentration"]["value"] for species, entry in document["species"].items()}
    world = World(grid, groups, initial)
    from friskoli_cad.engine.modular_runtime import ModularSimulation
    return ModularSimulation(world, document, registry, seed=seed, field_backend=field_backend)
