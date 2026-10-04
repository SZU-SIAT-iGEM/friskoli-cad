"""Hand-computed cohort metrics, including division, death and empty cohorts."""
import copy
import unittest

from friskoli_cad.engine.observations import initial_observation, advance_observation, observation_metrics, validate_observation_state


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.project = {'domain': {'counts_xyz': [10, 10, 1], 'spacing_um_xyz': [1, 1, 2]},
            'groups': {'a': {'ids': ['p'], 'positions_um': [[1, 2, 1]]}, 'empty': {'ids': [], 'positions_um': []}},
            'observation': {'id': 'right', 'label': 'Right', 'axis': 0,
                'region_lower_um': [5, 0, 0], 'region_upper_um': [10, 10, 2]}}
    def frame(self, t, positions):
        return {'time_s': t, 'frame_index': int(t), 'cells': [{'id': cid, 'group_id': 'a', 'position_um': pos} for cid, pos in positions.items()]}

    def test_arrival_residence_descendants_and_death(self):
        state = initial_observation(self.project, self.frame(0, {'p': [1, 2, 1]}))
        state = advance_observation(state, self.frame(1, {'p': [5, 2, 1]}))
        self.assertEqual(observation_metrics(state, self.frame(1, {'p': [5, 2, 1]}))['by_group']['a']['mean_residence_s'], 0)
        frame = self.frame(3, {'p': [6, 2, 1], 'child': [2, 2, 1]})
        state = advance_observation(state, frame)
        metric = observation_metrics(state, frame)['by_group']
        self.assertEqual(metric['a'], {'initial_count': 1, 'live_count': 2, 'mean_position_um': 4., 'drift_um_s': None, 'mean_displacement_um': 5,
            'region_fraction': .5, 'ever_arrived_fraction': 1, 'mean_residence_s': 2})
        self.assertIsNone(metric['empty']['ever_arrived_fraction'])
        state = advance_observation(state, self.frame(4, {'child': [2, 2, 1]}))
        state = advance_observation(state, self.frame(7, {}))
        metric = observation_metrics(state, self.frame(7, {}))['by_group']['a']
        self.assertEqual(metric['mean_residence_s'], 3)
        self.assertEqual(metric['ever_arrived_fraction'], 1)
        self.assertIsNone(metric['mean_displacement_um'])
        self.assertIsNone(metric['region_fraction'])
        self.assertEqual(validate_observation_state(state, self.project, self.frame(7, {})), state)

    def test_restore_validation_and_no_mutation(self):
        f = self.frame(0, {'p': [1, 2, 1]})
        state = initial_observation(self.project, f)
        before = copy.deepcopy(state)
        advance_observation(state, self.frame(1, {'p': [6, 2, 1]}))
        self.assertEqual(state, before)
        bad = copy.deepcopy(state); bad['cohort']['p']['residence_s'] = True
        with self.assertRaises(ValueError): validate_observation_state(bad, self.project, f)
        bad = copy.deepcopy(state); bad['cohort']['p']['last_position_um'] = [2, 2, 1]
        with self.assertRaises(ValueError): validate_observation_state(bad, self.project, f)
        with self.assertRaises(ValueError): advance_observation(state, f)


if __name__ == '__main__':
    unittest.main()
