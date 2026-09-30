"""SQLite admission, durable publication and an isolated single worker."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import math
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
import uuid
import psutil
from friskoli_cad.project import validate_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.protocol.task_validation import (VERSION, TaskValidationError, canonical_bytes, canonical_loads, sha256, strict_json_loads, validate_submission)
from .metadata import BACKEND, SEMANTICS, compiled_plan, estimate, provenance, registry_metadata
from .worker import run_worker

TERMINAL = frozenset(("completed", "failed", "cancelled", "interrupted"))
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def _issue(code, message, path="", phase="request"):
    return {"code": code, "severity": "error", "phase": phase, "path": path, "targets": [], "message": message}

class TaskError(ValueError):
    def __init__(self, status, code, message, path="", *, phase="request", issues=None):
        self.status, self.code, self.message, self.path = status, code, message, path
        self.issues = issues if issues is not None else [_issue(code, message, path, phase)]
        super().__init__(message)
    def to_dict(self):
        return {"task_contract_version": VERSION, "issues": self.issues}

@dataclass(frozen=True)
class TaskLimits:
    request_bytes: int = 1_000_000
    cells: int = 2_000
    voxels: int = 262_144
    steps: int = 10_000
    estimated_memory_bytes: int = 512 * 1024 * 1024
    output_bytes: int = 128 * 1024 * 1024
    queued_runs: int = 8
    active_runs: int = 1
    wall_time_s: int = 60
    event_page_size: int = 100
    chunk_bytes: int = 4 * 1024 * 1024
    retention_seconds: int = 86_400
    idempotency_retention_seconds: int = 172_800
    def __post_init__(self):
        for item in fields(self):
            if type(getattr(self, item.name)) is not int or getattr(self, item.name) < 1:
                raise ValueError(item.name + " must be a positive integer")
        if self.active_runs != 1:
            raise ValueError("Only one active numerical process is supported")
        if self.idempotency_retention_seconds < self.retention_seconds:
            raise ValueError("Idempotency retention cannot be shorter than task retention")

class TaskService:
    """Local durable queue; actual RSS is sampled every 10 ms and excess workers terminated.

    Normal cancellation waits for a complete step. Wall-time/RSS termination
    discards the uncommitted step; only the parent publishes durable state.
    """
    def __init__(self, directory, limits=None):
        self.directory = Path(directory).resolve()
        self.limits = limits if isinstance(limits, TaskLimits) else TaskLimits(**(limits or {}))
        self._lock = threading.RLock()
        self._stop, self._wake = threading.Event(), threading.Event()
        self._closed = self._unavailable = False
        self._process = None
        self._corrupt = set()
        self._registry, self._version_lock, self._sources = registry_metadata()
        self._ctx = multiprocessing.get_context("spawn")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._owner_file = (self.directory / "service.lock").open("a+b")
        self._acquire_owner()
        try:
            self._db = sqlite3.connect(self.directory / "tasks.sqlite3", timeout=10, isolation_level=None, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (run_id TEXT PRIMARY KEY, task TEXT NOT NULL, input BLOB NOT NULL,
                    plan TEXT NOT NULL, provenance TEXT NOT NULL, manifest TEXT, created REAL NOT NULL,
                    finished REAL, event_floor INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, digest TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES tasks(run_id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS events (run_id TEXT NOT NULL REFERENCES tasks(run_id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY(run_id, seq));
                CREATE TABLE IF NOT EXISTS chunks (run_id TEXT NOT NULL REFERENCES tasks(run_id) ON DELETE CASCADE,
                    chunk_id TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(run_id, chunk_id));
                CREATE TABLE IF NOT EXISTS tombstones (run_id TEXT PRIMARY KEY, expires REAL NOT NULL);
            """)
            self._recover()
            self._thread = threading.Thread(target=self._manage, name="friskoli-task-manager", daemon=True)
            self._thread.start()
        except BaseException:
            if hasattr(self, "_db"):
                self._db.close()
            self._release_owner()
            raise

    def _acquire_owner(self):
        try:
            if os.name == "nt":
                import msvcrt
                self._owner_file.seek(0, 2)
                if self._owner_file.tell() == 0:
                    self._owner_file.write(b"0")
                    self._owner_file.flush()
                self._owner_file.seek(0)
                msvcrt.locking(self._owner_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._owner_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._owner_file.close()
            raise TaskError(503, "task.service_busy", "This task directory already has a service owner.") from error

    def _release_owner(self):
        if self._owner_file.closed:
            return
        if os.name == "nt":
            import msvcrt
            self._owner_file.seek(0)
            msvcrt.locking(self._owner_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._owner_file.fileno(), fcntl.LOCK_UN)
        self._owner_file.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                yield
                self._db.execute("COMMIT")
            except BaseException as error:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                if isinstance(error, sqlite3.Error):
                    self._unavailable = True
                    raise TaskError(503, "task.storage_unavailable", "Durable task storage is unavailable.", phase="publish") from error
                raise

    def _dump(self, value):
        return canonical_bytes(value).decode("utf-8")

    def _row(self, run_id):
        if not isinstance(run_id, str) or not _ID.fullmatch(run_id):
            raise TaskError(400, "task.run_id", "Invalid task identifier.")
        row = self._db.execute("SELECT * FROM tasks WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            gone = self._db.execute("SELECT 1 FROM tombstones WHERE run_id=?", (run_id,)).fetchone()
            raise TaskError(410 if gone else 404, "task.expired" if gone else "task.not_found",
                            "Task has expired." if gone else "Unknown task.")
        return row

    def _event(self, task, kind, manifest=None):
        task["last_event_seq"] += 1
        task["updated_at"] = _now()
        event = {"seq": task["last_event_seq"], "type": kind, "created_at": task["updated_at"], "task": task}
        finished = time.time() if task["status"] in TERMINAL else None
        self._db.execute("UPDATE tasks SET task=?, finished=COALESCE(finished,?) WHERE run_id=?",
                         (self._dump(task), finished, task["run_id"]))
        self._db.execute("INSERT INTO events VALUES(?,?,?)", (task["run_id"], event["seq"], self._dump(event)))
        if manifest is not None:
            manifest.update(status=task["status"], completeness=task["result"]["completeness"],
                            progress=task["progress"], issues=task["issues"])
            self._db.execute("UPDATE tasks SET manifest=? WHERE run_id=?", (self._dump(manifest), task["run_id"]))

    def capabilities(self):
        return {"task_contract_version": VERSION, "mode": "single-worker",
            "pause": False, "resume": False, "checkpoint": False, "partial_results": True,
            "hash_canonicalization": "RFC8785", "limits": asdict(self.limits),
            "version_lock": canonical_loads(self._dump(self._version_lock)),
            "execution": {"semantics": SEMANTICS, "backend": BACKEND, "default_seed": 0}}

    def version_lock(self, project=None):
        lock = canonical_loads(self._dump(self._version_lock))
        if project is not None:
            used = {(node["module_id"], node["module_version"]) for node in project["graph"]["nodes"]}
            lock["implementations"] = [item for item in lock["implementations"] if (item["id"], item["version"]) in used]
        return lock

    def _admit(self, submission):
        project, execution = submission["project"], submission["execution"]
        if execution["semantics"] != SEMANTICS or execution["backend"] != BACKEND:
            raise TaskError(422, "task.execution_unsupported", "Unsupported execution semantics or backend.", "/execution", phase="resolve")
        if not math.isfinite(execution["dt_s"] * execution["steps"]):
            raise TaskError(422, "task.time_overflow", "Simulation duration must be finite.", "/execution")
        provided, expected, full = submission["version_lock"], self.version_lock(project), self._version_lock
        if provided["registry_sha256"] != full["registry_sha256"]:
            raise TaskError(422, "task.lock_mismatch", "Registry lock does not match this service.", "/version_lock/registry_sha256", phase="resolve")
        entries = provided["implementations"]
        unique = {(item["id"], item["version"]): item for item in entries}
        expected_map = {(item["id"], item["version"]): item for item in expected["implementations"]}
        full_map = {(item["id"], item["version"]): item for item in full["implementations"]}
        if (len(unique) != len(entries) or not set(expected_map) <= set(unique)
                or any(key not in full_map or value != full_map[key] for key, value in unique.items())):
            raise TaskError(422, "task.lock_mismatch", "Exact implementation locks are required for every graph module.", "/version_lock/implementations", phase="resolve")
        try:
            validate_project(project, self._registry.manifests)
            plan = compiled_plan(project, self._registry)
        except ProtocolError as error:
            path = getattr(error, "path", "")
            raise TaskError(422, getattr(error, "code", "task.project_invalid"), str(error),
                            "/project" + (path if path != "/" else ""), phase="validate") from error
        unknown = set(submission["output_plan"]["observables"]) - set(project["run"]["channels"])
        if unknown:
            raise TaskError(422, "task.observable_unknown", "Output plan includes an unknown frame channel.", "/output_plan/observables", phase="validate")
        if submission["output_plan"]["frame_every_steps"] != 1 and any("divide" in node["outputs"] for node in plan["nodes"]):
            raise TaskError(422, "task.sampling_unsupported", "Division models require every complete frame to preserve lineage events.", "/output_plan/frame_every_steps", phase="validate")
        budget = estimate(submission, self._registry)
        for name, bound in (("cells", "cells"), ("voxels", "voxels"), ("steps", "steps"),
                            ("memory_bytes", "estimated_memory_bytes"), ("output_bytes", "output_bytes")):
            if budget[name] > getattr(self.limits, bound):
                raise TaskError(413, "task.resource_limit", name + " exceeds the published admission limit.",
                                "/execution" if name == "steps" else "/project", phase="estimate")
        frame_count = 1 + execution["steps"] // submission["output_plan"]["frame_every_steps"]
        frame_count += int(execution["steps"] % submission["output_plan"]["frame_every_steps"] != 0)
        if budget["output_bytes"] // frame_count > self.limits.chunk_bytes:
            raise TaskError(413, "task.resource_limit", "Estimated complete frame exceeds chunk_bytes.", "/output_plan", phase="estimate")
        return plan, budget

    def submit(self, submission, key):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise TaskError(400, "task.idempotency_key", "A valid Idempotency-Key is required.")
        if self._closed or self._unavailable:
            raise TaskError(503, "task.storage_unavailable", "The task service cannot accept new work.")
        try:
            if isinstance(submission, (bytes, str)):
                raw = submission if isinstance(submission, bytes) else submission.encode("utf-8")
                if len(raw) > self.limits.request_bytes:
                    raise TaskError(413, "task.resource_limit", "Request exceeds request_bytes.")
                submission = strict_json_loads(raw)
            data = canonical_bytes(submission)
        except TaskValidationError as error:
            raise TaskError(400, error.issues[0]["code"], str(error), issues=error.issues) from error
        if len(data) > self.limits.request_bytes:
            raise TaskError(413, "task.resource_limit", "Request exceeds request_bytes.")
        submission = canonical_loads(data)
        try:
            validate_submission(submission)
        except TaskValidationError as error:
            raise TaskError(422, error.issues[0]["code"], str(error), issues=error.issues) from error
        digest = sha256({name: value for name, value in submission.items() if name != "request_id"})
        with self._lock:
            existing = self._db.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
            if existing:
                return self._same(existing, digest), False
        plan, budget = self._admit(submission)
        metadata = provenance(submission["execution"]["seed"], self._sources)
        with self._transaction():
            existing = self._db.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
            if existing:
                return self._same(existing, digest), False
            count = self._db.execute("SELECT count(*) FROM tasks WHERE json_extract(task,'$.status')='queued'").fetchone()[0]
            if count >= self.limits.queued_runs:
                raise TaskError(429, "task.queue_full", "The waiting queue is full.", phase="queue")
            if shutil.disk_usage(self.directory).free < budget["output_bytes"] + len(data) * 2 + 1024 * 1024:
                raise TaskError(503, "task.storage_unavailable", "Insufficient free storage for durable acceptance.", phase="queue")
            run_id, stamp = "run_" + uuid.uuid4().hex, _now()
            snapshot = {"href": f"/api/runs/{run_id}/input", "document_sha256": hashlib.sha256(data).hexdigest(),
                "scientific_sha256": sha256({name: submission[name] for name in ("project", "version_lock", "execution", "output_plan")}),
                "plan_sha256": sha256(plan), "registry_sha256": self._version_lock["registry_sha256"],
                "canonicalization": "RFC8785", "edit_revision": submission["edit_revision"]}
            task = {"task_contract_version": VERSION, "run_id": run_id, "status": "queued",
                "created_at": stamp, "updated_at": stamp, "finished_at": None, "cancel_requested": False,
                "input_snapshot": snapshot, "estimate": budget,
                "progress": {"committed_step": 0, "simulation_time_s": 0}, "last_event_seq": 0,
                "result": {"href": f"/api/runs/{run_id}/result", "completeness": "none"}, "issues": []}
            self._db.execute("INSERT INTO tasks(run_id,task,input,plan,provenance,created) VALUES(?,?,?,?,?,?)",
                (run_id, self._dump(task), data, self._dump(plan), self._dump(metadata), time.time()))
            self._db.execute("INSERT INTO idempotency VALUES(?,?,?)", (key, digest, run_id))
            self._event(task, "accepted")
        self._wake.set()
        return task, True

    def _same(self, existing, digest):
        if existing["digest"] != digest:
            raise TaskError(409, "task.idempotency_conflict", "This key already names a different submission.")
        return canonical_loads(self._row(existing["run_id"])["task"])

    def get(self, run_id):
        with self._lock:
            return canonical_loads(self._row(run_id)["task"])

    def input(self, run_id):
        with self._lock:
            return canonical_loads(self._row(run_id)["input"])

    def events(self, run_id, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= self.limits.event_page_size:
            raise TaskError(400, "task.cursor_invalid", "Invalid event cursor or page size.")
        with self._lock:
            row = self._row(run_id)
            latest = canonical_loads(row["task"])["last_event_seq"]
            if after > latest:
                raise TaskError(400, "task.cursor_invalid", "Event cursor is newer than this task.")
            if after < row["event_floor"]:
                raise TaskError(410, "task.cursor_expired", "Event history before this cursor has expired.")
            records = self._db.execute("SELECT event FROM events WHERE run_id=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?",
                                       (run_id, after, latest, limit)).fetchall()
            events = [canonical_loads(item[0]) for item in records]
            next_after = events[-1]["seq"] if events else after
            return {"run_id": run_id, "events": events,
                    "next_after": next_after, "latest_seq": latest, "has_more": next_after < latest}

    def manifest(self, run_id):
        with self._lock:
            row = self._row(run_id)
            if row["manifest"] is None:
                raise TaskError(409, "task.result_unavailable", "No complete frame has been published.")
            return canonical_loads(row["manifest"])

    def chunk(self, run_id, chunk_id):
        with self._lock:
            self._row(run_id)
            if not isinstance(chunk_id, str) or not _ID.fullmatch(chunk_id):
                raise TaskError(400, "task.chunk_id", "Invalid chunk identifier.")
            row = self._db.execute("SELECT metadata FROM chunks WHERE run_id=? AND chunk_id=?", (run_id, chunk_id)).fetchone()
            if row is None:
                raise TaskError(404, "task.not_found", "Unknown or unpublished chunk.")
            metadata = canonical_loads(row[0])
            try:
                body = (self.directory / "runs" / run_id / (chunk_id + ".json")).read_bytes()
            except OSError as error:
                raise TaskError(503, "task.output_corrupt", "A published chunk is unavailable.", phase="publish") from error
            if len(body) != metadata["bytes"] or hashlib.sha256(body).hexdigest() != metadata["sha256"]:
                raise TaskError(503, "task.output_corrupt", "A published chunk failed integrity verification.", phase="publish")
            return body

    def cancel(self, run_id):
        with self._transaction():
            row = self._row(run_id)
            task = canonical_loads(row["task"])
            if task["status"] in TERMINAL:
                return task, False
            if task["cancel_requested"]:
                return task, True
            task["cancel_requested"] = True
            if task["status"] == "queued":
                task["status"], task["finished_at"] = "cancelled", _now()
                self._event(task, "cancelled")
                return task, False
            self._event(task, "cancel_requested", canonical_loads(row["manifest"]) if row["manifest"] else None)
        self._wake.set()
        return task, True

    def _finish(self, run_id, status, issue=None):
        with self._transaction():
            row = self._row(run_id)
            task = canonical_loads(row["task"])
            if task["status"] in TERMINAL:
                return task
            task["status"], task["finished_at"] = status, _now()
            if status == "cancelled":
                task["cancel_requested"] = True
            if issue is not None:
                task["issues"].append(issue)
            self._event(task, status, canonical_loads(row["manifest"]) if row["manifest"] else None)
            return task

    def _recover(self):
        for row in self._db.execute("SELECT run_id,task FROM tasks").fetchall():
            if canonical_loads(row["task"])["status"] not in TERMINAL:
                self._finish(row["run_id"], "interrupted", _issue("task.service_interrupted",
                    "The previous service stopped before a terminal task commit.", phase="recover"))
        # Preserve indexed partials even when corrupt; their reads report explicit errors.
        for row in self._db.execute("SELECT run_id,chunk_id FROM chunks").fetchall():
            try:
                self.chunk(row["run_id"], row["chunk_id"])
            except TaskError:
                self._corrupt.add(row["run_id"])

    def _claim(self):
        with self._transaction():
            row = self._db.execute("SELECT * FROM tasks WHERE json_extract(task,'$.status')='queued' ORDER BY created,run_id LIMIT 1").fetchone()
            if row is None:
                return None
            task = canonical_loads(row["task"])
            task["status"] = "running"
            self._event(task, "started")
            return task["run_id"], canonical_loads(row["input"])

    def _write_atomic(self, path, body):
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _publish(self, run_id, message):
        with self._lock:
            row = self._row(run_id)
            existing = canonical_loads(row["manifest"]) if row["manifest"] else None
            descriptors = existing["chunks"][:] if existing else []
        metadata = None
        if "frame" in message:
            sequence = len(descriptors)
            chunk_id = f"chunk_{sequence:08d}"
            body = canonical_bytes({"task_contract_version": VERSION, "run_id": run_id,
                "chunk_id": chunk_id, "frames": [{"sequence": sequence, "step_index": message["step"],
                    "time_s": message["time_s"], "grid_revision": message["grid_revision"], "frame": message["frame"]}]})
            if len(body) > self.limits.chunk_bytes or sum(item["bytes"] for item in descriptors) + len(body) > self.limits.output_bytes:
                raise TaskError(413, "task.resource_limit", "Actual output exceeds the published output limit.", "/output_plan", phase="publish")
            metadata = {"chunk_id": chunk_id, "href": f"/api/runs/{run_id}/chunks/{chunk_id}",
                "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body), "media_type": "application/json",
                "first_step": message["step"], "last_step": message["step"]}
            self._write_atomic(self.directory / "runs" / run_id / (chunk_id + ".json"), body)
        with self._transaction():
            row = self._row(run_id)
            task = canonical_loads(row["task"])
            if task["status"] != "running":
                return task["status"]
            if metadata:
                self._db.execute("INSERT INTO chunks VALUES(?,?,?)", (run_id, metadata["chunk_id"], self._dump(metadata)))
                descriptors.append(metadata)
            task["progress"] = {"committed_step": message["step"], "simulation_time_s": message["time_s"]}
            if descriptors:
                task["result"]["completeness"] = "partial"
            if task["cancel_requested"]:
                task["status"], task["finished_at"] = "cancelled", _now()
            elif message["final"]:
                task["status"], task["finished_at"] = "completed", _now()
                task["result"]["completeness"] = "complete"
            manifest = {"task_contract_version": VERSION, "run_id": run_id,
                "input_snapshot": task["input_snapshot"], "chunks": descriptors,
                "compiled_plan": canonical_loads(row["plan"]), "provenance": canonical_loads(row["provenance"])} if descriptors else None
            self._event(task, "progress" if task["status"] == "running" else task["status"], manifest)
            return task["status"]

    def _run_active(self, run_id, submission):
        folder = self.directory / "runs" / run_id
        spool = folder / "worker"
        spool.mkdir(parents=True, exist_ok=True)
        acknowledgement = self._ctx.Event()
        process = self._ctx.Process(target=run_worker, args=(submission, str(spool), acknowledgement,
                                    asdict(self.limits), self._sources), name="friskoli-numerical-worker", daemon=True)
        self._process = process
        started = time.monotonic()
        try:
            process.start()
            observed = psutil.Process(process.pid)
            while not self._stop.is_set():
                if time.monotonic() - started > self.limits.wall_time_s:
                    raise TaskError(413, "task.resource_limit", "Worker exceeded wall_time_s.", phase="execute")
                try:
                    if observed.memory_info().rss > self.limits.estimated_memory_bytes:
                        raise TaskError(413, "task.resource_limit", "Worker RSS exceeded the published memory budget.", phase="execute")
                except psutil.NoSuchProcess:
                    pass
                ready = spool / "ready.json"
                if ready.exists():
                    if ready.stat().st_size > self.limits.chunk_bytes + 4096:
                        raise TaskError(413, "task.resource_limit", "Worker message exceeded chunk_bytes.", phase="publish")
                    message = canonical_loads(ready.read_bytes())
                    ready.unlink()
                    if message["kind"] == "error":
                        self._finish(run_id, "failed", _issue(message["code"], message["message"], phase=message["phase"]))
                        return
                    if self._publish(run_id, message) in TERMINAL:
                        return
                    acknowledgement.set()
                elif not process.is_alive():
                    raise TaskError(500, "task.worker_failed", "Numerical worker exited before publishing its final step.", phase="execute")
                self._stop.wait(0.01)
            self._finish(run_id, "interrupted", _issue("task.service_interrupted", "Service closed before this task finished.", phase="recover"))
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                process.close()
            self._process = None

    def _manage(self):
        while not self._stop.is_set():
            run_id = None
            try:
                claimed = self._claim()
                if claimed is None:
                    self._wake.wait(0.1)
                    self._wake.clear()
                    continue
                run_id, submission = claimed
                self._run_active(run_id, submission)
            except BaseException as error:
                if run_id is not None:
                    issue = error.issues[0] if isinstance(error, TaskError) else _issue(
                        "task.output_write_failed" if isinstance(error, OSError) else "task.worker_failed",
                        "Unable to publish the next complete step." if isinstance(error, OSError) else "Task worker management failed.",
                        phase="publish" if isinstance(error, OSError) else "execute")
                    try:
                        self._finish(run_id, "failed", issue)
                    except TaskError:
                        self._unavailable = True
                if self._unavailable or run_id is None:
                    self._unavailable = True
                    return

    def prune_events(self, run_id, through):
        """Explicit pruning; clients can reconnect from the retained latest sequence."""
        with self._transaction():
            row = self._row(run_id)
            latest = canonical_loads(row["task"])["last_event_seq"]
            if type(through) is not int or not 0 <= through <= latest:
                raise TaskError(400, "task.cursor_invalid", "Invalid event retention cursor.")
            self._db.execute("DELETE FROM events WHERE run_id=? AND seq<=?", (run_id, through))
            self._db.execute("UPDATE tasks SET event_floor=MAX(event_floor,?) WHERE run_id=?", (through, run_id))

    def reap(self, now=None):
        """Expire terminal records only after both advertised minimum windows."""
        now = time.time() if now is None else now
        removed = []
        with self._transaction():
            self._db.execute("DELETE FROM tombstones WHERE expires<=?", (now,))
            rows = self._db.execute("SELECT run_id FROM tasks WHERE finished IS NOT NULL AND finished<=?",
                (now - max(self.limits.retention_seconds, self.limits.idempotency_retention_seconds),)).fetchall()
            for row in rows:
                run_id = row["run_id"]
                self._db.execute("INSERT OR REPLACE INTO tombstones VALUES(?,?)", (run_id, now + self.limits.idempotency_retention_seconds))
                self._db.execute("DELETE FROM tasks WHERE run_id=?", (run_id,))
                removed.append(run_id)
        for run_id in removed:
            folder = self.directory / "runs" / run_id
            if folder.exists():
                shutil.rmtree(folder)
        return removed

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=15)
        if self._thread.is_alive():
            raise RuntimeError("Task manager has not stopped; storage ownership remains held")
        try:
            for row in self._db.execute("SELECT run_id,task FROM tasks").fetchall():
                if canonical_loads(row["task"])["status"] not in TERMINAL:
                    self._finish(row["run_id"], "interrupted", _issue("task.service_interrupted", "Service closed before this task finished.", phase="recover"))
        finally:
            self._db.close()
            self._release_owner()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
