"""Allocation-free admission estimates and source-backed execution metadata."""
from __future__ import annotations

import hashlib
import platform
from importlib.resources import files

import numpy as np

from friskoli_cad.engine.compiler import compile_graph
from friskoli_cad.engine.modules import default_registry
from friskoli_cad.protocol.task_validation import sha256

SEMANTICS = "legacy-explicit-v1"
BACKEND = "numpy-cpu"


def source_hashes() -> dict:
    root = files("friskoli_cad")
    paths = [("project.py", root.joinpath("project.py"))]
    for folder in ("engine", "protocol"):
        paths.extend((folder + "/" + entry.name, entry)
                     for entry in root.joinpath(folder).iterdir()
                     if entry.name.endswith(".py") and entry.name != "task_validation.py")
    paths.append(("tasks/worker.py", root.joinpath("tasks", "worker.py")))
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in sorted(paths)}


def registry_metadata() -> tuple:
    registry = default_registry()
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
    return {"plan_version": "0.1.0", "graph_id": plan.id,
        "execution_semantics": SEMANTICS, "nodes": [{
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


def provenance(seed: int, sources: dict) -> dict:
    return {"provenance_version": "0.1.0", "backend": BACKEND, "precision": "float64",
        "python_version": platform.python_version(), "numpy_version": np.__version__,
        "platform": platform.platform(), "source_sha256": sources,
        "rng": {"algorithm": "none-deterministic", "seed": seed, "used": False}}


def estimate(submission, registry) -> dict:
    """Conservative array/copy and serialized-frame budgets; no NumPy allocation."""
    project = submission["project"]
    cells = sum(len(group["ids"]) for group in project["groups"].values())
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
    memory = 64 * 1024 * 1024 + voxels * max(1, field_arrays) * 8 * 16
    memory += cells * (4096 + cell_arrays * 8 * 16)
    # UTF-8 names and channel IDs are included, rather than assuming ASCII names.
    channel_bytes = sum(len(name.encode("utf-8")) + 32
                        for name in submission["output_plan"]["observables"])
    longest_id = max((len(identifier.encode("utf-8")) for group in project["groups"].values()
                      for identifier in group["ids"]), default=0)
    output = frames * (2048 + cells * (768 + longest_id + channel_bytes))
    return {"cells": cells, "voxels": voxels, "steps": steps,
            "memory_bytes": memory, "output_bytes": output}
