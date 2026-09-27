"""Compare one physical uptake support across grids of increasing resolution."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLE_DIR = Path(__file__).resolve().parent
SPACINGS_UM = (5.0, 2.5, 1.25, 0.625, 0.3125, 0.15625)
TIME_STEP_S = 0.25
DURATION_S = 4.0
RATE_CONSTANT = 100.0
INITIAL_CONCENTRATION_UM = 10.0
SUPPORT_XY_UM = 2.0


def grid_for(geometry: str, spacing_um: float) -> GridDomain:
    nx, ny = round(20 / spacing_um), round(10 / spacing_um)
    if geometry == "thin_layer":
        return GridDomain.thin_layer(nx, ny, spacing_um, spacing_um, 1.0)
    if geometry == "volume":
        return GridDomain.volume(
            nx, ny, round(10 / spacing_um), spacing_um, spacing_um, spacing_um,
        )
    raise ValueError("unknown geometry")


def continuum_box_uptake(geometry: str) -> float:
    """Same Euler time step as the runtime, with infinitely fine spatial cells.

    The example starts uniformly and has no transport. In this special case,
    the box-average concentration obeys one linear decay equation.
    """
    effective_height_um = 1.0 if geometry == "thin_layer" else 2.0
    support_volume_um3 = SUPPORT_XY_UM**2 * effective_height_um
    molecules_per_uM_support = 602.214076 * support_volume_um3
    steps = round(DURATION_S / TIME_STEP_S)
    return (
        INITIAL_CONCENTRATION_UM * molecules_per_uM_support
        * -math.expm1(
            steps * math.log1p(-RATE_CONSTANT * TIME_STEP_S / molecules_per_uM_support)
        )
    )


def run_case(geometry: str, spacing_um: float) -> dict:
    grid = grid_for(geometry, spacing_um)
    z_um = 0.5 if geometry == "thin_layer" else 3.7
    group = CellGroup(
        "group_1", ("cell_0",), np.array([[6.2, 3.7, z_um]]),
        np.array([[0.0, 0.0, 0.0, 1.0]]),
    )
    graph = json.loads((EXAMPLE_DIR / "box_uptake.graph.json").read_text(encoding="utf-8"))
    if geometry == "volume":
        for node in graph["nodes"]:
            if node["id"] in ("sampler", "deposit"):
                node["parameters"]["support_z_um"]["value"] = 2.0
    run = json.loads((EXAMPLE_DIR / "uptake.run.json").read_text(encoding="utf-8"))
    run["graph_id"] = graph["id"]
    run["run_id"] = f"fixed-support-{geometry}-{spacing_um}"
    simulation = Simulation(World(grid, {"group_1": group}), graph, run, default_registry())
    initial_molecules = (
        simulation.current.concentration_fields["substrate"].sum()
        * grid.molecules_per_uM_voxel
    )
    for _ in range(round(DURATION_S / TIME_STEP_S)):
        snapshot = simulation.step(TIME_STEP_S)
    remaining_molecules = (
        snapshot.concentration_fields["substrate"].sum()
        * grid.molecules_per_uM_voxel
    )
    uptake = snapshot.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
    reference = continuum_box_uptake(geometry)
    return {
        "geometry": geometry,
        "grid_step_um": spacing_um,
        "voxel_count": grid.voxel_count,
        "support_xyz_um": [2.0, 2.0, 0.8 if geometry == "thin_layer" else 2.0],
        "dt_s": TIME_STEP_S,
        "duration_s": DURATION_S,
        "cumulative_uptake_molecules": uptake,
        "fine_grid_reference_molecules": reference,
        "spatial_difference_molecules": uptake - reference,
        "mass_balance_error_molecules": initial_molecules - remaining_molecules - uptake,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    args = parser.parse_args()
    for spacing_um in SPACINGS_UM:
        print(json.dumps(run_case(args.geometry, spacing_um)))


if __name__ == "__main__":
    main()
