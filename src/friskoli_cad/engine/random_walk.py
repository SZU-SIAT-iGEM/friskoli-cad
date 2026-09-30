"""Unbiased event-driven run/tumble reference motion, without PTS coupling."""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral, Real


WALK_VERSION = "isotropic-poisson-run-tumble-v1"


def _nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return float(value)


def _vector(value, label):
    try:
        result = tuple(float(v) for v in value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must contain three finite numbers") from error
    if len(result) != 3 or not all(math.isfinite(v) for v in result):
        raise ValueError(f"{label} must contain three finite numbers")
    return result


def _heading(value):
    result = _vector(value, "heading")
    if not math.isclose(math.hypot(*result), 1., rel_tol=1e-10, abs_tol=1e-10):
        raise ValueError("heading must be a unit vector")
    return result


@dataclass(frozen=True)
class RandomWalkParameters:
    speed_um_s: float
    tumble_rate_s: float
    dimensions: int

    def __post_init__(self):
        object.__setattr__(self, "speed_um_s", _nonnegative(self.speed_um_s, "speed_um_s"))
        object.__setattr__(self, "tumble_rate_s", _nonnegative(self.tumble_rate_s, "tumble_rate_s"))
        if isinstance(self.dimensions, bool) or not isinstance(self.dimensions, Integral) or self.dimensions not in (2, 3):
            raise ValueError("dimensions must be 2 or 3")
        object.__setattr__(self, "dimensions", int(self.dimensions))


@dataclass(frozen=True)
class RandomWalkState:
    heading: tuple[float, float, float]
    remaining_wait_s: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "heading", _heading(self.heading))
        if self.remaining_wait_s is not None:
            object.__setattr__(self, "remaining_wait_s", _nonnegative(self.remaining_wait_s, "remaining_wait_s"))

    def to_dict(self):
        return {"version": WALK_VERSION, "heading": list(self.heading),
                "remaining_wait_s": self.remaining_wait_s}

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict) or payload.get("version") != WALK_VERSION:
            raise ValueError("Unsupported random walk checkpoint version")
        try:
            return cls(payload["heading"], payload["remaining_wait_s"])
        except KeyError as error:
            raise ValueError("Incomplete random walk checkpoint") from error


@dataclass(frozen=True)
class MotionSegment:
    start_um: tuple[float, float, float]
    end_um: tuple[float, float, float]
    time_start_s: float
    time_end_s: float
    heading: tuple[float, float, float]
    end_heading: tuple[float, float, float]


@dataclass(frozen=True)
class RandomWalkProposal:
    position_um: tuple[float, float, float]
    state: RandomWalkState
    segments: tuple[MotionSegment, ...]
    event_count: int
    events: tuple[tuple[float, tuple[float, float, float]], ...] = ()


class RandomWalkBudgetError(ValueError):
    """The candidate must be discarded, including its candidate RNG state."""


def isotropic_heading(stream, dimensions):
    if dimensions not in (2, 3) or isinstance(dimensions, bool):
        raise ValueError("dimensions must be 2 or 3")
    phi = 2 * math.pi * stream.uniform_open()
    if dimensions == 2:
        return math.cos(phi), math.sin(phi), 0.
    z = 2 * stream.uniform_open() - 1
    radius = math.sqrt(max(0., 1 - z * z))
    return radius * math.cos(phi), radius * math.sin(phi), z


def advance_random_walk(position_um, state, dt_s, parameters, streams, *,
                        node_id, group_id, cell_id, max_events=10000, transport=None):
    """Advance a single cell using caller-owned candidate streams.

    ``transport(start, heading, duration_s, speed_um_s) -> (end, end_heading)``
    optionally applies the caller's collision guard to each straight run. A
    reflection changes subsequent motion until the next isotropic tumble.
    Exceptions reject the proposal: the caller must discard its RNG clone.
    State/parameters are immutable; initial heading is always caller supplied.
    """
    position = _vector(position_um, "position_um")
    dt_s = _nonnegative(dt_s, "dt_s")
    if not isinstance(state, RandomWalkState) or not isinstance(parameters, RandomWalkParameters):
        raise ValueError("Explicit RandomWalkState and RandomWalkParameters are required")
    if isinstance(max_events, bool) or not isinstance(max_events, Integral) or max_events < 0:
        raise ValueError("max_events must be a nonnegative integer")
    heading, wait = state.heading, state.remaining_wait_s
    if parameters.dimensions == 2 and heading[2] != 0:
        raise ValueError("2D heading must have zero Z component")
    if dt_s == 0:
        return RandomWalkProposal(position, state, (), 0)
    rate = parameters.tumble_rate_s
    wait_stream = None
    direction_stream = None
    if rate > 0:
        wait_stream = streams.stream(node_id, group_id, cell_id, "tumble_wait")
        direction_stream = streams.stream(node_id, group_id, cell_id, "tumble_direction")
        if wait is None:
            wait = wait_stream.exponential(rate)
    elapsed, count, segments, events = 0., 0, [], []
    while elapsed < dt_s:
        remaining = dt_s - elapsed
        event = rate > 0 and wait <= remaining
        duration = wait if event else remaining
        if event and count >= max_events:
            raise RandomWalkBudgetError(f"Tumble event budget ({max_events}) exceeded")
        end_time = elapsed + duration if event else dt_s
        if duration > 0:
            if end_time == elapsed:
                raise ValueError("Tumble interval is below float64 time resolution")
            start, initial_heading = position, heading
            if transport is None:
                distance = parameters.speed_um_s * duration
                position = tuple(x + distance * v for x, v in zip(position, heading))
            else:
                position, heading = transport(position, heading, duration, parameters.speed_um_s)
            position, heading = _vector(position, "end_position_um"), _heading(heading)
            if parameters.dimensions == 2 and (heading[2] != 0 or position[2] != start[2]):
                raise ValueError("2D transport must preserve Z position and planar heading")
            segments.append(MotionSegment(start, position, elapsed, end_time, initial_heading, heading))
        elapsed = end_time
        if event:
            count += 1
            heading = isotropic_heading(direction_stream, parameters.dimensions)
            events.append((elapsed, heading))
            wait = wait_stream.exponential(rate)
        elif rate > 0:
            wait -= duration
    return RandomWalkProposal(position, RandomWalkState(heading, wait), tuple(segments), count, tuple(events))
