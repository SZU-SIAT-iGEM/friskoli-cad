"""Allocation-free admission estimates and source-backed execution metadata."""
from __future__ import annotations

import hashlib
import platform
from importlib.resources import files

import numpy as np

from friskoli_cad.engine.compiler import compile_graph
from friskoli_cad.engine.field_backend import backend_environment
from .artifacts import final_field_estimate, concentration_species
from friskoli_cad.engine.profiles import MODULAR_PROFILE, profile_for_project, registry_for_profile, registry_for_project, task_version
from friskoli_cad.protocol.task_validation import sha256

BACKEND = "numpy-cpu"


def source_hashes() -> dict:
    root = files("friskoli_cad")
    paths = [(name, root.joinpath(name)) for name in ("project.py", "packages.py")]
    for folder in ("engine", "protocol", "science"):
        paths.extend((folder + "/" + entry.name, entry)
                     for entry in root.joinpath(folder).iterdir()
                     if entry.name.endswith(".py") and entry.name != "task_validation.py")
    paths.extend(("science/data/" + entry.name, entry)
                 for entry in root.joinpath("science", "data").iterdir()
                 if entry.name.endswith(".json"))
    paths.extend(("engine/declarations/" + entry.name, entry) for entry in root.joinpath("engine", "declarations").iterdir() if entry.name.endswith(".json"))
    paths.append(("tasks/worker.py", root.joinpath("tasks", "worker.py")))
    paths.append(("tasks/artifacts.py", root.joinpath("tasks", "artifacts.py")))
    paths.append(("tasks/arrays.py", root.joinpath("tasks", "arrays.py")))
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in sorted(paths)}


def registry_metadata(profile=MODULAR_PROFILE, project=None) -> tuple:
    registry = registry_for_project(project) if project is not None else registry_for_profile(profile)
    registry_document = {"manifests": list(registry.manifests), "catalog": registry.catalog}
    sources = source_hashes()
    if project and project.get('dependency_lock'):
        sources = {**sources, 'dependency_lock':sha256(project['dependency_lock'])}
    implementations = [{"id": item["id"], "version": item["version"],
        "sha256": sha256({"id": item["id"], "version": item["version"], "sources": sources})}
        for item in registry.manifests]
    implementations.sort(key=lambda item: (item["id"], item["version"]))
    return registry, {"registry_sha256": sha256(registry_document),
                      "implementations": implementations}, sources


def compiled_plan(project, registry, *, backend=BACKEND) -> dict:
    from friskoli_cad.engine.module_api import thaw
    plan = compile_graph(project["graph"], registry.manifests)
    from friskoli_cad.engine.execution_planner import plan_execution
    schedule = plan_execution(plan, registry, backend=backend, world_sizes={
        'voxels':int(np.prod(project['domain']['counts_xyz'])),
        'populations':{gid:len(group['ids']) for gid,group in project['groups'].items()}})
    return {"plan_version": task_version(profile_for_project(project)), "graph_id": plan.id,
        "execution_plan": schedule, "schedule": schedule,
        "execution_semantics": profile_for_project(project), "nodes": [{
            "id": node.id, "module_id": node.module_id, "module_version": node.module_version,
            "owner_kind": node.owner_kind, "owner_id": node.owner_id, "phase": node.phase,
            "parameters": {name: {"value": thaw(item.value), "unit": item.unit,
                "provenance_kind": item.provenance_kind,
                "provenance_reference": item.provenance_reference}
                for name, item in node.parameters.items()},
            "inputs": {name: {"source_node": item.source_node,
                "source_port": item.source_port, "timing": item.timing}
                for name, item in node.inputs.items()},
            "outputs": list(node.outputs), "initial_outputs": list(node.initial_outputs),
        } for node in plan.nodes]}


def provenance(seed: int, sources: dict, project=None, backend=BACKEND) -> dict:
    from friskoli_cad.engine.module_api import execution_contract
    registry = registry_for_project(project) if project is not None else registry_for_profile()
    used = project is not None and any(execution_contract(registry.get(n['module_id'], n['module_version'])).get('uses_rng', False) for n in project['graph']['nodes'])
    return {"provenance_version": "0.2.0", "backend": backend, "precision": "float64",
        "python_version": platform.python_version(), "numpy_version": np.__version__,
        **({"backend_environment": backend_environment(backend)} if backend == "numpy-cupy-cuda" else {}),
        "platform": platform.platform(), "source_sha256": sources,
        "rng": {"algorithm": "pcg64-sha256-key-v1", "seed": seed, "used": bool(used)}}


def estimate(submission, registry) -> dict:
    """Conservative array/copy and serialized-frame budgets; no field allocation."""
    from friskoli_cad.engine.module_api import execution_contract
    from friskoli_cad.engine.execution_planner import plan_execution
    from friskoli_cad.engine.port_semantics import RECORD_BYTE_LIMIT
    project = submission['project']
    profile_for_project(project)
    cells = sum(len(g['ids']) for g in project['groups'].values())
    grows = any('lifecycle.division' in execution_contract(registry.get(n['module_id'], n['module_version'])).get('effects', ()) for n in project['graph']['nodes'])
    budget_cells = max(cells, project.get('system_limits', {}).get('max_cells', 256)) if grows else cells
    nx, ny, nz = project['domain']['counts_xyz']
    voxels = nx * ny * nz
    steps = submission['execution']['steps']
    every = submission['output_plan']['frame_every_steps']
    frames = 1 + steps // every + int(steps % every != 0)
    plan = compile_graph(project['graph'], registry.manifests)
    populations = {gid: max(len(g['ids']), budget_cells) if grows else len(g['ids']) for gid, g in project['groups'].items()}
    planned = plan_execution(plan, registry, backend=submission['execution']['backend'], world_sizes={'voxels': voxels, 'populations': populations})['memory']
    species = concentration_species(project)
    memory = (64 * 1024 * 1024 + voxels * (len(species) * 8 * 16 + 8)
              + 16 * (planned['numeric_outputs_bytes'] + planned['numeric_state_bytes'])
              + 16 * RECORD_BYTE_LIMIT * planned['unbounded_record_ports'] + budget_cells * 8192
              + 32 * budget_cells * budget_cells + planned.get('declared_workspace_bytes', 0))
    channel_bytes = sum(len(n.encode('utf-8')) + 32 for n in submission['output_plan']['observables'])
    longest_id = max((len(i.encode('utf-8')) for g in project['groups'].values() for i in g['ids']), default=0)
    field_bytes = 0
    if submission['output_plan']['include_fields']:
        stride = submission['output_plan'].get('field_stride_xyz', [1, 1, 1])
        preview_voxels = (nx // stride[0]) * (ny // stride[1]) * (nz // stride[2])
        field_bytes = sum(384 + len(n.encode('utf-8')) + preview_voxels * 8 for n in species)
    object_bytes = sum(192 + len(n['id'].encode('utf-8')) * 6 for n in project['graph']['nodes'] if set(getattr(registry.get(n['module_id'], n['module_version']), 'provides_roles', ())) & {'material.body', 'source.inventory'})
    output = frames * (2048 + budget_cells * (768 + longest_id + channel_bytes) + field_bytes + object_bytes + 1024 * len(project['groups']))
    return {'cells': cells, 'voxels': voxels, 'steps': steps, 'memory_bytes': memory, 'output_bytes': output + final_field_estimate(submission)}
