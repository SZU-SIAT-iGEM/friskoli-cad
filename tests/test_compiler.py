from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from friskoli_cad.engine import compile_graph
from friskoli_cad.protocol import ProtocolError


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "protocol"


def load(relative_path: str):
    return json.loads((EXAMPLES / relative_path).read_text(encoding="utf-8"))


def manifests():
    return [load(f"modules/{name}.json") for name in (
        "field.hold", "field.diffusion", "field.sample_at_cells", "display.surface_copies"
    )]


class CompilerTests(unittest.TestCase):
    def test_execution_order_and_input_binding(self):
        plan = compile_graph(load("graph.json"), manifests())
        self.assertEqual([node.id for node in plan.nodes], ["field", "sampler", "display"])
        binding = plan.by_id["sampler"].inputs["concentration"]
        self.assertEqual((binding.source_node, binding.source_port, binding.timing),
                         ("field", "concentration", "same_step"))
        self.assertEqual(plan.by_id["display"].parameters["protein_id"].value, "INP")

    def test_environment_replacement_keeps_consumer_binding(self):
        old = compile_graph(load("graph.json"), manifests())
        new = compile_graph(load("graph-diffusion.json"), manifests())
        self.assertEqual(old.by_id["field"].module_id, "field.hold")
        self.assertEqual(new.by_id["field"].module_id, "field.diffusion")
        self.assertEqual(old.by_id["sampler"].inputs, new.by_id["sampler"].inputs)

    def test_previous_step_does_not_create_current_step_dependency(self):
        graph = load("graph.json")
        graph["edges"][0]["timing"] = "previous_step"
        registry = manifests()
        registry[0]["phase"] = 5
        plan = compile_graph(graph, registry)
        self.assertEqual([node.id for node in plan.nodes], ["sampler", "field", "display"])
        self.assertEqual(plan.by_id["sampler"].inputs["concentration"].timing, "previous_step")
        self.assertIn("concentration", plan.by_id["field"].initial_outputs)

    def test_unrelated_nodes_have_deterministic_order(self):
        graph = load("graph.json")
        second = copy.deepcopy(graph["nodes"][2])
        graph["nodes"][2]["id"] = "display_z"
        second["id"] = "display_a"
        graph["nodes"].append(second)
        plan = compile_graph(graph, manifests())
        self.assertEqual([node.id for node in plan.nodes],
                         ["field", "sampler", "display_a", "display_z"])

    def test_plan_is_a_snapshot_and_rejects_invalid_graphs(self):
        graph = load("graph.json")
        plan = compile_graph(graph, manifests())
        graph["nodes"][0]["parameters"]["level"]["value"] = 999
        self.assertEqual(plan.by_id["field"].parameters["level"].value, 10)
        with self.assertRaises(TypeError):
            plan.by_id["field"].parameters["level"] = "changed"
        graph["edges"] = []
        with self.assertRaises(ProtocolError):
            compile_graph(graph, manifests())


if __name__ == "__main__":
    unittest.main()
