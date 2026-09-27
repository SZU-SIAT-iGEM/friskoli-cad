"""Run the small concentration -> uptake -> concentration example."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, World, default_registry


EXAMPLE_DIR = Path(__file__).resolve().parent


def load(name: str) -> dict:
    return json.loads((EXAMPLE_DIR / name).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    args = parser.parse_args()
    if args.geometry == "thin_layer":
        grid = GridDomain.thin_layer(4, 2, 5, 5, 1)
        positions = np.array([[2.5, 2.5, 0.5], [12.5, 2.5, 0.5]])
    else:
        grid = GridDomain.volume(4, 2, 2, 5, 5, 5)
        positions = np.array([[2.5, 2.5, 2.5], [2.5, 2.5, 7.5]])
    group = CellGroup(
        "group_1",
        ("cell_0", "cell_1"),
        positions,
        np.array([[0, 0, 0, 1], [0, 0, 0, 1]]),
    )
    world = World(grid, {"group_1": group})
    simulation = Simulation(
        world, load("uptake.graph.json"), load("uptake.run.json"), default_registry()
    )
    initial_inventory = (
        simulation.current.concentration_fields["substrate"].sum()
        * world.grid.molecules_per_uM_voxel
    )
    def report(snapshot) -> None:
        field = snapshot.concentration_fields["substrate"]
        at_cells = field.ravel()[grid.flat_indices(group.positions_um)]
        cumulative = sum(
            cell["channels"]["uptake.cumulative"] for cell in snapshot.cell_frame["cells"]
        )
        record = {
            "geometry": grid.geometry,
            "time_s": snapshot.cell_frame["time_s"],
            "cell_0_concentration_uM": float(at_cells[0]),
            "cell_1_concentration_uM": float(at_cells[1]),
            "cumulative_uptake_molecule": cumulative,
            "mass_balance_error_molecule": float(
                initial_inventory - field.sum() * world.grid.molecules_per_uM_voxel - cumulative
            ),
        }
        print(json.dumps(record, ensure_ascii=False))

    report(simulation.current)
    for _ in range(4):
        report(simulation.step(1))


if __name__ == "__main__":
    main()
