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

    def test_contact_metrics_measure_the_capsule_not_the_centre(self):
        """A 2 x 0.8 capsule reaches half its length past its own centre.

        length_um is pole to pole, so the capsule body extends 1.0 um from the
        centre (0.6 um of spine plus the 0.4 um cap radius). With the box ending at
        x = 5 and the cell aligned with +X the surface gap is centre - 6.0, while
        the centre gap is centre - 5.0. At a centre of 7.0 both are outside the
        0.5 um range, and at 6.4 the surface gap is 0.4 while the centre gap is
        still 1.4. A metric that measured centre-to-box would miss every cell in
        that band, which is the mistake this one exists to avoid.
        """
        from friskoli_cad.engine.collision import BoxObstacle
        geometry = {'shape': 'capsule', 'length_um': 2., 'diameter_um': .8}
        def frame(t, x):
            return {'time_s': t, 'frame_index': int(t), 'cells': [{'id': 'p', 'group_id': 'a',
                'position_um': [x, 2., 1.], 'orientation_xyzw': [0., 0., 0., 1.], 'geometry': geometry}]}
        project = copy.deepcopy(self.project)
        project['observation']['contact_range_um'] = .5
        box = BoxObstacle('substrate', (0., 0., 0.), (5., 10., 2.))
        state = initial_observation(project, frame(0, 7.0), [box])
        self.assertEqual(observation_metrics(state, frame(0, 7.0), [box])['by_group']['a']['contact_fraction'], 0.)
        state = advance_observation(state, frame(1, 6.4), [box])
        metric = observation_metrics(state, frame(1, 6.4), [box])['by_group']['a']
        self.assertEqual(metric['contact_fraction'], 1.)
        self.assertEqual(metric['contact_cell_seconds'], 0.)   # left endpoint: t=0 was clear
        state = advance_observation(state, frame(2, 6.4), [box])
        metric = observation_metrics(state, frame(2, 6.4), [box])['by_group']['a']
        self.assertEqual(metric['contact_cell_seconds'], 1.)
        # Removing the declaration removes the keys rather than reporting zeros.
        plain = copy.deepcopy(self.project)
        other = initial_observation(plain, frame(0, 7.0), [box])
        self.assertNotIn('contact_fraction', observation_metrics(other, frame(0, 7.0), [box])['by_group']['a'])

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
