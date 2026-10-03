import unittest
import numpy as np

from friskoli_cad.engine.standard_modules import standard_modules
from friskoli_cad.engine.runtime import ModuleRegistry
from friskoli_cad.engine.module_api import StepContext, execute_module


class StandardModuleTests(unittest.TestCase):
    def setUp(self):
        self.modules = {m.manifest['id']: m for m in standard_modules()}

    def run_module(self, name, *, inputs, parameters=None, ids=('a', 'b'), world=None, sets=None):
        return execute_module(self.modules[name], StepContext(1., .1, 1, 'target', ids,
            world=world or {}, inputs=inputs, parameters=parameters or {}, entity_sets=sets or {}))

    def test_catalog_and_registered_unit_conversion(self):
        ModuleRegistry(self.modules.values(), 'modular-spatial-v1')
        result = self.run_module('units.concentration_um_to_mm_cells', inputs={'value': np.array([2., 3000.])}, parameters={'species': 'nutrient'})
        np.testing.assert_allclose(result.outputs['value'], [.002, 3.])

    def test_entity_map_follows_ids_when_order_and_cardinality_differ(self):
        result = self.run_module('mapping.scalar_by_id', inputs={'value': np.array([10., 20., 30.])},
            parameters={'source_population': 'population:source', 'mapping': {'a': 'z', 'b': 'x'}},
            sets={'population:source': ('x', 'y', 'z')})
        np.testing.assert_array_equal(result.outputs['value'], [30., 10.])
        with self.assertRaisesRegex(ValueError, 'explicit source ID'):
            self.run_module('mapping.scalar_by_id', inputs={'value': np.array([10.])},
                parameters={'source_population': 'population:source', 'mapping': {'a': 'x'}}, sets={'population:source': ('x',)})

    def test_empty_aggregation_has_no_fabricated_mean(self):
        result = self.run_module('aggregate.scalar', inputs={'value': np.empty(0)}, ids=(),
            parameters={'source_population': 'population:empty'}, sets={'population:empty': ()})
        self.assertEqual(result.outputs['sum'], 0.)
        self.assertIsNone(result.outputs['statistics']['mean'])

    def test_matched_sample_and_deposit_are_conservative_and_adjoint_near_walls(self):
        world = {'grid_shape_zyx': (2, 3, 4), 'spacing_xyz': (1., 1., 1.),
                 'molecules_per_uM_voxel': 602.214076, 'blocked': np.zeros((2, 3, 4), bool),
                 'positions_um': np.array([[.05, 1.2, .05], [3.9, 1.8, 1.95]])}
        world['blocked'][0, 1, 0] = True
        concentration = np.arange(24., dtype=float).reshape(2, 3, 4)
        amounts = np.array([3., 8.])
        sample = self.run_module('field.sample_trilinear', inputs={'field': concentration}, parameters={'species': 'x'}, world=world)
        deposit = self.run_module('field.deposit_trilinear', inputs={'amount': amounts}, parameters={'species': 'x'}, world=world)
        self.assertAlmostEqual(float(deposit.outputs['amount'].sum()), 11.)
        self.assertEqual(deposit.outputs['amount'][0, 1, 0], 0.)
        self.assertAlmostEqual(float(sample.outputs['concentration'] @ amounts), float(np.sum(concentration * deposit.outputs['amount'])))


if __name__ == '__main__':
    unittest.main()
