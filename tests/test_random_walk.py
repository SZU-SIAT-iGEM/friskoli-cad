import json
import math
import unittest

import numpy as np

from friskoli_cad.engine.random_streams import RandomStreams
from friskoli_cad.engine.random_walk import (
    RandomWalkBudgetError, RandomWalkParameters, RandomWalkState,
    advance_random_walk, isotropic_heading,
)


KEY = dict(node_id="movement", group_id="population", cell_id="stable-cell-5")


def advance(position, state, dt, params, streams, **kwargs):
    return advance_random_walk(position, state, dt, params, streams, **KEY, **kwargs)


class RandomWalkTests(unittest.TestCase):
    def test_checkpoint_rejects_non_json_numbers_and_unknown_fields(self):
        state = RandomWalkState((1., 0., 0.), .2)
        for heading in ([True, 0, 0], ['1', 0, 0], (1., 0., 0.)):
            payload = state.to_dict()
            payload['heading'] = heading
            with self.subTest(heading=heading), self.assertRaises(ValueError):
                RandomWalkState.from_dict(payload)
        for key, value in (('remaining_wait_s', True), ('remaining_wait_s', '0.2'), ('unknown', 1)):
            payload = state.to_dict()
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                RandomWalkState.from_dict(payload)

    def test_split_and_unsplit_have_same_events_state_and_trajectory(self):
        for dimensions in (2, 3):
            with self.subTest(dimensions=dimensions):
                params = RandomWalkParameters(4., 7., dimensions)
                original = RandomWalkState((0., 1., 0.))
                whole_rng, split_rng = RandomStreams(87), RandomStreams(87)
                whole = advance((2, 3, 7), original, 4., params, whole_rng)
                position, state, elapsed, events = (2, 3, 7), original, 0., []
                for dt in (.125, .25, .125, 1., .5, 2.):
                    proposal = advance(position, state, dt, params, split_rng)
                    events.extend((elapsed + t, h) for t, h in proposal.events)
                    position, state = proposal.position_um, proposal.state
                    elapsed += dt
                self.assertGreater(len(events), 10)
                np.testing.assert_allclose(position, whole.position_um, atol=1e-12, rtol=0)
                self.assertEqual(state.heading, whole.state.heading)
                self.assertAlmostEqual(state.remaining_wait_s, whole.state.remaining_wait_s, places=13)
                np.testing.assert_allclose([t for t, _ in events], [t for t, _ in whole.events], atol=1e-13)
                self.assertEqual([h for _, h in events], [h for _, h in whole.events])
                self.assertEqual(whole_rng.to_dict(), split_rng.to_dict())

    def test_checkpoint_restores_remaining_wait_and_stream_positions(self):
        params = RandomWalkParameters(12, 3, 3)
        rng = RandomStreams(187)
        first = advance((0, 0, 4), RandomWalkState((1, 0, 0)), .6, params, rng)
        restored_rng = RandomStreams.from_dict(json.loads(json.dumps(rng.to_dict())))
        restored_state = RandomWalkState.from_dict(json.loads(json.dumps(first.state.to_dict())))
        left = advance(first.position_um, first.state, 2., params, rng)
        right = advance(first.position_um, restored_state, 2., params, restored_rng)
        self.assertEqual(left, right)
        self.assertEqual(rng.to_dict(), restored_rng.to_dict())

    def test_budget_or_transport_failure_does_not_change_committed_rng(self):
        params = RandomWalkParameters(2, 1, 2)
        committed = RandomStreams(2)
        initial = RandomWalkState((1, 0, 0), .01)
        before = committed.to_dict()
        with self.assertRaises(RandomWalkBudgetError):
            advance((0, 0, 1), initial, 5, params, committed.clone(), max_events=1)
        self.assertEqual(committed.to_dict(), before)
        def fail(*args):
            raise RuntimeError("collision rejected")
        with self.assertRaises(RuntimeError):
            advance((0, 0, 1), initial, 5, params, committed.clone(), transport=fail)
        self.assertEqual(committed.to_dict(), before)
        candidate, retry = committed.clone(), committed.clone()
        self.assertEqual(advance((0, 0, 1), initial, 5, params, candidate),
                         advance((0, 0, 1), initial, 5, params, retry))
        self.assertEqual(candidate.to_dict(), retry.to_dict())

    def test_zero_rate_retains_project_heading_and_draws_no_randomness(self):
        rng = RandomStreams(1)
        params = RandomWalkParameters(2, 0, 2)
        proposal = advance((1, 2, 4), RandomWalkState((0, 1, 0)), 3, params, rng, max_events=0)
        self.assertEqual(proposal.position_um, (1., 8., 4.))
        self.assertEqual(proposal.state.heading, (0., 1., 0.))
        self.assertEqual(proposal.events, ())
        self.assertEqual(rng.to_dict()["streams"], [])

    def test_zero_speed_still_has_tumbles_and_zero_dt_draws_nothing(self):
        rng = RandomStreams(12)
        params = RandomWalkParameters(0, 7, 2)
        initial = RandomWalkState((1, 0, 0))
        still = advance((2, 4, 6), initial, 0, params, rng)
        self.assertEqual(still.state, initial)
        self.assertEqual(rng.to_dict()["streams"], [])
        moved = advance((2, 4, 6), initial, 3, params, rng)
        self.assertEqual(moved.position_um, (2, 4, 6))
        self.assertGreater(moved.event_count, 0)
        self.assertTrue(all(h[2] == 0 for _, h in moved.events))

    def test_exact_boundary_turn_is_recorded_and_not_repeated(self):
        rng = RandomStreams(9)
        params = RandomWalkParameters(1, 2, 2)
        first = advance((0, 0, 0), RandomWalkState((1, 0, 0), .25), .25, params, rng)
        self.assertEqual(first.event_count, 1)
        self.assertEqual(first.events, ((.25, first.state.heading),))
        self.assertEqual(first.position_um, (.25, 0, 0))
        self.assertGreater(first.state.remaining_wait_s, 0)
        next_dt = first.state.remaining_wait_s * .5
        next_step = advance(first.position_um, first.state, next_dt, params, rng)
        self.assertEqual(next_step.event_count, 0)
        self.assertEqual(next_step.segments[0].heading, first.state.heading)

    def test_initial_heading_and_wait_are_preserved_before_first_event(self):
        rng = RandomStreams(1)
        original = RandomWalkState((0, -1, 0), 10)
        proposal = advance((0, 0, 3), original, .1, RandomWalkParameters(3, 2, 2), rng)
        self.assertEqual(proposal.position_um, (0, -.30000000000000004, 3))
        self.assertEqual(proposal.state.heading, original.heading)
        self.assertEqual(proposal.state.remaining_wait_s, 9.9)
        self.assertEqual(proposal.event_count, 0)

    def test_transport_receives_every_segment_and_reflection_is_carried(self):
        calls = []
        def reflect(start, heading, dt, speed):
            calls.append((start, heading, dt))
            return start, tuple(-v for v in heading)
        original = RandomWalkState((1, 0, 0), 10)
        proposal = advance((0, 0, 3), original, 1, RandomWalkParameters(3, 2, 2),
                           RandomStreams(1), transport=reflect)
        self.assertEqual(len(calls), 1)
        self.assertEqual(proposal.state.heading, (-1., 0., 0.))
        self.assertEqual(proposal.segments[0].end_heading, proposal.state.heading)

    def test_isotropy_in_plane_and_on_sphere(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                stream = RandomStreams(83).stream("n", "g", "c", "direction")
                directions = np.array([isotropic_heading(stream, dimension) for _ in range(20000)])
                np.testing.assert_allclose(np.linalg.norm(directions, axis=1), 1., atol=2e-15)
                self.assertLess(np.max(np.abs(directions.mean(axis=0))), .02)
                expected = [.5, .5, 0.] if dimension == 2 else [1 / 3] * 3
                np.testing.assert_allclose((directions**2).mean(axis=0), expected, atol=.015, rtol=0)
                self.assertLess(abs((directions[:, 0] * directions[:, 1]).mean()), .015)
                if dimension == 2:
                    self.assertTrue(np.all(directions[:, 2] == 0))

    def test_different_seed_changes_event_sequence(self):
        state, params = RandomWalkState((1, 0, 0)), RandomWalkParameters(3, 9, 3)
        self.assertNotEqual(advance((0, 0, 0), state, 3, params, RandomStreams(1)).events,
                            advance((0, 0, 0), state, 3, params, RandomStreams(2)).events)

    def test_exponential_waiting_times_have_reference_rate(self):
        stream = RandomStreams(290).stream("n", "g", "c", "wait")
        samples = np.array([stream.exponential(3.) for _ in range(20000)])
        self.assertAlmostEqual(float(samples.mean()), 1 / 3, delta=.01)
        self.assertAlmostEqual(float(samples.std()), 1 / 3, delta=.02)

    def test_invalid_parameters_state_dt_budget_and_transport_rejected(self):
        for args in ((-1, 1, 2), (1, -1, 2), (math.inf, 1, 2), (1, math.nan, 2),
                     (1, 1, 1), (1, 1, 2.), (True, 1, 2)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                RandomWalkParameters(*args)
        for heading in ((0, 0, 0), (2, 0, 0), (math.nan, 0, 0), (1, 0)):
            with self.assertRaises(ValueError):
                RandomWalkState(heading)
        for wait in (-1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                RandomWalkState((1, 0, 0), wait)
        params, state, rng = RandomWalkParameters(1, 1, 2), RandomWalkState((1, 0, 0)), RandomStreams(1)
        for dt in (-1, math.nan, math.inf, True):
            with self.assertRaises(ValueError):
                advance((0, 0, 0), state, dt, params, rng)
        for budget in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                advance((0, 0, 0), state, 1, params, rng, max_events=budget)
        with self.assertRaises(ValueError):
            advance((0, 0, 0), RandomWalkState((0, 0, 1)), 1, params, rng)
        with self.assertRaises(ValueError):
            advance((0, 0, 0), state, 1, params, rng,
                    transport=lambda *args: ((0, 0, 1), (1, 0, 0)))


if __name__ == "__main__":
    unittest.main()
