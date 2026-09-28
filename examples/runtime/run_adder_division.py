"""Run the illustrative length adder and print the first division."""

from __future__ import annotations

import json
from pathlib import Path

from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import validate_frame_sequence


def main() -> None:
    document = json.loads(Path(__file__).with_name("adder_division.project.json").read_text(encoding="utf-8"))
    simulation = simulation_from_project(document)
    frames = [simulation.current.cell_frame]
    for _ in range(5):
        frames.append(simulation.step(0.5).cell_frame)
    validate_frame_sequence(frames, document["run"])
    for frame in frames:
        print(json.dumps({
            "time_s": frame["time_s"],
            "cells": [{
                "id": cell["id"],
                "length_um": round(cell["geometry"]["length_um"], 6),
                "added_length_um": round(cell["channels"]["adder.added_length"], 6),
            } for cell in frame["cells"]],
            "events": frame["events"],
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
