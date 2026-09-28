"""Show an idle registered species alongside one declared capsule shape."""

from __future__ import annotations

import json
from pathlib import Path

from friskoli_cad.project import simulation_from_project


PROJECT_PATH = Path(__file__).with_name("scheduled_inputs.project.json")


def main() -> None:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    project["project_version"] = "0.2.0"
    project["groups"]["group_1"]["initial_geometry"] = [{
        "shape": "capsule", "length_um": 2.0, "diameter_um": 0.8,
        "provenance": {"kind": "example", "reference": "illustrative dimensions only"},
    }]
    project["controls"].pop("oxygen_feed")
    idle_nodes = {"oxygen_field", "oxygen_input"}
    project["graph"]["nodes"] = [
        node for node in project["graph"]["nodes"] if node["id"] not in idle_nodes
    ]
    project["graph"]["edges"] = [
        edge for edge in project["graph"]["edges"]
        if edge["from"]["node"] not in idle_nodes and edge["to"]["node"] not in idle_nodes
    ]
    simulation = simulation_from_project(project)
    snapshot = simulation.step(0.25)
    capsule = simulation.world.groups["group_1"].geometry[0]
    print(json.dumps({
        "project_version": project["project_version"],
        "registered_species": list(project["species"]),
        "active_concentration_fields": list(snapshot.concentration_fields),
        "cell_0_capsule_um": {"length": capsule.length_um, "diameter": capsule.diameter_um},
    }))


if __name__ == "__main__":
    main()
