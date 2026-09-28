"""Trace one illustrative swimmer with uptake, diffusion and wall reflection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    args = parser.parse_args()
    if args.geometry == "thin_layer":
        grid = GridDomain.thin_layer(3, 3, 5, 5, 1)
        z_um = 0.5
    else:
        grid = GridDomain.volume(3, 3, 3, 5, 5, 5)
        z_um = 7.5
    group = CellGroup(
        "group_1", ("cell_0",), np.array([[12.5, 7.5, z_um]]),
        np.array([[0, 0, 0, 1]]),
    )
    graph = json.loads((EXAMPLE_DIR / "moving_uptake.graph.json").read_text(encoding="utf-8"))
    run = json.loads((EXAMPLE_DIR / "uptake.run.json").read_text(encoding="utf-8"))
    run["graph_id"] = graph["id"]
    simulation = Simulation(World(grid, {"group_1": group}), graph, run, default_registry())
    initial_molecules = (
        simulation.current.concentration_fields["substrate"].sum()
        * grid.molecules_per_uM_voxel
    )

    for step in range(9):
        snapshot = simulation.current if step == 0 else simulation.step(0.25)
        cell = snapshot.cell_frame["cells"][0]
        uptake = cell["channels"]["uptake.cumulative"]
        remaining = snapshot.concentration_fields["substrate"].sum() * grid.molecules_per_uM_voxel
        print(json.dumps({
            "time_s": snapshot.cell_frame["time_s"],
            "position_um": cell["position_um"],
            "heading": simulation.outputs["motion"]["heading"][0].tolist(),
            "uptake_flux_molecule_s": cell["channels"]["uptake.flux"],
            "cumulative_uptake_molecule": uptake,
            "mass_balance_error_molecule": initial_molecules - remaining - uptake,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
