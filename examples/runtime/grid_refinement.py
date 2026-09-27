"""Measure grid and time-step sensitivity of the current point-uptake example."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLE_DIR = Path(__file__).resolve().parent
SPACINGS_UM = (5.0, 2.5, 1.25)
TIME_STEPS_S = (1.0, 0.5, 0.25)
DURATION_S = 4.0


def grid_for(geometry: str, spacing_um: float) -> GridDomain:
    if spacing_um <= 0 or not all(
        math.isclose(length / spacing_um, round(length / spacing_um))
        for length in (20, 10)
    ):
        raise ValueError("spacing must divide the fixed physical extent")
    nx, ny = round(20 / spacing_um), round(10 / spacing_um)
    if geometry == "thin_layer":
        return GridDomain.thin_layer(nx, ny, spacing_um, spacing_um, 1)
    if geometry == "volume":
        return GridDomain.volume(
            nx, ny, round(10 / spacing_um), spacing_um, spacing_um, spacing_um
        )
    raise ValueError("unknown geometry")


def run_case(geometry: str, spacing_um: float, dt_s: float) -> dict:
    if dt_s <= 0 or not math.isclose(DURATION_S / dt_s, round(DURATION_S / dt_s)):
        raise ValueError("time step must divide the fixed duration")
    grid = grid_for(geometry, spacing_um)
    z_um = 0.5 if geometry == "thin_layer" else 3.7
    group = CellGroup(
        "group_1", ("cell_0",), np.array([[6.2, 3.7, z_um]]),
        np.array([[0.0, 0.0, 0.0, 1.0]]),
    )
    world = World(grid, {"group_1": group})
    graph = json.loads((EXAMPLE_DIR / "uptake.graph.json").read_text(encoding="utf-8"))
    run = json.loads((EXAMPLE_DIR / "uptake.run.json").read_text(encoding="utf-8"))
    run["run_id"] = f"refinement-{geometry}-{spacing_um}-{dt_s}"
    simulation = Simulation(world, graph, run, default_registry())
    initial_molecules = (
        simulation.current.concentration_fields["substrate"].sum()
        * grid.molecules_per_uM_voxel
    )

    for _ in range(round(DURATION_S / dt_s)):
        snapshot = simulation.step(dt_s)

    field = snapshot.concentration_fields["substrate"]
    remaining = field.sum() * grid.molecules_per_uM_voxel
    uptake = snapshot.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
    cell_index = grid.flat_indices(group.positions_um)[0]
    return {
        "geometry": geometry,
        "grid_step_um": spacing_um,
        "z_step_um": grid.dz_um,
        "voxel_count": grid.voxel_count,
        "voxel_volume_um3": grid.dx_um * grid.dy_um * grid.dz_um,
        "dt_s": dt_s,
        "duration_s": DURATION_S,
        "initial_molecules": float(initial_molecules),
        "cell_voxel_concentration_uM": float(field.ravel()[cell_index]),
        "cumulative_uptake_molecules": uptake,
        "mass_balance_error_molecules": float(initial_molecules - remaining - uptake),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    args = parser.parse_args()
    for spacing_um in SPACINGS_UM:
        for dt_s in TIME_STEPS_S:
            print(json.dumps(run_case(args.geometry, spacing_um, dt_s)))


if __name__ == "__main__":
    main()
