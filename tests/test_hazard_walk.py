import json
import numpy as np
import pytest

from friskoli_cad.engine.hazard_walk import HazardWalkParameters, HazardWalkState, advance_hazard_walk
from friskoli_cad.engine.random_streams import RandomStreams
from friskoli_cad.engine.walk_primitives import RandomWalkBudgetError

KEY = dict(node_id='motor', group_id='population', cell_id='stable-1')


def step(state, duration, rate, rng, *, mode='instant', dwell=0, position=(0, 0, 1), budget=10000):
    return advance_hazard_walk(position, state, duration,
        HazardWalkParameters(2, rate, 2, mode, dwell), rng, **KEY, max_events=budget)


def test_rate_changes_consume_saved_hazard_without_redrawing():
    rng = RandomStreams(42)
    initial = HazardWalkState((1, 0, 0), remaining_hazard=2.)
    first = step(initial, .5, 1., rng)
    assert first.state.remaining_hazard == 1.5
    assert not rng.to_dict()['streams']
    second = step(first.state, .5, 3., rng)
    assert second.events[0].time_s == .5
    assert second.state.remaining_hazard > 0
    assert len(second.events) == 1


def test_zero_rate_pauses_hazard_and_does_not_draw():
    rng = RandomStreams(42)
    state = HazardWalkState((1, 0, 0), remaining_hazard=.3)
    paused = step(state, 10., 0., rng)
    assert paused.state == state
    assert not rng.to_dict()['streams']
    resumed = step(paused.state, .3, 1., rng)
    assert resumed.events[0].time_s == .3


@pytest.mark.parametrize('mode,dwell', [('instant', 0), ('dwell', .17)])
def test_subdivisions_and_json_restoration_preserve_events(mode, dwell):
    whole_rng, split_rng = RandomStreams(7), RandomStreams(7)
    initial = HazardWalkState((1, 0, 0))
    whole = step(initial, 4., 4., whole_rng, mode=mode, dwell=dwell)
    position, state, elapsed, events = (0, 0, 1), initial, 0., []
    for duration in (.125, .375, .5, 1., 2.):
        part = step(state, duration, 4., split_rng, mode=mode, dwell=dwell, position=position)
        events.extend((elapsed + e.time_s, e.phase, e.heading) for e in part.events)
        elapsed += duration
        position, state = part.position_um, HazardWalkState.from_dict(json.loads(json.dumps(part.state.to_dict())))
        split_rng = RandomStreams.from_dict(json.loads(json.dumps(split_rng.to_dict())))
    assert len(events) == len(whole.events)
    np.testing.assert_allclose([e[0] for e in events], [e.time_s for e in whole.events], atol=1e-13)
    assert [(e[1], e[2]) for e in events] == [(e.phase, e.heading) for e in whole.events]
    np.testing.assert_allclose(position, whole.position_um, atol=1e-13)
    assert state.phase == whole.state.phase
    assert state.remaining_hazard == pytest.approx(whole.state.remaining_hazard)
    assert state.dwell_remaining_s == pytest.approx(whole.state.dwell_remaining_s)
    assert split_rng.to_dict() == whole_rng.to_dict()


def test_dwell_is_stationary_and_survives_rate_changes():
    rng = RandomStreams(3)
    state = HazardWalkState((1, 0, 0), remaining_hazard=.2)
    enter = step(state, .3, 1., rng, mode='dwell', dwell=.5)
    assert enter.position_um == (.4, 0., 1.)
    assert enter.state.phase == 'tumble'
    assert enter.state.dwell_remaining_s == pytest.approx(.4)
    finish = step(enter.state, .4, 100., rng, mode='dwell', dwell=.5, position=enter.position_um)
    assert finish.position_um == enter.position_um
    assert finish.state.phase == 'run'
    assert finish.events[0].time_s == pytest.approx(.4)


def test_failed_budget_does_not_change_committed_rng_and_retry_matches():
    committed = RandomStreams(9)
    before = committed.to_dict()
    state = HazardWalkState((1, 0, 0), remaining_hazard=.1)
    with pytest.raises(RandomWalkBudgetError):
        step(state, 2., 30., committed.clone(), budget=0)
    assert committed.to_dict() == before
    assert step(state, 2., 30., committed.clone()) == step(state, 2., 30., committed.clone())


def test_checkpoint_rejects_invalid_phase_clock_combinations():
    state = HazardWalkState((1, 0, 0))
    for patch in ({'phase': 'tumble'}, {'remaining_hazard': -1}, {'remaining_hazard': True},
                  {'dwell_remaining_s': 1}, {'unknown': 0}, {'heading': [True, 0, 0]}):
        with pytest.raises(ValueError):
            HazardWalkState.from_dict({**state.to_dict(), **patch})
