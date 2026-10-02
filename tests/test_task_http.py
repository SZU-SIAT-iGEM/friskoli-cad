"""Real HTTP admission, immutable results and recovery transport checks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from friskoli_cad.replay_service import EXAMPLE_PROJECT, ReplayServer
from friskoli_cad.protocol.task_validation import _validator
from friskoli_cad.engine.chemotaxis_templates import make_example


class TaskHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="friskoli-task-http-")
        self.server = ReplayServer(("127.0.0.1", 0), task_directory=Path(self.temp.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.temp.cleanup()

    def request(self, path, value=None, *, raw=None, key=None, method=None, headers=None):
        headers = dict(headers or {})
        if value is not None:
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if raw is not None:
            headers.setdefault("Content-Type", "application/json")
        if key is not None:
            headers["Idempotency-Key"] = key
        request = Request(self.root + path, data=raw, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            data = response.read()
            return response.status, dict(response.headers), json.loads(data)

    def submission(self, *, steps=3):
        status, _, caps = self.request("/api/capabilities")
        self.assertEqual(status, 200)
        self.assertEqual(caps["api_version"], "0.2.0")
        task = caps["task"]
        self.assertEqual(task["task_contract_version"], "0.1.0")
        _validator("TaskCapabilities").validate(task)
        project = json.loads(EXAMPLE_PROJECT.read_text(encoding="utf-8"))
        execution = task["execution"]
        return {"task_contract_version": "0.1.0", "request_id": "http-test",
                "project": project, "version_lock": task["version_lock"],
                "execution": {"semantics": execution["semantics"],
                              "backend": execution["backend"], "seed": execution["default_seed"],
                              "dt_s": .1, "steps": steps},
                "output_plan": {"frame_every_steps": 1, "observables": [], "include_fields": False},
                "edit_revision": "http-edit-1"}

    def terminal(self, run_id):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, _, task = self.request(f"/api/runs/{run_id}")
            self.assertEqual(status, 200)
            if task["status"] not in ("queued", "running"):
                return task
            time.sleep(.03)
        self.fail("task did not reach a terminal state")

    def scientific_submission(self, name, *, stride=1):
        project = make_example(name)
        return {"task_contract_version": "0.4.0", "request_id": "preflight-science",
                "edit_revision": "preflight:1", "project": project,
                "version_lock": self.server.task_service.version_lock(project),
                "execution": {"semantics": "chemotaxis-spatial-v1", "backend": "numpy-cpu",
                              "seed": 17, "dt_s": .05, "steps": 1900},
                "output_plan": {"frame_every_steps": stride, "observables": list(project["run"]["channels"]),
                                "include_fields": True}}

    def test_preflight_has_no_task_or_idempotency_side_effects(self):
        submission = self.submission(steps=1)
        before = deepcopy(submission)
        _, _, caps = self.request("/api/capabilities")
        self.assertEqual(caps["task_preflight"], {"href": "/api/runs/preflight", "method": "POST"})
        for _ in range(2):
            code, _, result = self.request("/api/runs/preflight", submission, key="unused-preflight-key")
            self.assertEqual(code, 200)
            self.assertTrue(result["valid"])
            self.assertEqual(result["frames"], 2)
        self.assertEqual(submission, before)
        service = self.server.task_service
        with service._lock:
            self.assertEqual(service._db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
            self.assertEqual(service._db.execute("SELECT COUNT(*) FROM idempotency").fetchone()[0], 0)
        submission["request_id"] = "actual-submit"
        code, _, task = self.request("/api/runs", submission, key="unused-preflight-key")
        self.assertEqual(code, 202)
        self.assertEqual(task["estimate"], result["estimate"])
        self.assertEqual(self.terminal(task["run_id"])["status"], "completed")

    def test_preflight_and_submit_reject_same_1900_lifecycle_budget(self):
        submission = self.scientific_submission("chemotaxis-lifecycle")
        code, _, check = self.request("/api/runs/preflight", submission)
        self.assertEqual(code, 413)
        issue = check["issues"][0]
        self.assertEqual(issue["path"], "/output_plan")
        self.assertIn("output_bytes", issue["message"])
        self.assertIn("at least 9", issue["message"])
        code, _, rejected = self.request("/api/runs", submission, key="budget-rejected")
        self.assertEqual(code, 413)
        self.assertEqual(check, rejected)
        execution = deepcopy(submission["execution"])
        submission["output_plan"]["frame_every_steps"] = 9
        code, _, accepted = self.request("/api/runs/preflight", submission)
        self.assertEqual(code, 200)
        self.assertLessEqual(accepted["estimate"]["output_bytes"], accepted["limits"]["output_bytes"])
        self.assertEqual(submission["execution"], execution)

    def test_preflight_fine_grid_uses_async_limits_and_real_output_plan(self):
        submission = self.scientific_submission("chemotaxis-mcp", stride=10)
        submission["project"]["domain"].update(counts_xyz=[100, 100, 1], spacing_um_xyz=[2, 1, 2])
        before = deepcopy(submission)
        code, _, result = self.request("/api/runs/preflight", submission)
        self.assertEqual(code, 200)
        self.assertEqual(result["voxels"], 10000)
        self.assertEqual(result["frames"], 191)
        self.assertEqual(result["estimate"]["output_bytes"], 125518706)
        self.assertEqual(submission, before)
        submission["output_plan"]["frame_every_steps"] = 1
        code, _, result = self.request("/api/runs/preflight", submission)
        self.assertEqual(code, 413)
        self.assertIn("output_bytes", result["issues"][0]["message"])

    def test_preflight_single_frame_limit_does_not_recommend_sparse_output(self):
        from dataclasses import replace
        service = self.server.task_service
        service.limits = replace(service.limits, chunk_bytes=1000)
        for stride in (1, 1900):
            code, _, result = self.request("/api/runs/preflight", self.scientific_submission("chemotaxis-mcp", stride=stride))
            self.assertEqual(code, 413)
            message = result["issues"][0]["message"]
            self.assertIn("single-frame bytes", message)
            self.assertIn("cannot reduce a single frame", message)
            self.assertNotIn("at least", message)

    def test_real_submission_events_input_and_checksummed_chunks(self):
        submission = self.submission()
        frozen = deepcopy(submission)
        status, headers, task = self.request("/api/runs", submission, key="roundtrip")
        self.assertEqual(status, 202)
        _validator("Task").validate(task)
        run_id = task["run_id"]
        self.assertEqual(headers["Location"], f"/api/runs/{run_id}")
        self.assertNotEqual(run_id, frozen["project"]["run"]["run_id"])
        submission["project"]["id"] = "edited-after-submit"
        code, _, stored = self.request(f"/api/runs/{run_id}/input")
        self.assertEqual(code, 200)
        self.assertEqual(stored, frozen)
        _validator("Submission").validate(stored)
        completed = self.terminal(run_id)
        self.assertEqual(completed["status"], "completed", completed)
        _validator("Task").validate(completed)
        self.assertEqual(completed["result"]["completeness"], "complete")
        self.assertEqual(completed["progress"]["committed_step"], 3)
        after, events = 0, []
        while True:
            code, _, page = self.request(f"/api/runs/{run_id}/events?after={after}&limit=2")
            self.assertEqual(code, 200)
            _validator("EventPage").validate(page)
            events.extend(page["events"])
            after = page["next_after"]
            if not page["has_more"]:
                break
        self.assertEqual([item["seq"] for item in events], list(range(1, after + 1)))
        self.assertTrue(all(item["task"]["last_event_seq"] == item["seq"] for item in events))
        self.assertEqual(after, completed["last_event_seq"])
        _, _, empty = self.request(f"/api/runs/{run_id}/events?after={after}")
        self.assertEqual(empty["next_after"], after)
        self.assertEqual(empty["events"], [])
        code, _, manifest = self.request(f"/api/runs/{run_id}/result")
        self.assertEqual(code, 200)
        self.assertEqual(manifest["completeness"], "complete")
        self.assertEqual(manifest["run_id"], run_id)
        _validator("Manifest").validate(manifest)
        frames = []
        for chunk in manifest["chunks"]:
            with urlopen(self.root + chunk["href"], timeout=10) as response:
                raw = response.read()
            self.assertEqual(len(raw), chunk["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), chunk["sha256"])
            data = json.loads(raw)
            _validator("ChunkBody").validate(data)
            self.assertEqual(data["run_id"], run_id)
            frames.extend(data["frames"])
        self.assertEqual([item["step_index"] for item in frames], [0, 1, 2, 3])
        self.assertEqual([item["sequence"] for item in frames], [0, 1, 2, 3])
        self.assertTrue(all(item["frame"]["run_id"] == frozen["project"]["run"]["run_id"] for item in frames))

    def test_concurrent_idempotency_and_conflict(self):
        submission = self.submission(steps=1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.request("/api/runs", submission, key="concurrent"), range(4)))
        self.assertEqual(sorted(row[0] for row in responses), [200, 200, 200, 202])
        self.assertEqual(len({row[2]["run_id"] for row in responses}), 1)
        changed = deepcopy(submission)
        changed["request_id"] = "retry-request-id"
        code, _, same = self.request("/api/runs", changed, key="concurrent")
        self.assertEqual(code, 200)
        self.assertEqual(same["run_id"], responses[0][2]["run_id"])
        changed["edit_revision"] = "different-edit"
        code, _, error = self.request("/api/runs", changed, key="concurrent")
        self.assertEqual(code, 409)
        self.assertEqual(error["issues"][0]["code"], "task.idempotency_conflict")

    def test_strict_transport_and_validation_errors(self):
        submission = self.submission(steps=1)
        cases = [(None, submission, None, 400),
                 ("duplicate", None, b'{"x":1,"x":2}', 400),
                 ("nan", None, b'{"x":NaN}', 400),
                 ("utf8", None, b'{"x":"\xff"}', 400),
                 ("shape", {}, None, 422)]
        for key, value, raw, expected in cases:
            with self.subTest(key=key):
                code, _, error = self.request("/api/runs", value, raw=raw, key=key)
                self.assertEqual(code, expected, error)
                self.assertEqual(error["task_contract_version"], "0.1.0")
                self.assertTrue(error["issues"])
                _validator("Error").validate(error)
        code, _, _ = self.request("/api/runs", submission, key="type", headers={"Content-Type": "text/plain"})
        self.assertEqual(code, 415)
        invalid = deepcopy(submission)
        invalid["execution"]["backend"] = "unavailable-gpu"
        code, _, _ = self.request("/api/runs", invalid, key="backend")
        self.assertEqual(code, 422)
        # Refused requests do not consume their key.
        code, _, _ = self.request("/api/runs", submission, key="backend")
        self.assertEqual(code, 202)

    def test_invalid_cursor_unknown_resource_and_bodyless_cancel(self):
        _, _, task = self.request("/api/runs", self.submission(steps=1), key="queries")
        run_id = task["run_id"]
        for query in ("after=-1", "after=1.5", "after=0&after=1", "after=99999999", "limit=0", "unexpected=1"):
            with self.subTest(query=query):
                code, _, _ = self.request(f"/api/runs/{run_id}/events?{query}")
                self.assertEqual(code, 400)
        code, _, _ = self.request("/api/runs/not-a-known-run")
        self.assertEqual(code, 404)
        code, _, _ = self.request(f"/api/runs/{run_id}/chunks/../input")
        self.assertEqual(code, 404)
        code, _, _ = self.request(f"/api/runs/{run_id}/cancel", {}, method="POST")
        self.assertEqual(code, 400)
        code, _, _ = self.request(f"/api/runs/{run_id}/cancel", method="POST")
        self.assertIn(code, (200, 202))
        terminal = self.terminal(run_id)
        self.assertIn(terminal["status"], ("cancelled", "completed"))
        code, _, again = self.request(f"/api/runs/{run_id}/cancel", method="POST")
        self.assertEqual(code, 200)
        self.assertEqual(again, terminal)

    def test_oversized_and_duplicate_length_rejected_before_read(self):
        for lengths, expected in ((["999999999"], 413), (["2", "2"], 400), (["-1"], 400)):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
            try:
                connection.putrequest("POST", "/api/runs")
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Idempotency-Key", "length")
                for length in lengths:
                    connection.putheader("Content-Length", length)
                connection.endheaders()
                response = connection.getresponse()
                self.assertEqual(response.status, expected)
                self.assertIn("issues", json.loads(response.read()))
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
