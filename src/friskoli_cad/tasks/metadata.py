"""Allocation-free admission estimates and source-backed execution metadata."""
from __future__ import annotations

import hashlib
import platform
from importlib.resources import files

import numpy as np

from friskoli_cad.engine.compiler import compile_graph
from friskoli_cad.engine.field_backend import backend_environment
from .artifacts import final_field_estimate, concentration_species
from friskoli_cad.engine.profiles import LEGACY_PROFILE, PTS_PROFILE, SPATIAL_PROFILE, CHEMOTAXIS_PROFILE, profile_for_project, registry_for_profile, pts_schedule, spatial_schedule, chemotaxis_schedule, task_version
from friskoli_cad.protocol.task_validation import sha256

SEMANTICS = LEGACY_PROFILE
BACKEND = "numpy-cpu"


def source_hashes() -> dict:
    root = files("friskoli_cad")
    paths = [("project.py", root.joinpath("project.py"))]
    for folder in ("engine", "protocol", "science"):
        paths.extend((folder + "/" + entry.name, entry)
                     for entry in root.joinpath(folder).iterdir()
                     if entry.name.endswith(".py") and entry.name != "task_validation.py")
    paths.extend(("science/data/" + entry.name, entry)
                 for entry in root.joinpath("science", "data").iterdir()
                 if entry.name.endswith(".json"))
    paths.append(("tasks/worker.py", root.joinpath("tasks", "worker.py")))
    paths.append(("tasks/artifacts.py", root.joinpath("tasks", "artifacts.py")))
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in sorted(paths)}


def registry_metadata(profile=LEGACY_PROFILE) -> tuple:
    registry = registry_for_profile(profile)
    registry_document = {"manifests": list(registry.manifests), "catalog": registry.catalog}
    sources = source_hashes()
    implementations = [{"id": item["id"], "version": item["version"],
        "sha256": sha256({"id": item["id"], "version": item["version"], "sources": sources})}
        for item in registry.manifests]
    implementations.sort(key=lambda item: (item["id"], item["version"]))
    return registry, {"registry_sha256": sha256(registry_document),
                      "implementations": implementations}, sources


def compiled_plan(project, registry) -> dict:
    plan = compile_graph(project["graph"], registry.manifests)
    return {"plan_version": task_version(profile_for_project(project)), "graph_id": plan.id,
        **({"schedule": chemotaxis_schedule(plan, registry)} if profile_for_project(project) == CHEMOTAXIS_PROFILE else
           {"schedule": spatial_schedule(plan, registry)} if profile_for_project(project) == SPATIAL_PROFILE else
           {"schedule": pts_schedule(plan)} if profile_for_project(project) == PTS_PROFILE else {}),
        "execution_semantics": profile_for_project(project), "nodes": [{
            "id": node.id, "module_id": node.module_id, "module_version": node.module_version,
            "owner_kind": node.owner_kind, "owner_id": node.owner_id, "phase": node.phase,
            "parameters": {name: {"value": item.value, "unit": item.unit,
                "provenance_kind": item.provenance_kind,
                "provenance_reference": item.provenance_reference}
                for name, item in node.parameters.items()},
            "inputs": {name: {"source_node": item.source_node,
                "source_port": item.source_port, "timing": item.timing}
                for name, item in node.inputs.items()},
            "outputs": list(node.outputs), "initial_outputs": list(node.initial_outputs),
        } for node in plan.nodes]}


def provenance(seed: int, sources: dict, project=None, backend=BACKEND) -> dict:
    spatial = project is not None and profile_for_project(project) in (SPATIAL_PROFILE, CHEMOTAXIS_PROFILE)
    used = spatial and any(project['groups'][node['owner']['id']]['ids'] and (
        node['module_id'] == 'motion.unbiased_run_tumble' and node['parameters']['tumble_rate_s']['value'] > 0 or
        node['module_id'] == 'motion.hazard_run_tumble' and node['parameters']['maximum_tumble_rate_s']['value'] > 0 or
        node['module_id'] in ('life.health_balance', 'life.starvation_hazard', 'division.area_adder'))
        for node in project['graph']['nodes'] if node['owner']['kind'] == 'population')
    return {"provenance_version": "0.2.0" if spatial else "0.1.0", "backend": backend, "precision": "float64",
        "python_version": platform.python_version(), "numpy_version": np.__version__,
        **({"backend_environment": backend_environment(backend)} if backend == "numpy-cupy-cuda" else {}),
        "platform": platform.platform(), "source_sha256": sources,
        "rng": {"algorithm": "pcg64-sha256-key-v1" if spatial else "none-deterministic", "seed": seed, "used": bool(used)}}


