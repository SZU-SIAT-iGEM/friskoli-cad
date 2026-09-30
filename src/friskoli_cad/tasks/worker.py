"""Spawned numerical worker; the parent alone publishes durable task state."""
from __future__ import annotations

import json
import os
import multiprocessing
import threading
from pathlib import Path

from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import canonical_bytes, sha256
from .metadata import source_hashes


def _send(spool: Path, acknowledgement, message: dict, maximum: int) -> None:
    data = canonical_bytes(message)
    if len(data) > maximum:
        raise WorkerLimit("A complete output frame exceeds chunk_bytes.")
    temporary = spool / "pending.tmp"
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    acknowledgement.clear()
    os.replace(temporary, spool / "ready.json")
    # The parent only acknowledges after the complete step is committed.
    acknowledgement.wait()


class WorkerLimit(ValueError):
    pass


def run_worker(submission: dict, spool_name: str, acknowledgement, limits: dict, expected_sources: dict) -> None:
    """Never mutates SQLite or published chunks; one bounded message is in flight."""
    def watch_parent():
        parent = multiprocessing.parent_process()
        if parent is not None:
            while parent.is_alive():
                threading.Event().wait(0.2)
            os._exit(1)
    threading.Thread(target=watch_parent, daemon=True).start()
    spool = Path(spool_name)
    phase = "initialize"
    try:
        if source_hashes() != expected_sources:
            raise ValueError("Execution source changed after admission.")
        simulation = simulation_from_project(submission["project"])
        execution = submission["execution"]
        observations = set(submission["output_plan"]["observables"])
        every = submission["output_plan"]["frame_every_steps"]
        for step in range(execution["steps"] + 1):
            phase = "execute"
            snapshot = simulation.current if step == 0 else simulation.step(execution["dt_s"])
            if len(snapshot.cell_frame["cells"]) > limits["cells"]:
                raise WorkerLimit("Cell growth exceeded the published cell limit.")
            if snapshot.domain.voxel_count > limits["voxels"]:
                raise WorkerLimit("Grid growth exceeded the published voxel limit.")
            message = {"kind": "step", "step": step,
                       "time_s": snapshot.cell_frame["time_s"],
                       "final": step == execution["steps"]}
            if step == 0 or step % every == 0 or message["final"]:
                frame = dict(snapshot.cell_frame)
                frame["cells"] = [{**cell, "channels": {key: value for key, value
                    in cell["channels"].items() if key in observations}}
                    for cell in frame["cells"]]
                message["frame"] = frame
                domain = snapshot.domain
                message["grid_revision"] = sha256({"shape": list(domain.shape),
                    "spacing": [domain.dx_um, domain.dy_um, domain.dz_um]})
            _send(spool, acknowledgement, message, limits["chunk_bytes"] + 4096)
    except BaseException as error:
        code = "task.resource_limit" if isinstance(error, WorkerLimit) else "task.worker_failed"
        # Numerical error paths are input pointers; arbitrary exception details never expose paths.
        message = str(error) if isinstance(error, WorkerLimit) else "Numerical worker failed during " + phase + "."
        try:
            _send(spool, acknowledgement, {"kind": "error", "code": code,
                "message": message, "phase": phase}, 4096)
        except BaseException:
            pass
