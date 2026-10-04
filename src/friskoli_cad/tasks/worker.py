"""Spawned numerical worker; the parent alone publishes durable task state."""
from __future__ import annotations

import os
import multiprocessing
import threading
import time
import numpy as np

from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.core import SimulationError
from friskoli_cad.protocol.task_validation import canonical_bytes, sha256
from .metadata import source_hashes
from .artifacts import write_final_fields, concentration_species, nonnegative_mean


PROGRESS_INTERVAL_S = 0.25


def _send(channel, message: dict, maximum: int):
    data = canonical_bytes(message)
    if len(data) > maximum:
        raise WorkerLimit("A complete output frame exceeds chunk_bytes.")
    # The pipe is transient, bounded to one checked message. Only the parent's
    # indexed chunks/task transaction are durable; no worker file is a result.
    channel.send_bytes(data)
    # The parent only acknowledges after the complete step is committed.
    action = channel.recv_bytes()
    if action not in (b"\x01", b"p"):
        raise ValueError("Invalid committed-step acknowledgement")
    return action


class WorkerLimit(ValueError):
    pass


def concentration_preview(values, domain, stride_xyz, *, binary=False):
    """Uniform-grid block means; preserves volume integral and never changes solver state."""
    sx, sy, sz = stride_xyz
    if any(type(s) is not int or s < 1 or n % s for n, s in zip(
            (domain.nx, domain.ny, domain.nz), stride_xyz)):
        raise ValueError("Field stride must divide every grid count")
    preview = values if (sx, sy, sz) == (1, 1, 1) else nonnegative_mean(values.reshape(
        domain.nz // sz, sz, domain.ny // sy, sy, domain.nx // sx, sx), axis=(1, 3, 5))
    return {"unit": "uM", "values_zyx": preview if binary else preview.tolist(), "aggregation": "volume_mean",
            "field_domain": {"geometry": domain.geometry,
                "counts_xyz": [domain.nx // sx, domain.ny // sy, domain.nz // sz],
                "spacing_um_xyz": [domain.dx_um * sx, domain.dy_um * sy, domain.dz_um * sz]}}


def run_worker(submission: dict, channel, limits: dict, expected_sources: dict, artifact_path=None, resume_path=None) -> None:
    """Never mutates SQLite or published chunks; one bounded message is in flight."""
    def watch_parent():
        parent = multiprocessing.parent_process()
        if parent is not None:
            while parent.is_alive():
                threading.Event().wait(0.2)
            os._exit(1)
    threading.Thread(target=watch_parent, daemon=True).start()
    phase = "initialize"
    try:
        if source_hashes() != expected_sources:
            raise SimulationError("task.source_changed", "Execution source changed after service startup; restart the service and submit against its current lock.")
        simulation = simulation_from_project(submission["project"], seed=submission["execution"]["seed"],
            field_backend=submission["execution"]["backend"])
        context = {}
        if resume_path:
            from friskoli_cad.engine.task_checkpoint import load_task_checkpoint
            simulation, document = load_task_checkpoint(resume_path, maximum=limits['estimated_memory_bytes'], return_document=True)
            context = document.get('task_context', {})
        execution = submission["execution"]
        modern = submission['task_contract_version'] == '0.6.0'
        started = time.monotonic()
        start_step = simulation.frame_index if resume_path else 0
        last_output_step = context.get('last_output_step')
        last_output_events = context.get('last_output_events', [])
        last_output_deaths = context.get('last_output_deaths', [])
        start_already_stored = bool(resume_path and __import__('pathlib').Path(resume_path).name != 'resume.zip' and last_output_step == start_step)
        checkpoint_every = submission['output_plan'].get('checkpoint_every_steps', 1000)
        checkpoint_path = str(__import__('pathlib').Path(artifact_path).with_name('checkpoint.pending.zip')) if artifact_path else None
        def checkpoint():
            from friskoli_cad.engine.task_checkpoint import save_task_checkpoint
            return save_task_checkpoint(simulation, checkpoint_path, maximum=limits['estimated_memory_bytes'],
                task_context={'pending_events': pending_events, 'pending_deaths': pending_deaths, 'last_output_step':last_output_step, 'last_output_events':last_output_events, 'last_output_deaths':last_output_deaths})
        observations = set(submission["output_plan"]["observables"])
        every = submission["output_plan"]["frame_every_steps"]
        pending_events = context.get('pending_events', [])
        pending_deaths = context.get('pending_deaths', [])
        if resume_path and __import__('pathlib').Path(resume_path).name == 'resume.zip' and last_output_step == start_step:
            pending_events = last_output_events
            pending_deaths = last_output_deaths
        pending_event_bytes = len(canonical_bytes(pending_events)) + len(canonical_bytes(pending_deaths))
        last_progress = time.monotonic()
        for step in range(start_step, execution["steps"] + 1):
            phase = "execute"
            snapshot = simulation.current if step == start_step else simulation.step(execution["dt_s"])
            if execution['semantics'] == 'modular-spatial-v1' and (step != start_step or not resume_path):
                pending_events.extend(snapshot.cell_frame['events'])
                pending_deaths.extend(snapshot.lifecycle_details.get('deaths', []))
                pending_event_bytes += len(canonical_bytes(snapshot.cell_frame['events'])) + len(canonical_bytes(snapshot.lifecycle_details.get('deaths', [])))
                if not modern and pending_event_bytes > limits['chunk_bytes']:
                    raise WorkerLimit('Lifecycle events between stored frames exceed chunk_bytes; reduce frame_every_steps.')
            if len(snapshot.cell_frame["cells"]) > limits["cells"]:
                raise WorkerLimit("Cell growth exceeded the published cell limit.")
            if snapshot.domain.voxel_count > limits["voxels"]:
                raise WorkerLimit("Grid growth exceeded the published voxel limit.")
            message = {"kind": "step", "step": step,
                       "time_s": snapshot.cell_frame["time_s"],
                       "final": step == execution["steps"]}
            if not (step == start_step and start_already_stored) and (step == start_step or step % every == 0 or message["final"] or (modern and pending_event_bytes >= limits["chunk_bytes"] // 4)):
                last_output_step = step
                frame = dict(snapshot.cell_frame)
                if execution['semantics'] == 'modular-spatial-v1':
                    frame['events'] = pending_events
                    last_output_events, last_output_deaths = pending_events, pending_deaths
                    pending_events = []
                    message['lifecycle_details'] = {'lifecycle_version': '1.0.0', 'events':frame['events']}
                    pending_deaths = []
                    pending_event_bytes = 0
                    message['metrics'] = dict(snapshot.metrics)
                frame["cells"] = [{**cell, "channels": {key: value for key, value
                    in cell["channels"].items() if key in observations}}
                    for cell in frame["cells"]]
                message["frame"] = frame
                if execution['semantics'] == 'modular-spatial-v1':
                    from friskoli_cad.engine.modular_runtime import snapshot_object_declarations
                    declarations = snapshot_object_declarations(submission['project'], simulation.registry)
                    nodes_by_id = {node['id']: node for node in submission['project']['graph']['nodes']}
                    expected_objects = {owner: nodes_by_id[d['node_id']] for owner, d in declarations.items()}
                    states = snapshot.object_states
                    if set(states) != set(expected_objects):
                        raise ValueError("Incomplete object inventory snapshot")
                    for node_id, inventory in states.items():
                        node = expected_objects[node_id]
                        expected_type = declarations[node_id]['object_type']
                        amount = inventory.get("remaining_molecules")
                        if (set(inventory) != {"object_type", "remaining_molecules"} or inventory["object_type"] != expected_type
                            or type(amount) not in (int, float) or not np.isfinite(amount)
                            or not 0 <= amount <= node["parameters"]["initial_molecules"]["value"]):
                            raise ValueError("Invalid object inventory snapshot")
                    message["object_states"] = {key: dict(value) for key, value in states.items()}
                if submission["output_plan"]["include_fields"] or (message["final"] and submission["output_plan"].get("include_final_fields", False)):
                    expected = concentration_species(submission["project"])
                    if set(snapshot.concentration_fields) != expected or any(
                        snapshot.concentration_units[species] != "uM" or values.shape != snapshot.domain.shape
                        or not np.isfinite(values).all() or (values < 0).any()
                        for species, values in snapshot.concentration_fields.items()
                    ):
                        raise ValueError("Invalid complete spatial fields")
                if submission["output_plan"]["include_fields"]:
                    message['concentrations'] = {species: concentration_preview(values, snapshot.domain,
                        submission['output_plan'].get('field_stride_xyz', [1, 1, 1]), binary=True)
                        for species, values in snapshot.concentration_fields.items()}
                if modern and 'concentrations' in message:
                    from .arrays import write_array
                    from pathlib import Path
                    for species_index, field in enumerate(message['concentrations'].values()):
                        field['array'] = write_array(field.pop('values_zyx'), Path(artifact_path).parent,
                            f'array_{step:010d}_{species_index:04d}', limits['chunk_bytes'])
                        field['array']['axis_order'] = 'zyx'
                domain = snapshot.domain
                message["grid_revision"] = sha256({"shape": list(domain.shape),
                    "spacing": [domain.dx_um, domain.dy_um, domain.dz_um]})
            if message["final"] and submission["output_plan"].get("include_final_fields", False):
                if artifact_path is None:
                    raise ValueError("Final field artifact path is unavailable")
                message["final_field_artifact"] = write_final_fields(snapshot, step, artifact_path, limits["output_bytes"])
            rotate = modern and time.monotonic() - started >= max(.1, limits['wall_time_s'] * .7)
            if modern and (step == start_step or step % checkpoint_every == 0 or message['final'] or rotate):
                message['checkpoint_artifact'] = checkpoint()
                message['rotate'] = rotate and not message['final']
            # Every numerical step above still computes and checks state. Pure
            # progress between requested output frames needs no per-step commit.
            # The next complete step after this interval also observes cancel.
            if "frame" in message or "checkpoint_artifact" in message or message["final"] or time.monotonic() - last_progress >= PROGRESS_INTERVAL_S:
                action = _send(channel, message, limits["chunk_bytes"] + 4096)
                if message.get('rotate'):
                    return
                if action == b'p':
                    _send(channel, {'kind':'step', 'step':step, 'time_s':simulation.time_s,
                        'final':False, 'paused':True, 'checkpoint_artifact':checkpoint()}, limits['chunk_bytes'] + 4096)
                    return
                last_progress = time.monotonic()
    except (EOFError, BrokenPipeError):
        # The parent stopped or closed its confirmation channel; there is no
        # peer left to consume another error message.
        pass
    except BaseException as error:
        known = isinstance(error, SimulationError) and submission['execution']['semantics'] == 'modular-spatial-v1'
        if not known and not isinstance(error, WorkerLimit):
            # Details stay in the local operator log, never in public task errors.
            __import__('logging').getLogger(__name__).exception('Numerical worker failed during %s', phase)
        if known and error.code == 'resource.cell_limit' and submission['task_contract_version'] == '0.6.0':
            # The runtime rejected the uncommitted transaction. Persist the
            # preceding state even when it lies between periodic checkpoints.
            try:
                _send(channel, {'kind':'step','step':simulation.frame_index,'time_s':simulation.time_s,
                    'final':False,'checkpoint_artifact':checkpoint()}, limits['chunk_bytes'] + 4096)
            except (OSError, ValueError, EOFError):
                pass
        code = error.code if known else ("task.resource_limit" if isinstance(error, WorkerLimit) else "task.worker_failed")
        # Numerical error paths are input pointers; arbitrary exception details never expose paths.
        message = str(error) if known or isinstance(error, WorkerLimit) else "Numerical worker failed during " + phase + "."
        try:
            _send(channel, {"kind": "error", "code": code,
                "message": message, "phase": phase, "path": error.path if known else ""}, 4096)
        except BaseException:
            pass
    finally:
        channel.close()
