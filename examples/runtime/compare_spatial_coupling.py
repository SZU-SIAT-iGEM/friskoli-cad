"""Compare single-voxel and physical-box coupling around a grid boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    args = parser.parse_args()
    if args.geometry == "thin_layer":
        grid = GridDomain.thin_layer(2, 1, 5, 5, 1)
        y_um, z_um = 2.5, 0.5
    else:
        grid = GridDomain.volume(2, 2, 2, 5, 5, 5)
        y_um, z_um = 5, 5
    registry = default_registry()

    for x_um in (4.99, 5.0, 5.01):
        group = CellGroup(
            "group_1", ("cell_0",), np.array([[x_um, y_um, z_um]]),
            np.array([[0, 0, 0, 1]]),
        )
        world = World(grid, {"group_1": group})
        for name, graph_name in (
            ("nearest", "uptake.graph.json"),
            ("box_support", "box_uptake.graph.json"),
        ):
            graph = json.loads((EXAMPLE_DIR / graph_name).read_text(encoding="utf-8"))
            run = json.loads((EXAMPLE_DIR / "uptake.run.json").read_text(encoding="utf-8"))
            run["graph_id"] = graph["id"]
            simulation = Simulation(world, graph, run, registry)
            snapshot = simulation.step(1)
            field = snapshot.concentration_fields["substrate"]
            loss = (10 - field) * grid.molecules_per_uM_voxel
            uptake = snapshot.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
            print(json.dumps({
                "geometry": grid.geometry,
                "method": name,
                "cell_x_um": x_um,
                "loss_by_voxel_molecules": np.round(loss, 6).tolist(),
                "total_loss_molecules": float(loss.sum()),
                "cell_uptake_molecules": uptake,
                "mass_balance_error_molecules": float(loss.sum() - uptake),
            }))


if __name__ == "__main__":
    main()
