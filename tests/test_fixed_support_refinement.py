from __future__ import annotations

import unittest

from examples.runtime.fixed_support_refinement import run_case


class FixedSupportRefinementTests(unittest.TestCase):
    def test_fixed_support_approaches_fine_grid_reference_in_both_geometries(self):
        for geometry in ("thin_layer", "volume"):
            with self.subTest(geometry=geometry):
                coarse = run_case(geometry, 5.0)
                fine = run_case(geometry, 0.15625)
                self.assertLess(
                    abs(fine["spatial_difference_molecules"]),
                    abs(coarse["spatial_difference_molecules"]) / 10,
                )
                for result in (coarse, fine):
                    self.assertLess(abs(result["mass_balance_error_molecules"]), 1e-6)


if __name__ == "__main__":
    unittest.main()
