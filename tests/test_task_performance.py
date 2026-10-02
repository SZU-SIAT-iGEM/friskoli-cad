"""Transport optimizations preserve complete scientific output and cancellation."""
from copy import deepcopy
import hashlib
import tempfile
import time
import unittest

from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import canonical_bytes, canonical_loads, sha256
from friskoli_cad.tasks import TaskService
from friskoli_cad.tasks.metadata import provenance


class TaskPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="friskoli-task-performance-")
        self.service = TaskService(self.temp.name)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def submission(self, steps, stride, dt):
        project = make_example("chemotaxis-pts-a")
        return {"task_contract_version": "0.4.0", "request_id": "transport-check", "edit_revision": "transport:1",
                "project": project, "version_lock": self.service.version_lock(project),
                "execution": {"semantics": "chemotaxis-spatial-v1", "backend": "numpy-cpu", "seed": 17,
                              "dt_s": dt, "steps": steps},
                "output_plan": {"frame_every_steps": stride, "observables": list(project["run"]["channels"]),
                                "include_fields": True}}

    def terminal(self, run_id):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            task = self.service.get(run_id)
            if task["status"] not in ("queued", "running"):
                return task
            time.sleep(.02)
        self.fail("Task did not reach a terminal state")

    def test_foundation_death_records_rng_but_reserve_alone_does_not(self):
        project = make_example("chemotaxis-pts-a")
        owner = {"kind": "population", "id": next(iter(project["groups"]))}
        project["graph"]["nodes"] = [{"module_id": "metabolism.reserve_balance", "owner": owner}]
        self.assertFalse(provenance(17, {}, project)["rng"]["used"])
        project["graph"]["nodes"].append({"module_id": "life.starvation_hazard", "owner": owner})
        self.assertTrue(provenance(17, {}, project)["rng"]["used"])

    def test_output_bytes_match_direct_solver_at_multiple_dt_and_output_intervals(self):
        for dt, stride in ((.05, 1), (.05, 7), (.1, 11)):
            with self.subTest(dt=dt, stride=stride):
                body = self.submission(45, stride, dt)
                original = deepcopy(body)
                task, _ = self.service.submit(body, f"equivalent-{dt}-{stride}")
                completed = self.terminal(task["run_id"])
                self.assertEqual(completed["status"], "completed", completed["issues"])
                self.assertEqual(completed["progress"]["committed_step"], 45)
                self.assertEqual(body, original)
                manifest = self.service.manifest(task["run_id"])
                actual = []
                for descriptor in manifest["chunks"]:
                    raw = self.service.chunk(task["run_id"], descriptor["chunk_id"])
                    self.assertEqual(len(raw), descriptor["bytes"])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), descriptor["sha256"])
                    actual.extend(canonical_loads(raw)["frames"])
                sim = simulation_from_project(body["project"], seed=17)
                expected = []
                for step in range(46):
                    snap = sim.current if step == 0 else sim.step(dt)
                    if step != 0 and step % stride and step != 45:
                        continue
                    domain = snap.domain
                    expected.append({"sequence": len(expected), "step_index": step,
                        "time_s": snap.cell_frame["time_s"],
                        "grid_revision": sha256({"shape": list(domain.shape),
                            "spacing": [domain.dx_um, domain.dy_um, domain.dz_um]}),
                        "frame": snap.cell_frame, "metrics": dict(snap.metrics),
                        "lifecycle_details": dict(snap.lifecycle_details), "object_states": dict(snap.object_states),
                        "concentrations": {key: {"unit": snap.concentration_units[key], "values_zyx": value.tolist()}
                                           for key, value in snap.concentration_fields.items()}})
                self.assertEqual(canonical_bytes(actual), canonical_bytes(expected))
                if stride > 1:
                    # Pure numerical steps no longer require durable progress events.
                    self.assertLess(completed["last_event_seq"], 45)

    def test_sparse_output_cancel_does_not_wait_for_next_output_frame(self):
        body = self.submission(10000, 10000, .05)
        task, _ = self.service.submit(body, "sparse-cancel")
        deadline = time.monotonic() + 15
        while self.service.get(task["run_id"])["result"]["completeness"] != "partial":
            if time.monotonic() > deadline:
                self.fail("Initial frame was not published")
            time.sleep(.01)
        started = time.monotonic()
        self.service.cancel(task["run_id"])
        cancelled = self.terminal(task["run_id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertLess(time.monotonic() - started, 5)
        self.assertLess(cancelled["progress"]["committed_step"], 10000)
        manifest = self.service.manifest(task["run_id"])
        self.assertEqual(manifest["completeness"], "partial")
        self.assertEqual([chunk["first_step"] for chunk in manifest["chunks"]], [0])
        # Recovery constructs the same manifest from durable indexed results.
        self.service.close()
        self.service = TaskService(self.temp.name)
        self.assertEqual(self.service.manifest(task["run_id"]), manifest)
        self.service.chunk(task["run_id"], manifest["chunks"][0]["chunk_id"])


if __name__ == "__main__":
    unittest.main()
