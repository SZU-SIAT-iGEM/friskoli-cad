from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from friskoli_cad.protocol import (
    ProtocolError, validate_frame_sequence, validate_graph, validate_manifest, validate_run_metadata,
)


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "protocol"


def load(relative_path: str):
    return json.loads((EXAMPLES / relative_path).read_text(encoding="utf-8"))


def manifests():
    return [load(f"modules/{name}.json") for name in (
        "field.hold", "field.diffusion", "field.sample_at_cells", "display.surface_copies"
    )]


class ProtocolTests(unittest.TestCase):
    def assert_protocol_error(self, code, action):
        with self.assertRaises(ProtocolError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_valid_examples_and_environment_replacement(self):
        registry = manifests()
        for manifest in registry:
            validate_manifest(manifest)
        validate_graph(load("graph.json"), registry)
        validate_graph(load("graph-diffusion.json"), registry)
        validate_run_metadata(load("run.json"), load("graph.json"), registry)
        validate_frame_sequence(load("frames.json"), load("run.json"))

    def test_unknown_fields_and_wrong_protocol_version_are_rejected(self):
        manifest = load("modules/field.hold.json")
        manifest["legacy_setting"] = 1
        self.assert_protocol_error("schema.invalid", lambda: validate_manifest(manifest))
        graph = load("graph.json")
        graph["protocol_version"] = "0.2.0"
        self.assert_protocol_error("schema.invalid", lambda: validate_graph(graph, manifests()))

    def test_manifest_references_and_division_rules(self):
        manifest = load("modules/field.hold.json")
        manifest["outputs"]["concentration"]["species_parameter"] = "missing"
        self.assert_protocol_error("port.species_binding", lambda: validate_manifest(manifest))
        display = load("modules/display.surface_copies.json")
        display["state"]["copies"]["on_division"] = "not_applicable"
        self.assert_protocol_error("state.division_rule", lambda: validate_manifest(display))

    def test_species_and_unit_mismatches_are_rejected(self):
        graph = load("graph.json")
        graph["nodes"][1]["parameters"]["species"]["value"] = "glucose"
        self.assert_protocol_error("edge.species", lambda: validate_graph(graph, manifests()))
        graph = load("graph.json")
        graph["nodes"][0]["parameters"]["level"]["unit"] = "nM"
        self.assert_protocol_error("parameter.unit", lambda: validate_graph(graph, manifests()))
        changed = manifests()
        changed[0]["outputs"]["concentration"]["unit"] = "nM"
        self.assert_protocol_error("edge.type", lambda: validate_graph(load("graph.json"), changed))
        changed = manifests()
        changed[0]["outputs"]["concentration"]["quantity"] = "receptor_activity"
        self.assert_protocol_error("edge.type", lambda: validate_graph(load("graph.json"), changed))

    def test_required_input_and_scope_are_checked(self):
        graph = load("graph.json")
        graph["edges"] = []
        self.assert_protocol_error("edge.required", lambda: validate_graph(graph, manifests()))
        graph = load("graph.json")
        graph["nodes"][1]["owner"]["kind"] = "environment"
        self.assert_protocol_error("node.scope", lambda: validate_graph(graph, manifests()))

    def test_parameter_type_and_range_are_checked(self):
        graph = load("graph.json")
        graph["nodes"][2]["parameters"]["initial_copies"]["value"] = 1.5
        self.assert_protocol_error("parameter.type", lambda: validate_graph(graph, manifests()))
        graph = load("graph.json")
        graph["nodes"][0]["parameters"]["level"]["value"] = -1
        self.assert_protocol_error("parameter.range", lambda: validate_graph(graph, manifests()))
        graph = load("graph.json")
        graph["nodes"][0]["parameters"]["level"]["value"] = float("nan")
        self.assert_protocol_error("number.non_finite", lambda: validate_graph(graph, manifests()))

    def test_previous_step_requires_initialized_source(self):
        graph = load("graph.json")
        changed = manifests()
        changed[0]["phase"] = 5
        self.assert_protocol_error("edge.phase", lambda: validate_graph(graph, changed))
        graph["edges"][0]["timing"] = "previous_step"
        validate_graph(graph, changed)
        changed[0]["initial_outputs"] = []
        self.assert_protocol_error("edge.initial", lambda: validate_graph(graph, changed))

    def test_same_step_cycles_are_rejected(self):
        display = load("modules/display.surface_copies.json")
        display["inputs"]["upstream_copies"] = {
            "shape": "cell.scalar", "quantity": "protein_copies", "unit": "molecule"
        }
        graph = load("graph.json")
        graph["nodes"][2]["id"] = "display_a"
        other = copy.deepcopy(graph["nodes"][2])
        other["id"] = "display_b"
        graph["nodes"].append(other)
        graph["edges"].extend([
            {
                "id": "a_to_b", "from": {"node": "display_a", "port": "copies"},
                "to": {"node": "display_b", "port": "upstream_copies"}, "timing": "same_step",
            },
            {
                "id": "b_to_a", "from": {"node": "display_b", "port": "copies"},
                "to": {"node": "display_a", "port": "upstream_copies"}, "timing": "same_step",
            },
        ])
        changed = manifests()
        changed[3] = display
        self.assert_protocol_error("edge.cycle", lambda: validate_graph(graph, changed))

    def test_cell_ports_cannot_cross_population_owners(self):
        display = load("modules/display.surface_copies.json")
        display["inputs"]["upstream_copies"] = {
            "shape": "cell.scalar", "quantity": "protein_copies", "unit": "molecule"
        }
        graph = load("graph.json")
        graph["nodes"][2]["id"] = "display_a"
        other = copy.deepcopy(graph["nodes"][2])
        other["id"] = "display_b"
        other["owner"]["id"] = "group_2"
        graph["nodes"].append(other)
        graph["edges"].append({
            "id": "cross_group", "from": {"node": "display_a", "port": "copies"},
            "to": {"node": "display_b", "port": "upstream_copies"}, "timing": "same_step"
        })
        changed = manifests()
        changed[3] = display
        self.assert_protocol_error("edge.population", lambda: validate_graph(graph, changed))

    def test_frame_events_preserve_cell_identity(self):
        frames = load("frames.json")
        frames[1]["events"] = []
        self.assert_protocol_error("frame.cells", lambda: validate_frame_sequence(frames))
        frames = load("frames.json")
        frames[2]["events"].append({"type": "birth", "time_s": 1.7, "cell_id": "cell_1", "group_id": "group_1"})
        self.assert_protocol_error("cell.reused", lambda: validate_frame_sequence(frames))

    def test_run_metadata_binds_channels_and_groups(self):
        run = load("run.json")
        run["channels"]["display.copies"]["unit"] = "uM"
        self.assert_protocol_error("channel.type", lambda: validate_run_metadata(run, load("graph.json"), manifests()))
        run = load("run.json")
        run["groups"] = ["other_group"]
        self.assert_protocol_error("run.groups", lambda: validate_run_metadata(run, load("graph.json"), manifests()))
        run = load("run.json")
        run["channels"]["display.copies"]["group_id"] = "other_group"
        self.assert_protocol_error(
            "channel.group", lambda: validate_run_metadata(run, load("graph.json"), manifests())
        )
        frames = load("frames.json")
        frames[0]["cells"][0]["channels"]["undeclared.value"] = 1
        self.assert_protocol_error("cell.channel", lambda: validate_frame_sequence(frames, load("run.json")))

    def test_frame_order_and_pose_are_checked(self):
        frames = load("frames.json")
        frames[1]["frame_index"] = 2
        self.assert_protocol_error("frame.order", lambda: validate_frame_sequence(frames))
        frames = load("frames.json")
        frames[0]["cells"][0]["orientation_xyzw"] = [0, 0, 0, 2]
        self.assert_protocol_error("cell.orientation", lambda: validate_frame_sequence(frames))


if __name__ == "__main__":
    unittest.main()
