"""Measure the complete no-diffusion runtime step on this machine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    parser.add_argument("--cells", type=int, default=10_000)
    parser.add_argument("--steps", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--nx", type=int, default=128)
    parser.add_argument("--ny", type=int, default=128)
    parser.add_argument("--nz", type=int, default=128, help="z layers for volume geometry")
    parser.add_argument("--spacing-um", type=float, default=5)
    parser.add_argument("--thickness-um", type=float, default=1, help="thin layer height")
    args = parser.parse_args()
    if args.cells <= 0 or args.steps <= 0 or args.repeats <= 0:
        parser.error("--cells, --steps and --repeats must be positive")

    if args.geometry == "thin_layer":
        grid = GridDomain.thin_layer(
            args.nx, args.ny, args.spacing_um, args.spacing_um, args.thickness_um
        )
    else:
        grid = GridDomain.volume(
            args.nx, args.ny, args.nz,
            args.spacing_um, args.spacing_um, args.spacing_um,
        )
    voxel = np.arange(args.cells, dtype=np.int64) * 104729 % grid.voxel_count
    positions = np.column_stack((
        (voxel % grid.nx + 0.5) * grid.dx_um,
        (voxel // grid.nx % grid.ny + 0.5) * grid.dy_um,
        (voxel // (grid.nx * grid.ny) + 0.5) * grid.dz_um,
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

    samples = []
    for _ in range(args.repeats):
        started = perf_counter()
        for _ in range(args.steps):
            simulation.step(0.1)
        samples.append((perf_counter() - started) / args.steps)
    print(json.dumps({
        "geometry": grid.geometry,
        "grid_shape_zyx": grid.shape,
        "voxel_um": [grid.dx_um, grid.dy_um, grid.dz_um],
        "extent_um": grid.extent_um,
        "cells": args.cells,
        "voxels": grid.voxel_count,
        "dt_s": 0.1,
        "steps_per_repeat": args.steps,
        "repeats": args.repeats,
        "seconds_per_step_samples": samples,
        "median_seconds_per_step": median(samples),
        "scope": "numerical modules, snapshots, and incremental frame checks",
        "excludes": "setup, full external JSON Schema validation, disk and browser output",
    }))


if __name__ == "__main__":
    main()