def estimate(submission, registry) -> dict:
    """Conservative array/copy and serialized-frame budgets; no NumPy allocation."""
    project = submission["project"]
    cells = sum(len(group["ids"]) for group in project["groups"].values())
    budget_cells = 256 if profile_for_project(project) == CHEMOTAXIS_PROFILE and any(
        n['module_id'] == 'division.area_adder' for n in project['graph']['nodes']) else cells
    nx, ny, nz = project["domain"]["counts_xyz"]
    voxels = nx * ny * nz
    field_arrays = cell_arrays = 0
    manifests = {(item["id"], item["version"]): item for item in registry.manifests}
    for node in project["graph"]["nodes"]:
        manifest = manifests[(node["module_id"], node["module_version"])]
        for port in manifest["outputs"].values():
            field_arrays += int(port["shape"] == "field.scalar")
            cell_arrays += 3 if port["shape"] == "cell.vector" else int(port["shape"] == "cell.scalar")
    steps = submission["execution"]["steps"]
    every = submission["output_plan"]["frame_every_steps"]
    frames = 1 + steps // every + int(steps % every != 0)
    # Includes Python/NumPy startup allowance, temporaries, previous/current arrays and JSON.
    # The PTS profile owns one well-mixed bulk scalar, not a voxel inventory.
    # It still retains the domain counts for geometry validation and limits.
    field_elements = 1 if profile_for_project(project) == PTS_PROFILE else voxels
    memory = 64 * 1024 * 1024 + field_elements * max(1, field_arrays) * 8 * 16
    if submission.get("task_contract_version") == "0.5.0" and profile_for_project(project) in (SPATIAL_PROFILE, CHEMOTAXIS_PROFILE):
        # The local-field modules own one array per species. Snapshot/graph ports
        # alias immutable arrays; budget eight float64 buffers per owner plus
        # masks/geometry scratch, rather than multiplying every alias by 16.
        local_species = {node["parameters"]["species"]["value"] for node in project["graph"]["nodes"]
                         if node["module_id"] in ("field.diffusive_local", "field.ideal_local_reservoir")}
        local_outputs = sum(sum(port["shape"] == "field.scalar" for port in manifests[(node["module_id"], node["module_version"])]["outputs"].values())
                            for node in project["graph"]["nodes"]
                            if node["module_id"] in ("field.diffusive_local", "field.ideal_local_reservoir"))
        owners = max(1, field_arrays - local_outputs + len(local_species))
        memory = 64 * 1024 * 1024 + voxels * (owners * 8 * 8 + 4)
    memory += budget_cells * (4096 + cell_arrays * 8 * 16)
    # UTF-8 names and channel IDs are included, rather than assuming ASCII names.
    channel_bytes = sum(len(name.encode("utf-8")) + 32
                        for name in submission["output_plan"]["observables"])
    longest_id = max((len(identifier.encode("utf-8")) for group in project["groups"].values()
                      for identifier in group["ids"]), default=0)
    field_bytes = 0
    if submission["output_plan"]["include_fields"]:
        species = {node["parameters"]["species"]["value"] for node in project["graph"]["nodes"]
                   if node["module_id"] in ("field.diffusive_local", "field.ideal_local_reservoir")}
        if submission.get("task_contract_version") == "0.5.0":
            species = concentration_species(project)
        stride = submission["output_plan"].get("field_stride_xyz", [1, 1, 1])
        preview_voxels = (nx // stride[0]) * (ny // stride[1]) * (nz // stride[2])
        field_overhead = 384 if submission.get("task_contract_version") == "0.5.0" else 128
        field_bytes = sum(field_overhead + len(name.encode("utf-8")) + preview_voxels * 32 for name in species)
    object_bytes = sum(192 + len(node["id"].encode("utf-8")) * 6 for node in project["graph"]["nodes"]
        if node["module_id"] in ("material.degradable_box", "source.finite_local")) if profile_for_project(project) in (SPATIAL_PROFILE, CHEMOTAXIS_PROFILE) else 0
    metric_bytes = 1024 * len(project['groups']) if profile_for_project(project) == CHEMOTAXIS_PROFILE else 0
    output = frames * (2048 + budget_cells * (768 + longest_id + channel_bytes) + field_bytes + object_bytes + metric_bytes)
    return {"cells": cells, "voxels": voxels, "steps": steps,
            "memory_bytes": memory, "output_bytes": output + final_field_estimate(submission)}
