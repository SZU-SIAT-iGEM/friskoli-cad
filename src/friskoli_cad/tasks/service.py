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
from friskoli_cad.engine.profiles import LEGACY_PROFILE, PTS_PROFILE, SPATIAL_PROFILE, CHEMOTAXIS_PROFILE, profile_for_project, task_version
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.protocol.task_validation import (VERSION, TaskValidationError, canonical_bytes, canonical_loads, sha256, strict_json_loads, validate_submission)
from .metadata import BACKEND, compiled_plan, estimate, provenance, registry_metadata
from .worker import run_worker
from .artifacts import file_digest, final_field_estimate
from friskoli_cad.engine.field_backend import available_backends, memory_available_bytes

TERMINAL = frozenset(("completed", "failed", "cancelled", "interrupted", "paused"))
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
    steps: int = 4_320_000
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
        self._profile_metadata = {LEGACY_PROFILE: (self._registry, self._version_lock, self._sources),
                                  PTS_PROFILE: registry_metadata(PTS_PROFILE),
                                  SPATIAL_PROFILE: registry_metadata(SPATIAL_PROFILE),
                                  CHEMOTAXIS_PROFILE: registry_metadata(CHEMOTAXIS_PROFILE)}
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
                CREATE TABLE IF NOT EXISTS artifacts (run_id TEXT NOT NULL REFERENCES tasks(run_id) ON DELETE CASCADE,
                    artifact_id TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(run_id, artifact_id));
                CREATE TABLE IF NOT EXISTS continuation (run_id TEXT PRIMARY KEY, path TEXT NOT NULL);
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

    def _event(self, task, kind):
        task["last_event_seq"] += 1
        task["updated_at"] = _now()
        event = {"seq": task["last_event_seq"], "type": kind, "created_at": task["updated_at"], "task": task}
        finished = time.time() if task["status"] in TERMINAL else None
        self._db.execute("UPDATE tasks SET task=?, finished=COALESCE(finished,?) WHERE run_id=?",
                         (self._dump(task), finished, task["run_id"]))
        self._db.execute("INSERT INTO events VALUES(?,?,?)", (task["run_id"], event["seq"], self._dump(event)))

    def _ensure_profile(self, profile):
        if profile not in self._profile_metadata:
            try:
                self._profile_metadata[profile] = registry_metadata(profile)
            except (ProtocolError, ValueError, KeyError):
                raise TaskError(422, 'task.execution_unsupported', 'Unsupported execution profile.') from None

    def capabilities(self, profile=LEGACY_PROFILE, contract_version=None):
        self._ensure_profile(profile)
        modern = contract_version == '0.6.0' or profile == 'modular-spatial-v1'
        self._ensure_profile(profile)
        if profile not in self._profile_metadata:
            raise TaskError(422, "task.execution_unsupported", "Unsupported execution profile.", "/execution/semantics", phase="resolve")
        _, version_lock, _ = self._profile_metadata[profile]
        limits = asdict(self.limits)
        if not modern:
            limits["steps"] = min(limits["steps"], 10000)
        if profile in (SPATIAL_PROFILE, CHEMOTAXIS_PROFILE, 'modular-spatial-v1'):
            from friskoli_cad.engine.spatial_runtime import MAX_CELLS, MAX_VOXELS
            limits.update(cells=self.limits.cells if profile == 'modular-spatial-v1' else min(MAX_CELLS, self.limits.cells), voxels=min(MAX_VOXELS, self.limits.voxels))
        return {"task_contract_version": "0.6.0" if modern else task_version(profile), "mode": "single-worker",
            "task_contract_versions": list(dict.fromkeys([task_version(profile), "0.5.0", "0.6.0"])),
            "pause": modern, "resume": modern, "checkpoint": modern, "partial_results": True,
            "hash_canonicalization": "RFC8785", "limits": limits,
            "version_lock": canonical_loads(self._dump(version_lock)),
            "execution": {"semantics": profile, "backend": BACKEND, "available_backends": available_backends() if profile in (SPATIAL_PROFILE, CHEMOTAXIS_PROFILE, 'modular-spatial-v1') else [BACKEND], "default_seed": 0}}

    def version_lock(self, project=None):
        profile = LEGACY_PROFILE if project is None else profile_for_project(project)
        self._ensure_profile(profile)
        if profile not in self._profile_metadata:
            raise TaskError(422, "task.execution_unsupported", "Unsupported execution profile.", "/execution/semantics", phase="resolve")
        lock = canonical_loads(self._dump(registry_metadata(profile, project)[1] if project is not None and project.get("dependency_lock") else self._profile_metadata[profile][1]))
        if project is not None:
            used = {(node["module_id"], node["module_version"]) for node in project["graph"]["nodes"]}
            lock["implementations"] = [item for item in lock["implementations"] if (item["id"], item["version"]) in used]
        return lock

    def _admit(self, submission):
        project, execution = submission["project"], submission["execution"]
        profile = profile_for_project(project)
        self._ensure_profile(profile)
        registry, full, _ = registry_metadata(profile, project) if project.get("dependency_lock") else self._profile_metadata[profile]
        expected_version = task_version(profile)
        if (execution["semantics"] != profile or execution["backend"] not in (available_backends() if profile in (SPATIAL_PROFILE, CHEMOTAXIS_PROFILE, 'modular-spatial-v1') else [BACKEND])
                or submission["task_contract_version"] not in (expected_version, "0.5.0", "0.6.0")
                or (submission["task_contract_version"] not in ("0.5.0", "0.6.0") and execution["backend"] != BACKEND)):
            raise TaskError(422, "task.execution_unsupported", "Unsupported execution semantics or backend.", "/execution", phase="resolve")
        if submission['task_contract_version'] == '0.6.0' and profile not in (SPATIAL_PROFILE, CHEMOTAXIS_PROFILE, 'modular-spatial-v1'):
            raise TaskError(422, 'task.checkpoint_unsupported', 'Task 0.6 requires a complete checkpoint profile.')
        if submission['task_contract_version'] != '0.6.0' and (execution['steps'] > 10000 or submission['output_plan']['frame_every_steps'] > 10000):
            raise TaskError(413, 'task.resource_limit', 'Historical task contracts retain the 10000 step/output interval bound.')
        if not math.isfinite(execution["dt_s"] * execution["steps"]):
            raise TaskError(422, "task.time_overflow", "Simulation duration must be finite.", "/execution")
        provided, expected = submission["version_lock"], self.version_lock(project)
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
            validate_project(project, registry.manifests, registry=registry)
            plan = compiled_plan(project, registry, backend=execution["backend"])
        except ProtocolError as error:
            path = getattr(error, "path", "")
            raise TaskError(422, getattr(error, "code", "task.project_invalid"), str(error),
                            "/project" + (path if path != "/" else ""), phase="validate") from error
        strides = submission["output_plan"].get("field_stride_xyz", [1, 1, 1])
        if profile == 'modular-spatial-v1':
            cell_limit = project.get('system_limits',{}).get('max_cells',256)
            initial_cells = sum(len(group['ids']) for group in project['groups'].values())
            if cell_limit > self.limits.cells:
                raise TaskError(413,'task.resource_limit',f'Project max_cells {cell_limit} exceeds service cells {self.limits.cells}.',
                                '/project/system_limits/max_cells',phase='estimate')
            if initial_cells > cell_limit:
                raise TaskError(413,'task.resource_limit','Initial population exceeds project max_cells.',
                                '/project/system_limits/max_cells',phase='estimate')
        if any(count % stride for count, stride in zip(project["domain"]["counts_xyz"], strides)):
            raise TaskError(422, "task.field_stride", "Each field stride must divide its grid count.", "/output_plan/field_stride_xyz", phase="validate")
        unknown = set(submission["output_plan"]["observables"]) - set(project["run"]["channels"])
        if unknown:
            raise TaskError(422, "task.observable_unknown", "Output plan includes an unknown frame channel.", "/output_plan/observables", phase="validate")
        if profile not in (CHEMOTAXIS_PROFILE, 'modular-spatial-v1') and submission["output_plan"]["frame_every_steps"] != 1 and any("divide" in node["outputs"] for node in plan["nodes"]):
            raise TaskError(422, "task.sampling_unsupported", "Division models require every complete frame to preserve lineage events.", "/output_plan/frame_every_steps", phase="validate")
        budget = estimate(submission, registry)
        frame_count = 1 + execution["steps"] // submission["output_plan"]["frame_every_steps"]
        frame_count += int(execution["steps"] % submission["output_plan"]["frame_every_steps"] != 0)
        frame_bytes = (budget["output_bytes"] - final_field_estimate(submission)) // frame_count
        total_frame_bytes = frame_bytes
        if submission['task_contract_version'] == '0.6.0':
            metadata_submission = {**submission, 'output_plan':{**submission['output_plan'], 'include_fields':False, 'include_final_fields':False}}
            frame_bytes = estimate(metadata_submission, registry)['output_bytes'] // frame_count
        if frame_bytes > self.limits.chunk_bytes:
            hint = " Disable field output explicitly if fields are not needed." if submission["output_plan"]["include_fields"] else ""
            raise TaskError(413, "task.resource_limit",
                f"Estimated single-frame bytes {frame_bytes} exceed chunk_bytes {self.limits.chunk_bytes}. "
                "Increasing the output interval cannot reduce a single frame." + hint, "/output_plan", phase="estimate")
        for name, bound in (("cells", "cells"), ("voxels", "voxels"), ("steps", "steps"),
                            ("memory_bytes", "estimated_memory_bytes"), ("output_bytes", "output_bytes")):
            if budget[name] > getattr(self.limits, bound):
                hint = ""
                if name == "output_bytes":
                    if profile in (CHEMOTAXIS_PROFILE, 'modular-spatial-v1'):
                        available_frames = (self.limits.output_bytes - final_field_estimate(submission)) // max(1,total_frame_bytes)
                        minimum = math.ceil(execution['steps'] / (available_frames - 1)) if available_frames >= 2 else None
                        if minimum is not None and minimum > min(execution['steps'],self.limits.steps):
                            minimum = None
                        if minimum is not None:
                            hint = f" Set output frame_every_steps to at least {minimum}; numerical dt_s and steps stay unchanged."
                    if submission["output_plan"]["include_fields"]:
                        hint += " Field output may be disabled explicitly if those results are not needed."
                raise TaskError(413, "task.resource_limit",
                    f"Estimated {name} {budget[name]} exceeds {bound} {getattr(self.limits, bound)}." + hint,
                    "/output_plan" if name == "output_bytes" else "/execution" if name == "steps" else "/project", phase="estimate")
        device_free = memory_available_bytes(execution["backend"])
        if device_free is not None and budget["voxels"] * 17 + 64 * 1024 * 1024 > device_free:
            raise TaskError(413, "task.resource_unavailable", "Estimated diffusion buffers exceed currently available device memory.", "/execution/backend", phase="estimate")
        if budget["memory_bytes"] > psutil.virtual_memory().available:
            raise TaskError(413, "task.resource_unavailable", "Estimated worker memory exceeds currently available host RAM.", "/project", phase="estimate")
        if budget["output_bytes"] + len(canonical_bytes(submission)) * 2 + 1024 * 1024 > shutil.disk_usage(self.directory).free:
            raise TaskError(503, "task.storage_unavailable", "Insufficient free storage for estimated task output.", "/output_plan", phase="estimate")
        return plan, budget

    def _validated_submission(self, submission):
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
        return submission, data

    def preflight(self, submission):
        """Check the exact static admission rules without reserving a task or key.

        Queue/storage availability and actual runtime limits are checked at
        submission/execution; this result is not an execution guarantee.
        """
        submission, _ = self._validated_submission(submission)
        from friskoli_cad.diagnostics import diagnose_project
        from friskoli_cad.engine.profiles import registry_for_project
        diagnostics = diagnose_project(submission['project'], registry=registry_for_project(submission['project']), backend=submission['execution']['backend'])
        if not diagnostics['valid']:
            issues = [{**item, 'path':'/project'+item.get('path','')} for item in diagnostics['issues']]
            raise TaskError(422, 'task.project_invalid', 'Project diagnostics failed.', issues=issues)
        _, budget = self._admit(submission)
        for check in diagnostics['checks']:
            if check.get('id') == 'resources': check['status'] = 'pass'
        steps = submission["execution"]["steps"]
        stride = submission["output_plan"]["frame_every_steps"]
        profile = profile_for_project(submission["project"])
        return {"valid": True, "issues": [], "checks":diagnostics["checks"], "estimate": budget,
                "frames": 1 + (steps + stride - 1) // stride,
                "cells": budget["cells"], "voxels": budget["voxels"],
                "limits": self.capabilities(profile,submission["task_contract_version"])["limits"]}

    def submit(self, submission, key, *, _resume=None):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise TaskError(400, "task.idempotency_key", "A valid Idempotency-Key is required.")
        if self._closed or self._unavailable:
            raise TaskError(503, "task.storage_unavailable", "The task service cannot accept new work.")
        submission, data = self._validated_submission(submission)
        digest = sha256({**{name: value for name, value in submission.items() if name != "request_id"}, **({"continuation": _resume[1]} if _resume else {})})
        with self._lock:
            existing = self._db.execute("SELECT * FROM idempotency WHERE key=?", (key,)).fetchone()
            if existing:
                return self._same(existing, digest), False
        plan, budget = self._admit(submission)
        metadata = provenance(submission["execution"]["seed"], self._sources, submission["project"], submission["execution"]["backend"])
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
                "plan_sha256": sha256(plan), "registry_sha256": self.version_lock(submission["project"])["registry_sha256"],
                "canonicalization": "RFC8785", "edit_revision": submission["edit_revision"]}
            task = {"task_contract_version": submission["task_contract_version"], "run_id": run_id, "status": "queued",
                "created_at": stamp, "updated_at": stamp, "finished_at": None, "cancel_requested": False,
                "input_snapshot": snapshot, "estimate": budget,
                "progress": {"committed_step": 0, "simulation_time_s": 0}, "last_event_seq": 0,
                "result": {"href": f"/api/runs/{run_id}/result", "completeness": "none"}, "issues": []}
            if submission['task_contract_version'] == '0.6.0':
                task.update(pause_requested=False, parent_run_id=None, start_step=0, checkpoint=None)
            if _resume:
                source, relation = _resume
                folder = self.directory / 'runs' / run_id
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / 'resume.zip'
                shutil.copyfile(source, target)
                task.update(parent_run_id=relation.get('parent_run_id'), start_step=relation['step_index'])
                if 'migration_audit' in relation:
                    task['migration_audit'] = relation['migration_audit']
                task['progress'] = {'committed_step':relation['step_index'], 'simulation_time_s':relation['time_s']}
                self._db.execute('INSERT INTO continuation VALUES(?,?)', (run_id, str(target)))
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

    def manifest(self, run_id, *, offset=0, limit=1024):
        with self._lock:
            row = self._row(run_id)
            task = canonical_loads(row['task'])
            modern = task['task_contract_version'] == '0.6.0'
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1024:
                raise TaskError(400,'task.manifest_cursor','Manifest requires offset>=0 and limit in [1,1024].')
            total = self._db.execute('SELECT COUNT(*) FROM chunks WHERE run_id=?',(run_id,)).fetchone()[0]
            records = self._db.execute('SELECT metadata FROM chunks WHERE run_id=? ORDER BY chunk_id LIMIT ? OFFSET ?',
                (run_id,limit if modern else -1,offset if modern else 0)).fetchall()
            if not total:
                raise TaskError(409, "task.result_unavailable", "No complete frame has been published.")
            # Read one committed snapshot under the same lock as publication.
            # The indexed chunks are authoritative for old and new databases;
            # never repeatedly serialize their entire history on each step.
            task = canonical_loads(row["task"])
            return {"task_contract_version": task["task_contract_version"], "run_id": run_id,
                "input_snapshot": task["input_snapshot"], "chunks": [canonical_loads(item[0]) for item in records],
                "compiled_plan": canonical_loads(row["plan"]), "provenance": canonical_loads(row["provenance"]),
                "status": task["status"], "completeness": task["result"]["completeness"],
                "progress": task["progress"], "issues": task["issues"],
                **({'migration_audit':task['migration_audit']} if 'migration_audit' in task else {}),
                **({"artifacts": [canonical_loads(item[0]) for item in self._db.execute(
                    "SELECT metadata FROM artifacts WHERE run_id=? AND artifact_id IN ('checkpoint','final_fields') ORDER BY artifact_id", (run_id,)).fetchall()]}
                   if task["task_contract_version"] in ("0.5.0", "0.6.0") else {}),
                **({"parent_run_id":task.get("parent_run_id"), "start_step":task.get("start_step",0), "chunk_offset":offset, "total_chunks":total, "next_chunk_offset":offset+len(records) if offset+len(records)<total else None} if modern else {})}

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

    def artifact(self, run_id, artifact_id):
        """Return a verified file path for streaming; unpublished files are inaccessible."""
        with self._lock:
            self._row(run_id)
            if artifact_id not in ("final_fields", "checkpoint") and not (isinstance(artifact_id, str) and re.fullmatch(r"array_[0-9_]+", artifact_id)):
                raise TaskError(404, "task.not_found", "Unknown artifact.")
            row = self._db.execute("SELECT metadata FROM artifacts WHERE run_id=? AND artifact_id=?", (run_id, artifact_id)).fetchone()
            if row is None:
                raise TaskError(404, "task.not_found", "Unknown or unpublished artifact.")
            metadata = canonical_loads(row[0])
            path = self.directory / "runs" / run_id / ("final-fields.npz" if artifact_id == "final_fields" else metadata["filename"])
            try:
                size, digest = file_digest(path)
            except OSError as error:
                raise TaskError(503, "task.output_corrupt", "Published artifact is unavailable.", phase="publish") from error
            if size != metadata["bytes"] or digest != metadata["sha256"]:
                raise TaskError(503, "task.output_corrupt", "Published artifact failed integrity verification.", phase="publish")
            return path, metadata

    def pause(self, run_id):
        with self._transaction():
            task = canonical_loads(self._row(run_id)['task'])
            if task['task_contract_version'] != '0.6.0':
                raise TaskError(409, 'task.pause_unsupported', 'Pause requires Task 0.6.')
            if task['status'] in TERMINAL:
                return task, False
            task['pause_requested'] = True
            self._event(task, 'pause_requested')
        return task, True

    def resume(self, run_id, request, key):
        if type(request) is not dict or set(request) != {'request_id', 'edit_revision'}:
            raise TaskError(422, 'task.resume_request', 'Resume needs request_id and edit_revision.')
        with self._lock:
            task = canonical_loads(self._row(run_id)['task'])
            if task['status'] not in ('paused', 'interrupted', 'failed', 'cancelled'):
                raise TaskError(409, 'task.resume_state', 'Only a stopped run can be continued.')
            source, metadata = self.artifact(run_id, 'checkpoint')
            submission = canonical_loads(self._row(run_id)['input'])
        if metadata['step_index'] >= submission['execution']['steps']:
            raise TaskError(409, 'task.resume_complete', 'The checkpoint already reached the target step.')
        from friskoli_cad.engine.task_checkpoint import load_task_checkpoint
        try:
            simulation = load_task_checkpoint(source, maximum=self.limits.estimated_memory_bytes)
        except (ValueError, OSError) as error:
            raise TaskError(422, 'task.checkpoint_invalid', str(error)) from error
        if simulation.project != submission['project']:
            raise TaskError(422, 'task.checkpoint_project', 'Checkpoint project differs from frozen input.')
        submission.update(request)
        return self.submit(submission, key, _resume=(source, {'parent_run_id':run_id,
            'step_index':metadata['step_index'], 'time_s':metadata['time_s'], 'sha256':metadata['sha256']}))

    def import_checkpoint(self, source, submission, key):
        """Explicit import; validate the complete state before reserving the child run."""
        from friskoli_cad.engine.task_checkpoint import load_task_checkpoint
        try:
            simulation = load_task_checkpoint(source, maximum=self.limits.estimated_memory_bytes)
        except (ValueError, OSError) as error:
            raise TaskError(422, 'task.checkpoint_invalid', str(error)) from error
        if submission.get('task_contract_version') != '0.6.0' or submission.get('project') != simulation.project:
            raise TaskError(422, 'task.checkpoint_project', 'Import requires Task 0.6 and the exact checkpoint project.')
        if submission['execution']['steps'] <= simulation.frame_index or submission['execution']['seed'] != simulation.seed:
            raise TaskError(422, 'task.checkpoint_execution', 'Target must follow checkpoint and seed must match.')
        size, digest = file_digest(source)
        return self.submit(submission, key, _resume=(source, {'parent_run_id':None,
            'step_index':simulation.frame_index, 'time_s':simulation.time_s, 'sha256':digest}))

    def migrate(self, run_id, request, key=None, *, preview=True):
        from friskoli_cad.engine.task_checkpoint import load_task_checkpoint, save_task_checkpoint
        from friskoli_cad.engine.task_migration import migrate_simulation
        import tempfile
        required = {'project','mapping'} | (set() if preview else {'request_id','edit_revision','preview_sha256'})
        if type(request) is not dict or set(request) != required:
            raise TaskError(422, 'task.migration_request', 'Migration requires project/mapping and execution needs the approved preview hash plus request IDs.')
        with self._lock:
            task = canonical_loads(self._row(run_id)['task'])
            if task['status'] not in ('paused','interrupted','failed','cancelled'):
                raise TaskError(409, 'task.migration_state', 'Pause the run at a saved boundary before migration.')
            source, checkpoint = self.artifact(run_id,'checkpoint')
            submission = canonical_loads(self._row(run_id)['input'])
        try:
            submission['project'] = request['project']
            submission['version_lock'] = self.version_lock(request['project'])
            self.preflight(submission)
            simulation, document = load_task_checkpoint(source, maximum=self.limits.estimated_memory_bytes, return_document=True)
            candidate, audit = migrate_simulation(simulation,request['project'],request['mapping'])
            token = sha256({'checkpoint':checkpoint['sha256'],'project':request['project'],'mapping':request['mapping'],'audit':audit})
            if preview:
                return {'valid':True,'preview_sha256':token,'audit':audit,'source_run_id':run_id}
            if request['preview_sha256'] != token:
                raise ValueError('Migration preview no longer matches the saved boundary or requested mapping')
            submission.update({k:request[k] for k in ('request_id','edit_revision')})
            with tempfile.TemporaryDirectory(dir=self.directory, prefix='.migration-') as directory:
                path = Path(directory)/'state.zip'
                metadata = save_task_checkpoint(candidate,path,maximum=self.limits.estimated_memory_bytes,
                    task_context={**document.get('task_context',{}),'migration':audit,'source_run_id':run_id})
                child, created = self.submit(submission,key,_resume=(path,{'parent_run_id':run_id,
                    'step_index':candidate.frame_index,'time_s':candidate.time_s,'sha256':metadata['sha256'],'migration_audit':audit}))
            return {'task':child,'created':created,'audit':audit}
        except TaskError:
            raise
        except (ValueError,OSError,ImportError) as error:
            raise TaskError(422,'task.migration_rejected',str(error)) from error

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
            self._event(task, "cancel_requested")
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
            self._event(task, status)
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

        for row in self._db.execute("SELECT run_id,artifact_id FROM artifacts").fetchall():
            try:
                self.artifact(row["run_id"], row["artifact_id"])
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
            count, output_bytes = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(json_extract(metadata,'$.bytes')),0) FROM chunks WHERE run_id=?",
                (run_id,)).fetchone()
            contract_version = canonical_loads(row["task"])["task_contract_version"]
        with self._lock:
            output_bytes += self._db.execute("SELECT COALESCE(SUM(json_extract(metadata,'$.bytes')),0) FROM artifacts WHERE run_id=? AND artifact_id!='checkpoint'", (run_id,)).fetchone()[0]
        array_artifacts = []
        for field in message.get('concentrations', {}).values():
            if 'array' not in field:
                continue
            from .arrays import validate_descriptor
            validate_descriptor(field['array'], self.limits.estimated_memory_bytes)
            for segment in field['array']['segments']:
                path = self.directory / 'runs' / run_id / segment['name']
                size, digest = file_digest(path)
                if size != segment['bytes'] or digest != segment['sha256'] or size > self.limits.chunk_bytes:
                    raise TaskError(500, 'task.array_corrupt', 'Array segment size/hash differs.')
                aid = segment['name'][:-4]
                segment['href'] = f'/api/runs/{run_id}/artifacts/{aid}'
                array_artifacts.append((aid, {**segment, 'filename':segment['name'], 'media_type':'application/octet-stream'}))
        array_bytes = sum(meta['bytes'] for _, meta in array_artifacts)
        if output_bytes + array_bytes > self.limits.output_bytes:
            raise TaskError(413, 'task.resource_limit', 'Array output exceeds output_bytes.')
        output_bytes += array_bytes
        metadata = None
        checkpoint = message.get('checkpoint_artifact')
        checkpoint_path = None
        if checkpoint:
            pending_checkpoint = self.directory / 'runs' / run_id / 'checkpoint.pending.zip'
            size, digest = file_digest(pending_checkpoint)
            if size != checkpoint['bytes'] or digest != checkpoint['sha256'] or checkpoint['step_index'] != message['step']:
                raise TaskError(500, 'task.checkpoint_corrupt', 'Checkpoint integrity verification failed.')
            filename = f'checkpoint_{message["step"]:010d}_{digest[:12]}.zip'
            checkpoint_path = pending_checkpoint.with_name(filename)
            os.replace(pending_checkpoint, checkpoint_path)
            checkpoint = {**checkpoint, 'filename':filename, 'href':f'/api/runs/{run_id}/checkpoint'}
        artifact = message.get("final_field_artifact")
        artifact_bytes = 0
        if artifact is not None:
            submission = canonical_loads(row["input"])
            if not message["final"] or contract_version not in ("0.5.0", "0.6.0") or not submission["output_plan"].get("include_final_fields", False):
                raise TaskError(500, "task.output_corrupt", "Unexpected final field artifact.", phase="publish")
            pending = self.directory / "runs" / run_id / "final-fields.pending.npz"
            artifact_bytes, digest = file_digest(pending)
            if artifact_bytes != artifact["bytes"] or digest != artifact["sha256"] or artifact["step_index"] != message["step"]:
                raise TaskError(500, "task.output_corrupt", "Final field artifact failed integrity verification.", phase="publish")
            if output_bytes + artifact_bytes > self.limits.output_bytes:
                raise TaskError(413, "task.resource_limit", "Final field artifact exceeds output_bytes.", phase="publish")
            artifact = {**artifact, "href": f"/api/runs/{run_id}/artifacts/final_fields"}
        elif message["final"] and canonical_loads(row["input"])["output_plan"].get("include_final_fields", False):
            raise TaskError(500, "task.output_corrupt", "Requested final field artifact is missing.", phase="publish")
        if "frame" in message:
            sequence = count
            chunk_id = f"chunk_{sequence:08d}"
            body = canonical_bytes({"task_contract_version": contract_version, "run_id": run_id,
                "chunk_id": chunk_id, "frames": [{"sequence": sequence, "step_index": message["step"],
                    "time_s": message["time_s"], "grid_revision": message["grid_revision"], "frame": message["frame"],
                    **({"concentrations": message["concentrations"]} if "concentrations" in message else {}),
                    **({"object_states": message["object_states"]} if "object_states" in message else {}),
                    **({"metrics": message["metrics"]} if "metrics" in message else {}),
                    **({"lifecycle_details": message["lifecycle_details"]} if "lifecycle_details" in message else {})}]})
            if len(body) > self.limits.chunk_bytes or output_bytes + len(body) + artifact_bytes > self.limits.output_bytes:
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
            for aid, meta in array_artifacts:
                self._db.execute('INSERT INTO artifacts VALUES(?,?,?)', (run_id, aid, self._dump(meta)))
            if checkpoint:
                self._db.execute('INSERT OR REPLACE INTO artifacts VALUES(?,?,?)', (run_id, 'checkpoint', self._dump(checkpoint)))
                self._db.execute('INSERT OR REPLACE INTO continuation VALUES(?,?)', (run_id, str(checkpoint_path)))
                task['checkpoint'] = checkpoint
            if metadata:
                self._db.execute("INSERT INTO chunks VALUES(?,?,?)", (run_id, metadata["chunk_id"], self._dump(metadata)))
                count += 1
            if artifact is not None and not task["cancel_requested"]:
                os.replace(self.directory / "runs" / run_id / "final-fields.pending.npz",
                           self.directory / "runs" / run_id / "final-fields.npz")
                self._db.execute("INSERT INTO artifacts VALUES(?,?,?)", (run_id, "final_fields", self._dump(artifact)))
            task["progress"] = {"committed_step": message["step"], "simulation_time_s": message["time_s"]}
            if count:
                task["result"]["completeness"] = "partial"
            if task["cancel_requested"]:
                task["status"], task["finished_at"] = "cancelled", _now()
            elif message.get('paused'):
                task['status'], task['finished_at'] = 'paused', _now()
            elif message.get('rotate'):
                task['status'] = 'queued'
            elif message["final"]:
                task["status"], task["finished_at"] = "completed", _now()
                task["result"]["completeness"] = "complete"
            self._event(task, "progress" if task["status"] == "running" else task["status"])
            return task["status"]

    def _run_active(self, run_id, submission):
        (self.directory / "runs" / run_id).mkdir(parents=True, exist_ok=True)
        # One bounded data message then one durable-commit acknowledgement.
        # No queue can accumulate frames if disk publication is slower than compute.
        parent_channel, worker_channel = self._ctx.Pipe(duplex=True)
        with self._lock:
            continuation = self._db.execute('SELECT path FROM continuation WHERE run_id=?', (run_id,)).fetchone()
        process = self._ctx.Process(target=run_worker, args=(submission, worker_channel,
                                    asdict(self.limits), self._sources, str(self.directory / "runs" / run_id / "final-fields.pending.npz"), continuation[0] if continuation else None), name="friskoli-numerical-worker", daemon=True)
        self._process = process
        started = time.monotonic()
        try:
            process.start()
            # Only the child owns its endpoint, so a killed worker exposes EOF.
            worker_channel.close()
            observed = psutil.Process(process.pid)
            while not self._stop.is_set():
                if time.monotonic() - started > self.limits.wall_time_s:
                    raise TaskError(413, "task.resource_limit", "Worker exceeded wall_time_s.", phase="execute")
                try:
                    if observed.memory_info().rss > self.limits.estimated_memory_bytes:
                        raise TaskError(413, "task.resource_limit", "Worker RSS exceeded the published memory budget.", phase="execute")
                except psutil.NoSuchProcess:
                    pass
                try:
                    message_ready = parent_channel.poll(0.01)
                except (EOFError, OSError) as error:
                    raise TaskError(500, "task.worker_failed", "Numerical worker disconnected before publishing its next step.", phase="execute") from error
                if message_ready:
                    try:
                        raw = parent_channel.recv_bytes(self.limits.chunk_bytes + 4096)
                    except (EOFError, OSError) as error:
                        raise TaskError(500, "task.worker_failed", "Numerical worker disconnected before publishing its next step.", phase="execute") from error
                    message = canonical_loads(raw)
                    if message["kind"] == "error":
                        self._finish(run_id, "failed", _issue(message["code"], message["message"], path=message.get("path", ""), phase=message["phase"]))
                        return
                    published = self._publish(run_id, message)
                    if message.get('checkpoint_artifact'):
                        current = self.get(run_id).get('checkpoint')
                        if current:
                            for old in (self.directory / 'runs' / run_id).glob('checkpoint_*.zip'):
                                if old.name != current['filename']:
                                    try:
                                        old.unlink(missing_ok=True)
                                    except OSError:
                                        pass  # An in-flight download may hold the old file on Windows.
                    if published in TERMINAL | {'queued'}:
                        return
                    try:
                        # At most one byte can be outstanding: the child must
                        # consume it before it can send the next bounded message.
                        # No process-shared condition lock can be poisoned by
                        # terminating the child while it waits for this ack.
                        parent_channel.send_bytes(b'p' if self.get(run_id).get('pause_requested') else b'\x01')
                    except (EOFError, OSError) as error:
                        raise TaskError(500, "task.worker_failed",
                            "Numerical worker disconnected before acknowledging its committed step.", phase="execute") from error
                elif not process.is_alive():
                    raise TaskError(500, "task.worker_failed", "Numerical worker exited before publishing its final step.", phase="execute")
            self._finish(run_id, "interrupted", _issue("task.service_interrupted", "Service closed before this task finished.", phase="recover"))
        finally:
            worker_channel.close()
            parent_channel.close()
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)
                process.close()
            self._process = None
            pending = self.directory / "runs" / run_id / "final-fields.pending.npz"
            pending.unlink(missing_ok=True)
            run_directory = (self.directory / 'runs' / run_id).resolve()
            for temporary in run_directory.glob('.checkpoint-*'):
                if temporary.is_dir() and temporary.resolve().parent == run_directory:
                    shutil.rmtree(temporary, ignore_errors=True)
            with self._lock:
                published_files = {canonical_loads(row[0]).get('filename') for row in self._db.execute(
                    'SELECT metadata FROM artifacts WHERE run_id=?',(run_id,)).fetchall()}
            for orphan in run_directory.glob('array_*.bin'):
                if orphan.name not in published_files:
                    orphan.unlink(missing_ok=True)
            (run_directory/'checkpoint.pending.zip').unlink(missing_ok=True)

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
                if self._db.execute("SELECT 1 FROM tasks WHERE json_extract(task,'$.parent_run_id')=? LIMIT 1",(run_id,)).fetchone():
                    continue
                self._db.execute('DELETE FROM continuation WHERE run_id=?',(run_id,))
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
