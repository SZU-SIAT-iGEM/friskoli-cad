from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from friskoli_cad.engine import CellGroup, GridDomain, Simulation, SimulationError, World, default_registry
from friskoli_cad.engine.spatial import box_overlap_weights


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def make_simulation(grid: GridDomain, position: tuple[float, float, float], box: bool) -> Simulation:
    graph_name = "box_uptake.graph.json" if box else "uptake.graph.json"
    graph = json.loads((EXAMPLES / graph_name).read_text(encoding="utf-8"))
    if box and grid.geometry == "volume":
        for node in graph["nodes"]:
            if node["id"] in ("sampler", "deposit"):
                node["parameters"]["support_z_um"]["value"] = 2
    run = json.loads((EXAMPLES / "uptake.run.json").read_text(encoding="utf-8"))
    run["graph_id"] = graph["id"]
    group = CellGroup(
        "group_1", ("cell_0",), np.array([position]),
        np.array([[0, 0, 0, 1]]),
    )
    return Simulation(World(grid, {"group_1": group}), graph, run, default_registry())


class BoxSupportTests(unittest.TestCase):
    def test_exact_grid_planes_split_into_two_or_eight_voxels(self):
        thin = GridDomain.thin_layer(2, 1, 5, 5, 1)
        indices, weights = box_overlap_weights(thin, np.array([5, 2.5, 0.5]), (2, 2, 0.8))
        self.assertEqual(indices.tolist(), [0, 1])
        np.testing.assert_allclose(weights, [0.5, 0.5], rtol=0, atol=1e-15)

        volume = GridDomain.volume(2, 2, 2, 5, 5, 5)
        indices, weights = box_overlap_weights(volume, np.array([5, 5, 5]), (2, 2, 2))
        self.assertEqual(indices.tolist(), list(range(8)))
        np.testing.assert_allclose(weights, np.full(8, 1 / 8), rtol=0, atol=1e-15)

    def test_sampling_and_deposition_change_smoothly_across_grid_plane(self):
        grid = GridDomain.thin_layer(2, 1, 5, 5, 1)
        field = np.array([0.0, 10.0])
        left_voxels, left_weights = box_overlap_weights(grid, np.array([4.99, 2.5, 0.5]), (2, 2, 0.8))
        right_voxels, right_weights = box_overlap_weights(grid, np.array([5.01, 2.5, 0.5]), (2, 2, 0.8))
        sampled_left = np.dot(field[left_voxels], left_weights)
        sampled_right = np.dot(field[right_voxels], right_weights)
        self.assertAlmostEqual(sampled_left, 4.95)
        self.assertAlmostEqual(sampled_right, 5.05)

        box_left = make_simulation(grid, (4.99, 2.5, 0.5), True).step(1)
        box_right = make_simulation(grid, (5.01, 2.5, 0.5), True).step(1)
        nearest_left = make_simulation(grid, (4.99, 2.5, 0.5), False).step(1)
        nearest_right = make_simulation(grid, (5.01, 2.5, 0.5), False).step(1)
        box_difference = np.abs(
            box_left.concentration_fields["substrate"] - box_right.concentration_fields["substrate"]
        ).sum()
        nearest_difference = np.abs(
            nearest_left.concentration_fields["substrate"] - nearest_right.concentration_fields["substrate"]
        ).sum()
        self.assertLess(box_difference, nearest_difference / 20)

    def test_3d_deposition_preserves_cell_uptake(self):
        grid = GridDomain.volume(2, 2, 2, 5, 5, 5)
        simulation = make_simulation(grid, (5, 5, 5), True)
        after = simulation.step(1)
        field = after.concentration_fields["substrate"]
        loss = (10 * grid.voxel_count - field.sum()) * grid.molecules_per_uM_voxel
        self.assertAlmostEqual(loss, 1000, places=7)
        self.assertAlmostEqual(
            after.cell_frame["cells"][0]["channels"]["uptake.cumulative"], 1000
        )
        np.testing.assert_allclose(
            10 - field, np.full(grid.shape, 125 / grid.molecules_per_uM_voxel),
            rtol=0, atol=1e-14,
        )

    def test_support_clips_to_domain_and_invalid_dimensions_fail(self):
        grid = GridDomain.thin_layer(2, 1, 5, 5, 1)
        indices, weights = box_overlap_weights(grid, np.array([0.1, 2.5, 0.5]), (2, 2, 0.8))
        self.assertEqual(indices.tolist(), [0])
        self.assertAlmostEqual(weights.sum(), 1)
        with self.assertRaises(SimulationError) as caught:
            box_overlap_weights(grid, np.array([5, 2.5, 0.5]), (0, 2, 0.8))
        self.assertEqual(caught.exception.code, "spatial.support")

        graph = json.loads((EXAMPLES / "box_uptake.graph.json").read_text(encoding="utf-8"))
        sampler = next(node for node in graph["nodes"] if node["id"] == "sampler")
        sampler["parameters"]["support_x_um"]["value"] = 0
        run = json.loads((EXAMPLES / "uptake.run.json").read_text(encoding="utf-8"))
        run["graph_id"] = graph["id"]
        group = CellGroup("group_1", ("cell_0",), np.array([[5, 2.5, 0.5]]), np.array([[0, 0, 0, 1]]))
        with self.assertRaises(SimulationError) as caught:
            Simulation(World(grid, {"group_1": group}), graph, run, default_registry())
        self.assertEqual(caught.exception.code, "spatial.support")


if __name__ == "__main__":
    unittest.main()
