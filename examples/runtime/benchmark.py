"""Measure the complete no-diffusion runtime step on this machine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", type=int, default=10_000)
    parser.add_argument("--steps", type=int, default=3)
    args = parser.parse_args()
    if args.cells <= 0 or args.steps <= 0:
        parser.error("--cells and --steps must be positive")

    grid = GridDomain(nx=128, ny=128, dx_um=5, depth_um=1)
    voxel = np.arange(args.cells) % (grid.nx * grid.ny)
    positions = np.column_stack((
        (voxel % grid.nx + 0.5) * grid.dx_um,
        (voxel // grid.nx + 0.5) * grid.dx_um,
        np.full(args.cells, 0.5),
    ))
    orientation = np.tile([0, 0, 0, 1], (args.cells, 1))
    group = CellGroup(
        "group_1", tuple(f"cell_{index}" for index in range(args.cells)),
        positions, orientation,
    )
    world = World(grid, {"group_1": group})
    graph = json.loads((EXAMPLE_DIR / "uptake.graph.json").read_text(encoding="utf-8"))
    run = json.loads((EXAMPLE_DIR / "uptake.run.json").read_text(encoding="utf-8"))
    simulation = Simulation(world, graph, run, default_registry())

    started = perf_counter()
    for _ in range(args.steps):
        simulation.step(0.1)
    seconds = perf_counter() - started
    print(json.dumps({
        "cells": args.cells,
        "voxels": grid.nx * grid.ny,
        "steps": args.steps,
        "seconds_per_step": seconds / args.steps,
        "scope": "numerical modules, snapshots, and incremental frame checks",
        "excludes": "setup, full external JSON Schema validation, disk and browser output",
    }))


if __name__ == "__main__":
    main()
