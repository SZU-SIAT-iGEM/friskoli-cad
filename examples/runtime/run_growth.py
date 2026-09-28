"""Replay the illustrative project and show per-cell geometry in each frame."""

from __future__ import annotations

import json
from pathlib import Path

from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import validate_frame_sequence


PROJECT_PATH = Path(__file__).with_name("linear_growth.project.json")


def main() -> None:
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    simulation = simulation_from_project(project)
    snapshots = [simulation.current]
    snapshots.extend(simulation.step(0.5) for _ in range(3))
    validate_frame_sequence((snapshot.cell_frame for snapshot in snapshots), project["run"])
    for snapshot in snapshots:
        cell = snapshot.cell_frame["cells"][0]
        geometry = cell["geometry"]
        # Round the report only; the validated frame retains its full precision.
        print(json.dumps({
            "time_s": snapshot.cell_frame["time_s"],
            "frame_version": snapshot.cell_frame["frame_version"],
            "cell_id": cell["id"],
            "geometry": {
                "shape": geometry["shape"],
                "length_um": round(geometry["length_um"], 12),
                "diameter_um": geometry["diameter_um"],
            },
            "active_concentration_fields": list(snapshot.concentration_fields),
        }))


if __name__ == "__main__":
    main()
