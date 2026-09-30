"""Real spawn-worker tests. All persistent files use TemporaryDirectory."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import hashlib
from importlib.resources import files
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from friskoli_cad.tasks import TaskService, TaskError, TaskLimits
from friskoli_cad.tasks import service as service_module
from friskoli_cad.protocol.task_validation import _validator, sha256, canonical_bytes, canonical_loads


def project():
    return json.loads(files("friskoli_cad").joinpath("examples", "workspace_3d.project.json").read_text(encoding="utf-8"))

def submission(service, *, steps=3, dt=0.01):
    doc = project()
    return {"task_contract_version": "0.1.0", "request_id": "request-1", "edit_revision": "edit-1",
        "project": doc, "version_lock": service.version_lock(doc),
        "execution": {"semantics": "legacy-explicit-v1", "backend": "numpy-cpu",
                      "dt_s": dt, "steps": steps, "seed": 0},
        "output_plan": {"frame_every_steps": 1, "observables": list(doc["run"]["channels"]), "include_fields": False}}

def until(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for the real task service")

def terminal(service, run_id):
    return until(lambda: (item := service.get(run_id)) if item_status(service, run_id) else None)

def item_status(service, run_id):
    return service.get(run_id)["status"] in ("completed", "failed", "cancelled", "interrupted")

def crash_host(directory, ready):
    svc = TaskService(directory)
    body = submission(svc, steps=10000, dt=0.000001)
    task, _ = svc.submit(body, "crash-key")
    until(lambda: svc.get(task["run_id"])["result"]["completeness"] == "partial")
    ready.put((task["run_id"], body))
    while True:
        time.sleep(1)

class TaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="friskoli-task-test-")
        self.service = TaskService(self.temp.name)
    def tearDown(self):
        self.service.close()
        self.temp.cleanup()
    def assert_error(self, status, operation):
        with self.assertRaises(TaskError) as caught:
            operation()
        self.assertEqual(caught.exception.status, status)
        _validator("Error").validate(caught.exception.to_dict())
        return caught.exception

    def test_real_complete_manifest_frames_and_provenance(self):
        body = submission(self.service)
        task, created = self.service.submit(body, "complete")
        self.assertTrue(created)
        _validator("TaskCapabilities").validate(self.service.capabilities())
        _validator("Task").validate(task)
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "completed", result["issues"])
        manifest = self.service.manifest(task["run_id"])
        _validator("Manifest").validate(manifest)
        self.assertEqual(manifest["progress"], result["progress"])
        self.assertEqual(manifest["completeness"], "complete")
        self.assertEqual(sha256(manifest["compiled_plan"]), result["input_snapshot"]["plan_sha256"])
        self.assertFalse(manifest["provenance"]["rng"]["used"])
        self.assertEqual(len(manifest["chunks"]), 4)
        for index, meta in enumerate(manifest["chunks"]):
            data = self.service.chunk(task["run_id"], meta["chunk_id"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"])
            self.assertEqual(len(data), meta["bytes"])
            chunk = json.loads(data)
            _validator("ChunkBody").validate(chunk)
            frame = chunk["frames"][0]
            self.assertEqual(frame["sequence"], index)
            self.assertEqual(frame["step_index"], index)
            self.assertEqual(frame["frame"]["run_id"], body["project"]["run"]["run_id"])
        after_cancel, accepted = self.service.cancel(task["run_id"])
        self.assertFalse(accepted)
        self.assertEqual(after_cancel, result)

    def test_concurrent_same_key_once_conflict_and_immutable_input(self):
        body = submission(self.service)
        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(lambda _: self.service.submit(deepcopy(body), "shared"), range(16)))
        self.assertEqual(sum(created for _, created in answers), 1)
        run_id = answers[0][0]["run_id"]
        self.assertEqual({task["run_id"] for task, _ in answers}, {run_id})
        changed = deepcopy(body)
        changed["request_id"] = "request-2"
        self.assertFalse(self.service.submit(changed, "shared")[1])
        changed["edit_revision"] = "edit-2"
        self.assert_error(409, lambda: self.service.submit(changed, "shared"))
        body["project"]["id"] = "edited-later"
        self.assertNotEqual(self.service.input(run_id)["project"]["id"], "edited-later")
        terminal(self.service, run_id)
        events = self.service.events(run_id)["events"]
        self.assertEqual(sum(event["type"] == "started" for event in events), 1)

    def test_queue_bounds_and_queued_running_cancel(self):
        self.service.limits = replace(self.service.limits, queued_runs=1)
        body = submission(self.service, steps=10000, dt=0.000001)
        first, _ = self.service.submit(body, "active")
        until(lambda: self.service.get(first["run_id"])["status"] == "running")
        second, _ = self.service.submit(body, "queued")
        self.assert_error(429, lambda: self.service.submit(body, "reusable"))
        cancelled, accepted = self.service.cancel(second["run_id"])
        self.assertFalse(accepted)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assert_error(409, lambda: self.service.manifest(second["run_id"]))
        queued_again, created = self.service.submit(body, "reusable")
        self.assertTrue(created)
        self.service.cancel(queued_again["run_id"])
        _, accepted = self.service.cancel(first["run_id"])
        self.assertTrue(accepted)
        repeat, _ = self.service.cancel(first["run_id"])
        cancelled = terminal(self.service, first["run_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertLessEqual(repeat["last_event_seq"], cancelled["last_event_seq"])
        self.assertEqual(self.service.manifest(first["run_id"])["completeness"], "partial")

    def test_event_paging_snapshot_and_cursor_expiry(self):
        task, _ = self.service.submit(submission(self.service), "events")
        result = terminal(self.service, task["run_id"])
        after, collected = 0, []
        while True:
            page = self.service.events(task["run_id"], after=after, limit=2)
            _validator("EventPage").validate(page)
            collected += page["events"]
            after = page["next_after"]
            if not page["has_more"]:
                break
        self.assertEqual([item["seq"] for item in collected], list(range(1, result["last_event_seq"] + 1)))
        for event in collected:
            self.assertEqual(event["seq"], event["task"]["last_event_seq"])
        self.assertEqual(self.service.events(task["run_id"], after)["next_after"], after)
        self.assert_error(400, lambda: self.service.events(task["run_id"], after + 1))
        self.service.prune_events(task["run_id"], 2)
        self.assert_error(410, lambda: self.service.events(task["run_id"], 0))
        self.assertEqual(self.service.events(task["run_id"], after)["events"], [])

    def test_static_guards_before_runtime_and_key_not_reserved(self):
        body = submission(self.service)
        invalid = deepcopy(body)
        invalid["project"]["domain"]["counts_xyz"] = [1000000, 1000000, 1000000]
        self.assert_error(413, lambda: self.service.submit(invalid, "retry"))
        invalid = deepcopy(body)
        invalid["output_plan"]["frame_every_steps"] = 2
        self.assert_error(422, lambda: self.service.submit(invalid, "retry"))
        invalid = deepcopy(body)
        invalid["output_plan"]["observables"] = ["absent"]
        self.assert_error(422, lambda: self.service.submit(invalid, "retry"))
        self.assert_error(400, lambda: self.service.submit(b'{"a":1,"a":2}', "retry"))
        self.assertTrue(self.service.submit(body, "retry")[1])

    def test_actual_cell_growth_fails_and_keeps_partial(self):
        self.service.limits = replace(self.service.limits, cells=8)
        task, _ = self.service.submit(submission(self.service, steps=2, dt=2), "growth")
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"][0]["code"], "task.resource_limit")
        self.assertEqual(result["progress"]["committed_step"], 0)
        self.assertEqual(result["result"]["completeness"], "partial")
        self.assertEqual(len(self.service.manifest(task["run_id"])["chunks"]), 1)

    def test_actual_rss_and_wall_time_limits(self):
        body = submission(self.service, steps=10000, dt=0.000001)
        self.service.limits = replace(self.service.limits, wall_time_s=1)
        task, _ = self.service.submit(body, "wall")
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("wall_time_s", result["issues"][0]["message"])
        self.service.limits = replace(self.service.limits, estimated_memory_bytes=1_000_000, wall_time_s=15)
        estimate = service_module.estimate
        def underestimated(*args):
            value = estimate(*args)
            value["memory_bytes"] = 1
            return value
        with patch.object(service_module, "estimate", underestimated):
            task, _ = self.service.submit(submission(self.service), "rss")
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("RSS", result["issues"][0]["message"])

    def test_output_failure_retains_published_partial_and_orphan_is_unqueryable(self):
        original = self.service._write_atomic
        calls = []
        def fail_after_first(path, body):
            calls.append(path)
            if len(calls) == 2:
                original(path, body)
                raise OSError("simulated failure after rename")
            return original(path, body)
        self.service._write_atomic = fail_after_first
        task, _ = self.service.submit(submission(self.service), "write-error")
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"][0]["code"], "task.output_write_failed")
        manifest = self.service.manifest(task["run_id"])
        self.assertEqual(len(manifest["chunks"]), 1)
        self.assertEqual(result["progress"]["committed_step"], 0)
        self.assertTrue(calls[1].exists())
        self.assert_error(404, lambda: self.service.chunk(task["run_id"], "chunk_00000001"))
        self.service.chunk(task["run_id"], "chunk_00000000")
        self.service.close()
        self.service = TaskService(self.temp.name)
        self.assertEqual(self.service.get(task["run_id"]), result)
        self.assertEqual(self.service.manifest(task["run_id"]), manifest)
        self.assert_error(404, lambda: self.service.chunk(task["run_id"], "chunk_00000001"))

    def test_disk_full_rejects_before_acceptance(self):
        original = service_module.shutil.disk_usage(self.temp.name)
        with patch.object(service_module.shutil, "disk_usage", return_value=original._replace(free=0)):
            self.assert_error(503, lambda: self.service.submit(submission(self.service), "disk"))
        task, created = self.service.submit(submission(self.service), "disk")
        self.assertTrue(created)
        self.assertEqual(terminal(self.service, task["run_id"])["status"], "completed")

    def test_actual_output_exceeds_underestimated_budget(self):
        self.service.limits = replace(self.service.limits, output_bytes=4000)
        estimate = service_module.estimate
        def low_estimate(*args):
            budget = estimate(*args)
            budget["output_bytes"] = 1
            return budget
        with patch.object(service_module, "estimate", low_estimate):
            task, _ = self.service.submit(submission(self.service), "output")
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"][0]["code"], "task.resource_limit")
        self.assertEqual(result["result"]["completeness"], "partial")
        self.assertEqual(len(self.service.manifest(task["run_id"])["chunks"]), 1)

    def test_worker_crash_is_failed_and_next_task_executes(self):
        task, _ = self.service.submit(submission(self.service, steps=10000, dt=0.000001), "crashed-worker")
        until(lambda: self.service.get(task["run_id"])["result"]["completeness"] == "partial")
        self.service._process.terminate()
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["issues"][0]["code"], "task.worker_failed")
        self.assertEqual(result["result"]["completeness"], "partial")
        next_task, _ = self.service.submit(submission(self.service), "after-crash")
        self.assertEqual(terminal(self.service, next_task["run_id"])["status"], "completed")

    def test_cancel_wins_before_final_publication_and_complete_wins_after(self):
        reached, proceed = threading.Event(), threading.Event()
        original = self.service._write_atomic
        def pause_final(path, body):
            if json.loads(body)["frames"][0]["step_index"] == 1:
                reached.set()
                if not proceed.wait(10):
                    raise OSError("test deadline")
            return original(path, body)
        self.service._write_atomic = pause_final
        task, _ = self.service.submit(submission(self.service, steps=1), "race")
        self.assertTrue(reached.wait(10))
        before = self.service.manifest(task["run_id"])
        self.assertEqual(before["completeness"], "partial")
        self.service.cancel(task["run_id"])
        proceed.set()
        cancelled = terminal(self.service, task["run_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.service.manifest(task["run_id"])["completeness"], "partial")
        self.service._write_atomic = original
        task, _ = self.service.submit(submission(self.service, steps=1), "winner")
        completed = terminal(self.service, task["run_id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.service.cancel(task["run_id"]), (completed, False))

    def test_real_host_crash_recovers_interrupted_and_preserves_partial(self):
        self.service.close()
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        host = context.Process(target=crash_host, args=(self.temp.name, ready))
        host.start()
        try:
            run_id, body = ready.get(timeout=15)
            host.terminate()
            host.join(timeout=5)
            self.service = TaskService(self.temp.name)
            result = self.service.get(run_id)
            self.assertEqual(result["status"], "interrupted")
            self.assertEqual(result["issues"][0]["code"], "task.service_interrupted")
            self.assertEqual(result["result"]["completeness"], "partial")
            manifest = self.service.manifest(run_id)
            self.assertEqual(manifest["status"], "interrupted")
            for chunk in manifest["chunks"]:
                self.service.chunk(run_id, chunk["chunk_id"])
            self.assertEqual(self.service.submit(body, "crash-key"), (result, False))
        finally:
            if host.is_alive():
                host.terminate()
                host.join(timeout=5)
            host.close()
            ready.close()

    def test_exclusive_owner_retention_tombstones_and_integrity(self):
        self.assert_error(503, lambda: TaskService(self.temp.name))
        task, _ = self.service.submit(submission(self.service), "retain")
        result = terminal(self.service, task["run_id"])
        before = time.time()
        self.assertEqual(self.service.reap(now=before + self.service.limits.retention_seconds - 1), [])
        manifest = self.service.manifest(task["run_id"])
        path = Path(self.temp.name) / "runs" / task["run_id"] / (manifest["chunks"][0]["chunk_id"] + ".json")
        path.write_bytes(b"corrupt")
        self.assert_error(503, lambda: self.service.chunk(task["run_id"], manifest["chunks"][0]["chunk_id"]))
        self.assertEqual(self.service.get(task["run_id"]), result)
        elapsed = before + self.service.limits.idempotency_retention_seconds + 1
        self.assertEqual(self.service.reap(now=elapsed), [task["run_id"]])
        self.assert_error(410, lambda: self.service.get(task["run_id"]))
        self.service.reap(now=elapsed + self.service.limits.idempotency_retention_seconds + 1)
        self.assert_error(404, lambda: self.service.get(task["run_id"]))

    def test_subsample_stable_population_and_float_integral_steps(self):
        body = submission(self.service, steps=5)
        body["project"]["graph"]["nodes"] = body["project"]["graph"]["nodes"][:1]
        body["project"]["graph"]["edges"] = []
        body["project"]["run"]["channels"] = {}
        body["output_plan"] = {"frame_every_steps": 2, "observables": [], "include_fields": False}
        body["version_lock"] = self.service.version_lock(body["project"])
        body["execution"]["steps"] = 5.0
        task, _ = self.service.submit(body, "sampled")
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "completed", result["issues"])
        manifest = self.service.manifest(task["run_id"])
        self.assertEqual([item["first_step"] for item in manifest["chunks"]], [0, 2, 4, 5])

    def test_large_binary64_values_survive_admission_worker_storage_and_restart(self):
        body = submission(self.service, steps=1, dt=1e16)
        body["project"]["graph"]["nodes"] = body["project"]["graph"]["nodes"][:1]
        body["project"]["graph"]["edges"] = []
        body["project"]["run"]["channels"] = {}
        body["output_plan"]["observables"] = []
        body["version_lock"] = self.service.version_lock(body["project"])
        task, created = self.service.submit(json.dumps(body), "large-float")
        self.assertTrue(created)
        result = terminal(self.service, task["run_id"])
        self.assertEqual(result["status"], "completed", result["issues"])
        self.assertEqual(result["progress"]["simulation_time_s"], 1e16)
        self.assertEqual(sha256(self.service.input(task["run_id"])), task["input_snapshot"]["document_sha256"])
        self.assertEqual(canonical_bytes(self.service.input(task["run_id"])), canonical_bytes(body))
        manifest = self.service.manifest(task["run_id"])
        data = self.service.chunk(task["run_id"], manifest["chunks"][-1]["chunk_id"])
        self.assertEqual(canonical_loads(data)["frames"][-1]["time_s"], 1e16)
        self.assertEqual(self.service.events(task["run_id"])["events"][-1]["task"], result)
        # Public strict input rejects unsafe integer tokens; trusted JCS restoration does not.
        self.assert_error(400, lambda: self.service.submit(canonical_bytes(body), "unsafe-token"))
        self.service.close()
        self.service = TaskService(self.temp.name)
        self.assertEqual(self.service.get(task["run_id"]), result)
        self.assertEqual(self.service.manifest(task["run_id"]), manifest)
        self.assertEqual(self.service.submit(json.dumps(body), "large-float"), (result, False))
