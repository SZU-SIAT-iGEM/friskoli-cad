"""Event-driven motion with integrated hazard and explicit tumble residence.

The rate is constant within a caller's integration interval. A changing rate
consumes the same saved unit-exponential threshold; it never redraws a clock.
The caller owns candidate RNG streams and commits them only with the proposal.
"""
from dataclasses import dataclass
import math
from numbers import Integral, Real

from .random_walk import isotropic_heading, RandomWalkBudgetError

WALK_VERSION = 'integrated-hazard-walk/v1'


def _number(value, label, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f'{label} must be finite and {"positive" if positive else "nonnegative"}')
    return float(value)


def _vector(value, label, unit=False):
    if len(value) != 3 or any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in value):
        raise ValueError(f'{label} must contain three finite numbers')
    result = tuple(float(v) for v in value)
    if unit and not math.isclose(math.hypot(*result), 1., rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError('heading must be a unit vector')
    return result


@dataclass(frozen=True)
class HazardWalkParameters:
    speed_um_s: float
    tumble_hazard_s: float
    dimensions: int
    mode: str = 'instant'
    dwell_s: float = 0.
    turn_kernel: str = 'isotropic'

    def __post_init__(self):
        for key in ('speed_um_s', 'tumble_hazard_s', 'dwell_s'):
            object.__setattr__(self, key, _number(getattr(self, key), key))
        if isinstance(self.dimensions, bool) or not isinstance(self.dimensions, Integral) or self.dimensions not in (2, 3):
            raise ValueError('dimensions must be 2 or 3')
        if self.mode not in ('instant', 'dwell') or (self.mode == 'dwell' and self.dwell_s <= 0):
            raise ValueError('mode must be instant or dwell with positive dwell_s')
        if self.mode == 'instant' and self.dwell_s != 0:
            raise ValueError('instant mode has zero dwell_s')
        if self.turn_kernel not in ('isotropic', 'rebuilt_normal', 'simplified_normal'):
            raise ValueError('Unknown turn kernel')


@dataclass(frozen=True)
class HazardWalkState:
    heading: tuple[float, float, float]
    phase: str = 'run'
    remaining_hazard: float | None = None
    dwell_remaining_s: float = 0.

    def __post_init__(self):
        object.__setattr__(self, 'heading', _vector(self.heading, 'heading', True))
        if self.phase not in ('run', 'tumble'):
            raise ValueError('phase must be run or tumble')
        if self.remaining_hazard is not None:
            object.__setattr__(self, 'remaining_hazard', _number(self.remaining_hazard, 'remaining_hazard'))
        object.__setattr__(self, 'dwell_remaining_s', _number(self.dwell_remaining_s, 'dwell_remaining_s'))
        if (self.phase == 'run' and self.dwell_remaining_s != 0) or (self.phase == 'tumble' and (self.remaining_hazard is not None or self.dwell_remaining_s <= 0)):
            raise ValueError('phase and pending clocks disagree')

    def to_dict(self):
        return {'version': WALK_VERSION, 'heading': list(self.heading), 'phase': self.phase,
                'remaining_hazard': self.remaining_hazard, 'dwell_remaining_s': self.dwell_remaining_s}

    @classmethod
    def from_dict(cls, value):
        if type(value) is not dict or set(value) != {'version', 'heading', 'phase', 'remaining_hazard', 'dwell_remaining_s'} or value['version'] != WALK_VERSION:
            raise ValueError('Invalid hazard walk checkpoint version or keys')
        if type(value['heading']) is not list or any(type(v) not in (int, float) for v in value['heading']):
            raise ValueError('Checkpoint heading must contain JSON numbers')
        if (value['remaining_hazard'] is not None and type(value['remaining_hazard']) not in (int, float)) or type(value['dwell_remaining_s']) not in (int, float):
            raise ValueError('Checkpoint clocks must be JSON numbers')
        return cls(value['heading'], value['phase'], value['remaining_hazard'], value['dwell_remaining_s'])


@dataclass(frozen=True)
class HazardEvent:
    time_s: float
    phase: str
    heading: tuple[float, float, float]


@dataclass(frozen=True)
class HazardSegment:
    start_s: float
    end_s: float
    phase: str
    heading: tuple[float, float, float]


@dataclass(frozen=True)
class HazardWalkProposal:
    position_um: tuple[float, float, float]
    state: HazardWalkState
    segments: tuple[HazardSegment, ...]
    events: tuple[HazardEvent, ...]

    @property
    def event_count(self):
        return len(self.events)


def turn_heading(heading, stream, dimensions, kernel):
    if kernel == 'isotropic':
        return isotropic_heading(stream, dimensions)
    # Explicit Box-Muller transform avoids hidden distribution caches.
    normal = math.sqrt(-2 * math.log(stream.uniform_open())) * math.cos(2 * math.pi * stream.uniform_open())
    angle_deg = max(0., 68. + 36. * normal)
    if kernel == 'simplified_normal':
        angle_deg = min(180., angle_deg)
    theta = math.radians(angle_deg)
    if dimensions == 2:
        theta *= -1 if stream.uniform_open() < .5 else 1
        x, y, _ = heading
        return (x * math.cos(theta) - y * math.sin(theta), x * math.sin(theta) + y * math.cos(theta), 0.)
    # Stable perpendicular basis with a uniform azimuth around the old heading.
    axis = (1., 0., 0.) if abs(heading[0]) < .8 else (0., 1., 0.)
    dot = sum(a * b for a, b in zip(axis, heading))
    v = tuple(a - dot * b for a, b in zip(axis, heading))
    norm = math.hypot(*v)
    v = tuple(a / norm for a in v)
    w = (heading[1] * v[2] - heading[2] * v[1], heading[2] * v[0] - heading[0] * v[2], heading[0] * v[1] - heading[1] * v[0])
    phi = 2 * math.pi * stream.uniform_open()
    result = tuple(math.cos(theta) * h + math.sin(theta) * (math.cos(phi) * a + math.sin(phi) * b) for h, a, b in zip(heading, v, w))
    norm = math.hypot(*result)
    return tuple(v / norm for v in result)


def advance_hazard_walk(position_um, state, dt_s, parameters, streams, *, node_id, group_id, cell_id, max_events=10000):
    position = _vector(position_um, 'position_um')
    dt = _number(dt_s, 'dt_s')
    if not isinstance(state, HazardWalkState) or not isinstance(parameters, HazardWalkParameters):
        raise ValueError('Explicit hazard state and parameters required')
    if isinstance(max_events, bool) or not isinstance(max_events, Integral) or max_events < 0:
        raise ValueError('max_events must be a nonnegative integer')
    if parameters.dimensions == 2 and state.heading[2] != 0:
        raise ValueError('Planar heading must have zero Z component')
    if parameters.mode == 'instant' and state.phase != 'run':
        raise ValueError('Instant model cannot restore a dwell phase')
    if dt == 0:
        return HazardWalkProposal(position, state, (), ())
    heading, phase, hazard, dwell = state.heading, state.phase, state.remaining_hazard, state.dwell_remaining_s
    elapsed, segments, events = 0., [], []
    rate = parameters.tumble_hazard_s
    while elapsed < dt:
        if phase == 'run':
            if rate > 0 and hazard is None:
                hazard = streams.stream(node_id, group_id, cell_id, 'run_hazard').exponential(1.)
            duration_to_event = hazard / rate if rate > 0 else math.inf
        else:
            duration_to_event = dwell
        remaining = dt - elapsed
        event = duration_to_event <= remaining
        duration = duration_to_event if event else remaining
        if event and len(events) >= max_events:
            raise RandomWalkBudgetError(f'Hazard event budget ({max_events}) exceeded')
        end = elapsed + duration if event else dt
        if duration > 0:
            if end <= elapsed:
                raise ValueError('Hazard interval is below float64 time resolution')
            segments.append(HazardSegment(elapsed, end, phase, heading))
            if phase == 'run':
                position = tuple(x + parameters.speed_um_s * duration * h for x, h in zip(position, heading))
                if not all(math.isfinite(v) for v in position):
                    raise ValueError('Motion overflows position')
        elapsed = end
        if not event:
            if phase == 'run' and rate > 0:
                hazard = max(0., hazard - rate * duration)
            elif phase == 'tumble':
                dwell -= duration
            break
        if phase == 'run' and parameters.mode == 'dwell':
            heading = turn_heading(heading, streams.stream(node_id, group_id, cell_id, 'tumble_direction'), parameters.dimensions, parameters.turn_kernel)
            phase, hazard, dwell = 'tumble', None, parameters.dwell_s
        else:
            leaving_dwell = phase == 'tumble'
            phase, dwell = 'run', 0.
            if not leaving_dwell:
                heading = turn_heading(heading, streams.stream(node_id, group_id, cell_id, 'tumble_direction'), parameters.dimensions, parameters.turn_kernel)
            # Draw at the event boundary, even at the interval's right edge.
            # This makes subdivisions and checkpoint boundaries equivalent.
            hazard = streams.stream(node_id, group_id, cell_id, 'run_hazard').exponential(1.)
        events.append(HazardEvent(elapsed, phase, heading))
    return HazardWalkProposal(position, HazardWalkState(heading, phase, hazard, dwell), tuple(segments), tuple(events))
