"""Compare fixed-cell uptake with and without no-flux diffusion."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry
from friskoli_cad.engine.diffusion import explicit_no_flux_limit


EXAMPLE_DIR = Path(__file__).resolve().parent


def run_case(geometry: str, diffusivity_um2_s: float) -> dict:
    if geometry == "thin_layer":
        grid = GridDomain.thin_layer(3, 3, 5, 5, 1)
        z_um, center = 0.5, (0, 1, 1)
    else:
        grid = GridDomain.volume(3, 3, 3, 5, 5, 5)
        z_um, center = 7.5, (1, 1, 1)
    group = CellGroup(
        "group_1", ("cell_0",), np.array([[7.5, 7.5, z_um]]),
        np.array([[0, 0, 0, 1]]),
    )
    graph = json.loads((EXAMPLE_DIR / "diffusion_uptake.graph.json").read_text(encoding="utf-8"))
    diffusion = next(node for node in graph["nodes"] if node["id"] == "diffusion")
    diffusion["parameters"]["diffusivity"]["value"] = diffusivity_um2_s
    run = json.loads((EXAMPLE_DIR / "uptake.run.json").read_text(encoding="utf-8"))
    run["graph_id"] = graph["id"]
    run["run_id"] = f"no-flux-{geometry}-{diffusivity_um2_s}"
    simulation = Simulation(World(grid, {"group_1": group}), graph, run, default_registry())
    initial_molecules = (
        simulation.current.concentration_fields["substrate"].sum()
        * grid.molecules_per_uM_voxel
    )
    for _ in range(6):
        snapshot = simulation.step(0.25)
    field = snapshot.concentration_fields["substrate"]
    uptake = snapshot.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
    limit = explicit_no_flux_limit(grid, diffusivity_um2_s)
    return {
        "geometry": geometry,
        "diffusivity_um2_s": diffusivity_um2_s,
        "time_s": snapshot.cell_frame["time_s"],
        "explicit_dt_limit_s": limit if math.isfinite(limit) else None,
        "center_concentration_uM": float(field[center]),
        "neighbor_concentration_uM": float(field[center[0], center[1], center[2] + 1]),
        "cell_uptake_molecules": uptake,
        "mass_balance_error_molecules": float(
            initial_molecules - field.sum() * grid.molecules_per_uM_voxel - uptake
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    args = parser.parse_args()
    for diffusivity in (0, 5):
        print(json.dumps(run_case(args.geometry, diffusivity)))


if __name__ == "__main__":
    main()
