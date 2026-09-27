"""Show conservation for a four-way split and a noninteger grid change."""

from __future__ import annotations

import json

import numpy as np

from friskoli_cad.engine import GridDomain, remap_concentration


def amount(field: np.ndarray, grid: GridDomain) -> float:
    return float(field.sum() * grid.molecules_per_uM_voxel)


def main() -> None:
    coarse = GridDomain.thin_layer(1, 1, 5, 5, 1)
    quarters = GridDomain.thin_layer(2, 2, 2.5, 2.5, 1)
    original = np.full(coarse.shape, 10.0)
    split = remap_concentration(original, coarse, quarters)
    print(json.dumps({
        "case": "four_way_split",
        "old_concentration_uM": float(original[0, 0, 0]),
        "new_concentrations_uM": split[0].tolist(),
        "old_voxel_molecules": amount(original, coarse),
        "new_voxel_molecules": (
            split * quarters.molecules_per_uM_voxel
        )[0].tolist(),
        "new_total_molecules": amount(split, quarters),
    }))

    two = GridDomain.thin_layer(2, 1, 5, 1, 1)
    three = GridDomain.thin_layer(3, 1, 10 / 3, 1, 1)
    uneven = np.array([[[0.0, 12.0]]])
    remapped = remap_concentration(uneven, two, three)
    print(json.dumps({
        "case": "two_to_three",
        "old_concentrations_uM": uneven[0, 0].tolist(),
        "new_concentrations_uM": remapped[0, 0].tolist(),
        "old_total_molecules": amount(uneven, two),
        "new_total_molecules": amount(remapped, three),
    }))


if __name__ == "__main__":
    main()
