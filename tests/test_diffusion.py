from friskoli_cad.engine.core import SimulationError
import unittest
import numpy as np
from friskoli_cad.engine.core import GridDomain
from friskoli_cad.engine.diffusion import explicit_no_flux_limit, no_flux_diffusion_rate

class DiffusionTests(unittest.TestCase):
    def test_face_transfers_spread_a_pulse_without_changing_total(self):
        thin = GridDomain.thin_layer(3, 1, 1, 1, 1)
        pulse = np.array([[[0.0, 10.0, 0.0]]])
        rate = no_flux_diffusion_rate(pulse, thin, 1)
        np.testing.assert_allclose(rate, [[[10.0, -20.0, 10.0]]])
        np.testing.assert_allclose(pulse + 0.25 * rate, [[[2.5, 5.0, 2.5]]])
        self.assertAlmostEqual(rate.sum(), 0)

        volume = GridDomain.volume(2, 2, 2, 1, 1, 1)
        pulse_3d = np.zeros(volume.shape)
        pulse_3d[0, 0, 0] = 10
        rate_3d = no_flux_diffusion_rate(pulse_3d, volume, 1)
        self.assertAlmostEqual(rate_3d[0, 0, 0], -30)
        for neighbor in ((0, 0, 1), (0, 1, 0), (1, 0, 0)):
            self.assertAlmostEqual(rate_3d[neighbor], 10)
        self.assertAlmostEqual(rate_3d.sum(), 0)
        self.assertAlmostEqual(explicit_no_flux_limit(volume, 1), 1 / 6)

    def test_uniform_field_and_zero_diffusivity_have_zero_rate(self):
        for grid in (GridDomain.thin_layer(3, 2, 2, 3, 1), GridDomain.volume(3, 2, 2, 2, 3, 4)):
            np.testing.assert_array_equal(no_flux_diffusion_rate(np.full(grid.shape, 10), grid, 5), 0)
            np.testing.assert_array_equal(no_flux_diffusion_rate(np.ones(grid.shape), grid, 0), 0)
        with self.assertRaises(SimulationError) as caught:
            explicit_no_flux_limit(GridDomain.thin_layer(2, 2, 1, 1, 1), -1)
        self.assertEqual(caught.exception.code, "diffusion.coefficient")

    def test_anisotropic_3d_faces_use_their_own_spacing(self):
        grid = GridDomain.volume(2, 2, 2, 1, 2, 4)
        pulse = np.zeros(grid.shape)
        pulse[0, 0, 0] = 10
        rate = no_flux_diffusion_rate(pulse, grid, 2)
        self.assertAlmostEqual(rate[0, 0, 0], -26.25)
        self.assertAlmostEqual(rate[0, 0, 1], 20)
        self.assertAlmostEqual(rate[0, 1, 0], 5)
        self.assertAlmostEqual(rate[1, 0, 0], 1.25)
        self.assertAlmostEqual(rate.sum(), 0)
        self.assertAlmostEqual(explicit_no_flux_limit(grid, 2), 1 / 5.25)

