"""Replay the saved two-species project and print each external material account."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from friskoli_cad.project import simulation_from_project


PROJECT_PATH = Path(__file__).with_name("scheduled_inputs.project.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--geometry", choices=("thin_layer", "volume"), default="thin_layer")
    args = parser.parse_args()
    document = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    if args.geometry == "volume":
        document["domain"] = {
            "geometry": "volume", "counts_xyz": [4, 2, 3], "spacing_um_xyz": [5, 5, 5]
        }
        document["groups"]["group_1"]["positions_um"][0][2] = 7.5
    simulation = simulation_from_project(document)
    molecules_per_voxel = simulation.world.grid.molecules_per_uM_voxel
    initial = {
        name: field.sum() * molecules_per_voxel
        for name, field in simulation.current.concentration_fields.items()
    }
    for index in range(10):
        snapshot = simulation.current if index == 0 else simulation.step(0.25)
        uptake = snapshot.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
        species_accounts = {}
        for species in document["species"]:
            field_node = f"{species}_field"
            input_node = f"{species}_input"
            current = snapshot.concentration_fields[species].sum() * molecules_per_voxel
            external = snapshot.environment_fields[field_node]["cumulative_external"].values.sum()
            cell_uptake = uptake if species == "substrate" else 0
            species_accounts[species] = {
                "applied_input_rate_uM_s": float(
                    snapshot.environment_fields[input_node]["external_rate"].values.flat[0]
                ),
                "external_net_molecules": float(external),
                "cell_uptake_molecules": float(cell_uptake),
                "balance_error_molecules": float(initial[species] + external - cell_uptake - current),
            }
        print(json.dumps({"time_s": snapshot.cell_frame["time_s"], "species": species_accounts}))


if __name__ == "__main__":
    main()
