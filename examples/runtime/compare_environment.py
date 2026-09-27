"""Run the same uptake graph with two environment implementations."""

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
        "group_1", ("cell_0", "cell_1"), positions,
        np.array([[0, 0, 0, 1], [0, 0, 0, 1]]),
    )
    world = World(grid, {"group_1": group})
    registry = default_registry()
    simulations = {}
    for name, graph_file in (
        ("inventory", "uptake.graph.json"),
        ("reservoir", "reservoir.graph.json"),
    ):
        graph = load(graph_file)
        run = load("uptake.run.json")
        run["graph_id"] = graph["id"]
        run["run_id"] = f"{graph['id']}-run"
        simulations[name] = Simulation(world, graph, run, registry)

    for step in range(4):
        if step:
            for simulation in simulations.values():
                simulation.step(1)
        inventory = simulations["inventory"].current
        reservoir = simulations["reservoir"].current
        cell_voxel = grid.flat_indices(group.positions_um)[0]
        record = {
            "geometry": grid.geometry,
            "time_s": reservoir.cell_frame["time_s"],
            "inventory_cell_0_uM": float(inventory.concentration_fields["substrate"].ravel()[cell_voxel]),
            "reservoir_cell_0_uM": float(reservoir.concentration_fields["substrate"].ravel()[cell_voxel]),
            "inventory_uptake_molecule": sum(
                cell["channels"]["uptake.cumulative"] for cell in inventory.cell_frame["cells"]
            ),
            "reservoir_uptake_molecule": sum(
                cell["channels"]["uptake.cumulative"] for cell in reservoir.cell_frame["cells"]
            ),
            "reservoir_supply_molecule": float(
                reservoir.environment_fields["field"]["cumulative_supply"].values.sum()
            ),
        }
        print(json.dumps(record))


if __name__ == "__main__":
    main()
