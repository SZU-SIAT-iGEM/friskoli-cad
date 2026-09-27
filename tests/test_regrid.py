from __future__ import annotations

import unittest

import numpy as np

from friskoli_cad.engine import GridDomain, SimulationError, remap_concentration


def molecules(field: np.ndarray, grid: GridDomain) -> float:
    return float(field.sum() * grid.molecules_per_uM_voxel)


class RegridTests(unittest.TestCase):
    def test_noninteger_thin_layer_transfer_preserves_amount(self):
        source = GridDomain.thin_layer(4, 2, 5, 5, 1)
        target = GridDomain.thin_layer(7, 3, 20 / 7, 10 / 3, 1)
        field = np.array([[[1, 2, 5, 3], [4, 8, 6, 7]]], dtype=float)
        transferred = remap_concentration(field, source, target)

        self.assertEqual(transferred.shape, (1, 3, 7))
        self.assertAlmostEqual(molecules(transferred, target), molecules(field, source), delta=1e-8)
        self.assertGreaterEqual(transferred.min(), field.min())
        self.assertLessEqual(transferred.max(), field.max())
        self.assertFalse(np.shares_memory(transferred, field))

        uniform = remap_concentration(np.full(source.shape, 10.0), source, target)
        np.testing.assert_allclose(uniform, 10.0, rtol=0, atol=1e-14)

    def test_overlap_weighting_at_noninteger_boundary(self):
        source = GridDomain.thin_layer(2, 1, 5, 1, 1)
        target = GridDomain.thin_layer(3, 1, 10 / 3, 1, 1)
        field = np.array([[[0.0, 12.0]]])
        transferred = remap_concentration(field, source, target)

        np.testing.assert_allclose(transferred[0, 0], [0.0, 6.0, 12.0], atol=1e-12)
        self.assertAlmostEqual(molecules(transferred, target), molecules(field, source))

    def test_noninteger_3d_transfer_preserves_amount(self):
        source = GridDomain.volume(2, 2, 2, 5, 5, 5)
        target = GridDomain.volume(3, 4, 5, 10 / 3, 2.5, 2)
        field = (np.arange(8, dtype=float) + 1).reshape(source.shape)
        transferred = remap_concentration(field, source, target)

        self.assertEqual(transferred.shape, (5, 4, 3))
        self.assertAlmostEqual(molecules(transferred, target), molecules(field, source), delta=1e-7)
        uniform = remap_concentration(np.full(source.shape, 10.0), source, target)
        np.testing.assert_allclose(uniform, 10.0, rtol=0, atol=1e-13)

    def test_invalid_field_or_extent_is_rejected(self):
        source = GridDomain.thin_layer(2, 1, 5, 1, 1)
        target = GridDomain.thin_layer(3, 1, 10 / 3, 1, 1)
        for field in (np.zeros((1, 1, 3)), np.array([[[-1.0, 2.0]]]), np.array([[[1.0, np.nan]]])):
            with self.subTest(field=field):
                with self.assertRaises(SimulationError) as caught:
                    remap_concentration(field, source, target)
                self.assertEqual(caught.exception.code, "grid.remap")
        with self.assertRaises(SimulationError) as caught:
            remap_concentration(np.ones(source.shape), source, GridDomain.thin_layer(3, 1, 3, 1, 1))
        self.assertEqual(caught.exception.code, "grid.remap")
        with self.assertRaises(SimulationError) as caught:
            remap_concentration(
                np.ones(source.shape), source,
                GridDomain.volume(3, 1, 2, 10 / 3, 1, 0.5),
            )
        self.assertEqual(caught.exception.code, "grid.remap")


if __name__ == "__main__":
    unittest.main()
