"""Finite-capsule clearance and conservative whole-proposal motion rejection.

Distances are micrometres. This is a kinematic safety guard, not contact dynamics.
See docs/science/collision-baseline.md for interpolation and numerical limits.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral
from typing import Callable, Sequence

import numpy as np


StableID = str | int
Vec3 = tuple[float, float, float]


def _id(value: StableID) -> StableID:
    if isinstance(value, bool) or not isinstance(value, (str, Integral)):
        raise ValueError("stable ID must be a string or integer")
    if isinstance(value, str) and not value.strip():
        raise ValueError("stable ID must not be empty")
    return int(value) if isinstance(value, Integral) else value


def _key(value: StableID) -> tuple[str, str]:
    return type(value).__name__, str(value)


def _vec(value, name: str) -> Vec3:
    array = np.asarray(value, dtype=float)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain three finite coordinates")
    return tuple(float(v) for v in array)


@dataclass(frozen=True)
class Capsule:
    cell_id: StableID
    position_um: Vec3
    heading: Vec3
    length_um: float
    diameter_um: float

    def __post_init__(self):
        object.__setattr__(self, "cell_id", _id(self.cell_id))
        object.__setattr__(self, "position_um", _vec(self.position_um, "position_um"))
        heading = _vec(self.heading, "heading")
        norm = math.hypot(*heading)
        if abs(norm - 1) > 1e-10:
            raise ValueError("heading must be a unit vector")
        object.__setattr__(self, "heading", tuple(v / norm for v in heading))
        length, diameter = float(self.length_um), float(self.diameter_um)
        if not math.isfinite(length) or not math.isfinite(diameter) or not 0 < diameter <= length:
            raise ValueError("capsule requires finite length_um >= diameter_um > 0")
        object.__setattr__(self, "length_um", length)
        object.__setattr__(self, "diameter_um", diameter)

    @property
    def spine(self) -> tuple[np.ndarray, np.ndarray]:
        offset = np.asarray(self.heading) * ((self.length_um - self.diameter_um) / 2)
        center = np.asarray(self.position_um)
        return center - offset, center + offset


@dataclass(frozen=True)
class BoxObstacle:
    obstacle_id: StableID
    lower_um: Vec3
    upper_um: Vec3

    def __post_init__(self):
        object.__setattr__(self, "obstacle_id", _id(self.obstacle_id))
        object.__setattr__(self, "lower_um", _vec(self.lower_um, "lower_um"))
        object.__setattr__(self, "upper_um", _vec(self.upper_um, "upper_um"))
        if any(a >= b for a, b in zip(self.lower_um, self.upper_um)):
            raise ValueError("box upper bounds must exceed lower bounds")


def _point_segment_sq(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    d = b - a
    dd = float(d @ d)
    t = float(np.clip((p - a) @ d / dd, 0, 1)) if dd else 0.0
    residual = p - (a + t * d)
    return float(residual @ residual)


def _cross3(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Fixed 3D cross product; preserve NumPy scalar operation ordering."""
    return np.array((a[1] * b[2] - a[2] * b[1],
                     a[2] * b[0] - a[0] * b[2],
                     a[0] * b[1] - a[1] * b[0]))


def _segment_distance(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    # The quadratic minimum lies on a boundary or at an interior stationary point.
    candidates = [_point_segment_sq(a, c, d), _point_segment_sq(b, c, d),
                  _point_segment_sq(c, a, b), _point_segment_sq(d, a, b)]
    u, v, w = b - a, d - c, a - c
    cross = _cross3(u, v)
    denominator = float(cross @ cross)
    if denominator > 0:
        # Cross products avoid subtracting almost equal aa*bb and ab*ab.
        s = float(_cross3(v, w) @ cross / denominator)
        t = float(_cross3(u, w) @ cross / denominator)
        if 0 <= s <= 1 and 0 <= t <= 1:
            residual = w + s * u - t * v
            candidates.append(float(residual @ residual))
    if not all(math.isfinite(value) for value in candidates):
        raise ValueError("segment distance is not numerically representable")
    return math.sqrt(max(0.0, min(candidates)))


def capsule_gap(a: Capsule, b: Capsule) -> float:
    """Spine distance minus radii: positive separation, zero contact, negative overlap."""
    return _segment_distance(*a.spine, *b.spine) - (a.diameter_um + b.diameter_um) / 2


def capsule_box_gap(capsule: Capsule, box: BoxObstacle) -> float:
    """Exact external clearance; negative is an overlap witness, not penetration depth."""
    a, b = capsule.spine
    direction = b - a
    lower, upper = np.asarray(box.lower_um), np.asarray(box.upper_um)
    knots = [0.0, 1.0]
    for axis in range(3):
        if direction[axis] != 0:
            for plane in (lower[axis], upper[axis]):
                t = float((plane - a[axis]) / direction[axis])
                if 0 < t < 1:
                    knots.append(t)
    knots = sorted(set(knots))

    def distance_sq(t):
        p = a + t * direction
        residual = p - np.clip(p, lower, upper)
        value = float(residual @ residual)
        if not math.isfinite(value):
            raise ValueError("box distance is not numerically representable")
        return value

    minimum = min(distance_sq(t) for t in knots)
    for left, right in zip(knots, knots[1:]):
        midpoint = a + (left + right) * 0.5 * direction
        active = (midpoint < lower) | (midpoint > upper)
        if not np.any(active):
            return -capsule.diameter_um / 2
        target = np.where(midpoint < lower, lower, upper)
        slope = direction[active]
        denominator = float(slope @ slope)
        if denominator:
            t = float(np.clip(-((a - target)[active] @ slope) / denominator, left, right))
            minimum = min(minimum, distance_sq(t))
    return math.sqrt(max(0.0, minimum)) - capsule.diameter_um / 2


def capsule_wall_gap(capsule: Capsule, extent_um: Sequence[float]) -> float:
    """Smallest inward clearance to all six walls, including the Z walls."""
    extent = np.asarray(_vec(extent_um, "extent_um"))
    if np.any(extent <= 0):
        raise ValueError("extent_um must be positive")
    a, b = capsule.spine
    radius = capsule.diameter_um / 2
    return float(min(np.min(np.minimum(a, b) - radius),
                     np.min(extent - np.maximum(a, b) - radius)))


@dataclass(frozen=True)
class Contact:
    cell_ids: tuple[StableID, ...]
    kind: str
    target_id: StableID | None
    reason: str


class InitialOverlapError(ValueError):
    """Invalid initial placement; diagnostics are separate from scientific cell death."""

    def __init__(self, contacts: Sequence[Contact]):
        self.contacts = tuple(contacts)
        super().__init__("initial capsule overlap/outside boundary: " + repr(self.contacts))


@dataclass(frozen=True)
class GuardResult:
    capsules: tuple[Capsule, ...]
    blocked_ids: tuple[StableID, ...]
    contacts: tuple[Contact, ...]
    evaluations: int
    policy: str = "conservative_reject_whole_proposal"


class _Path:
    def __init__(self, start: Capsule, end: Capsule):
        self.start, self.end = start, end
        self.origin = np.asarray(start.position_um)
        self.delta = np.asarray(end.position_um) - self.origin
        self.heading = np.asarray(start.heading)
        last = np.asarray(end.heading)
        dot = float(np.clip(self.heading @ last, -1, 1))
        residual = last - dot * self.heading
        sine = float(np.linalg.norm(residual))
        self.angle = math.atan2(sine, dot)
        if sine > 1e-14:
            self.tangent = residual / sine
        else:
            basis = np.eye(3)[int(np.argmin(np.abs(self.heading)))]
            tangent = basis - (basis @ self.heading) * self.heading
            self.tangent = tangent / np.linalg.norm(tangent)
            # Tiny near-parallel changes remain bounded by their measured angle.
            if dot > 0 and sine > 0:
                self.tangent = residual / sine
        self.bound = float(np.linalg.norm(self.delta)) + (start.length_um - start.diameter_um) / 2 * self.angle
        self.moving = start.position_um != end.position_um or start.heading != end.heading

    def at(self, t: float) -> Capsule:
        if t == 0:
            return self.start
        if t == 1:
            return self.end
        heading = self.heading * math.cos(t * self.angle) + self.tangent * math.sin(t * self.angle)
        return Capsule(self.start.cell_id, tuple(self.origin + t * self.delta), tuple(heading),
                       self.start.length_um, self.start.diameter_um)


def _certify(gap: Callable[[float], float], speed: float, tolerance: float,
             budget: int) -> tuple[str | None, int]:
    """A midpoint lower bound covers each complete interval, never just sampled points."""
    if not math.isfinite(speed):
        return "numerically_uncertain", 0
    if speed == 0:
        try:
            clearance = gap(0)
        except (ValueError, ArithmeticError):
            return "numerically_uncertain", 1
        if not math.isfinite(clearance):
            return "numerically_uncertain", 1
        return (None if clearance >= -tolerance else "collision"), 1
    pending = [(0.0, 1.0)]
    evaluations = 0
    while pending:
        if evaluations >= budget:
            return "budget_exhausted", evaluations
        left, right = pending.pop()
        mid = (left + right) / 2
        evaluations += 1
        try:
            clearance = gap(mid)
        except (ValueError, ArithmeticError):
            return "numerically_uncertain", evaluations
        if not math.isfinite(clearance):
            return "numerically_uncertain", evaluations
        if clearance < -tolerance:
            return "collision", evaluations
        if clearance - speed * ((right - left) / 2) >= tolerance:
            continue
        if mid == left or mid == right:
            return "numerically_uncertain", evaluations
        pending.append((mid, right))
        pending.append((left, mid))
    return None, evaluations


def contested_mask(centers, half_lengths, travel, extent, obstacles, margin, block=512):
    """True for cells whose swept sphere is not provably clear of walls, solids and other swept spheres.

    A capsule always lies inside the sphere of radius length/2 about its center, so the
    start center with radius length/2 + center travel bounds the whole path, rotation included.
    False cells cannot touch anything and need no exact certification.
    """
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    n = len(centers)
    if n == 0:
        return np.zeros(0, dtype=bool)
    reach = np.asarray(half_lengths, dtype=float) + np.asarray(travel, dtype=float)
    clear = np.all(centers - reach[:, None] >= margin, axis=1) & np.all(centers + reach[:, None] <= np.asarray(extent) - margin, axis=1)
    for box in obstacles:
        lower, upper = np.asarray(box.lower_um), np.asarray(box.upper_um)
        outside = np.maximum(np.maximum(lower - centers, centers - upper), 0.)
        clear &= np.linalg.norm(outside, axis=1) > reach + margin
    for lo in range(0, n, block):
        hi = min(lo + block, n)
        squared = np.zeros((hi - lo, n))
        for axis in range(3):
            delta = centers[lo:hi, axis, None] - centers[None, :, axis]
            squared += delta * delta
        close = squared <= (reach[lo:hi, None] + reach[None, :] + margin) ** 2
        close[np.arange(hi - lo), np.arange(lo, hi)] = False
        clear[lo:hi] &= ~close.any(axis=1)
    return ~clear


def guard_motion(
    start: Sequence[Capsule], end: Sequence[Capsule], *, extent_um: Sequence[float],
    obstacles: Sequence[BoxObstacle] = (), geometry: str = "volume",
    tolerance_um: float = 1e-9, max_subdivisions: int = 256, use_broad_phase: bool = True,
) -> GuardResult:
    """Accept entire safe paths or freeze entire proposals, resolving conflicts together.

    Endpoint poses specify linear translation and deterministic shortest-arc rotation
    over normalized time [0,1]. All cells, including stationary cells, must be supplied.
    A thin layer still has a real three-dimensional extent and finite-sized bodies.
    """
    extent = _vec(extent_um, "extent_um")
    if min(extent) <= 0 or geometry not in ("volume", "thin_layer"):
        raise ValueError("positive extent and volume/thin_layer geometry required")
    tolerance = float(tolerance_um)
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance_um must be finite and positive")
    if isinstance(max_subdivisions, bool) or not isinstance(max_subdivisions, Integral) or max_subdivisions < 1:
        raise ValueError("max_subdivisions must be a positive integer evaluation budget")
    if type(use_broad_phase) is not bool:
        raise ValueError("use_broad_phase must be boolean")
    start, end, obstacles = tuple(start), tuple(end), tuple(obstacles)
    first, last = {c.cell_id: c for c in start}, {c.cell_id: c for c in end}
    if len(first) != len(start) or len(last) != len(end) or first.keys() != last.keys():
        raise ValueError("start/end require unique identical cell ID sets")
    if len({b.obstacle_id for b in obstacles}) != len(obstacles):
        raise ValueError("obstacle IDs must be unique")
    ids = sorted(first, key=_key)
    obstacles = tuple(sorted(obstacles, key=lambda b: _key(b.obstacle_id)))
    for id_ in ids:
        if (first[id_].length_um, first[id_].diameter_um) != (last[id_].length_um, last[id_].diameter_um):
            raise ValueError("motion guard cannot change capsule dimensions")
    # Fixed absolute tolerance must dominate ordinary binary64 coordinate error.
    scale = max([1.0, *extent, *(abs(v) for c in start + end for v in c.position_um),
                 *(c.length_um for c in start),
                 *(abs(v) for b in obstacles for v in b.lower_um + b.upper_um)])
    if tolerance < 128 * np.finfo(float).eps * scale:
        raise ValueError("tolerance_um too small for coordinate scale")
    if use_broad_phase and ids:
        centers = np.asarray([first[i].position_um for i in ids]).reshape(-1, 3)
        travel = np.linalg.norm(np.asarray([last[i].position_um for i in ids]).reshape(-1, 3) - centers, axis=1)
        contested = contested_mask(centers, [first[i].length_um / 2 for i in ids], travel, extent, obstacles,
                                   tolerance + 1024 * np.finfo(float).eps * scale)
        ids = [i for i, flag in zip(ids, contested) if flag]
    # A capsule is contained in the sphere of radius total_length / 2 about
    # its center, regardless of orientation. A separated coordinate alone is
    # a lower bound on Euclidean center distance. Inflate the threshold for
    # floating-point subtraction/rounding; uncertain pairs use exact geometry.
    rounding_margin = 512 * np.finfo(float).eps * scale
    radii = np.asarray([first[cid].length_um / 2 for cid in ids])
    centers = np.asarray([first[cid].position_um for cid in ids]).reshape(-1, 3)
    def far_pairs(points, inflation):
        if not use_broad_phase:
            return np.zeros((len(ids), len(ids)), dtype=bool)
        threshold = radii[:, None] + radii[None, :] + inflation[:, None] + inflation[None, :] + tolerance + rounding_margin
        # O(N^2) cheap comparisons replace O(N^2) expensive segment distances.
        # Avoid an N x N x 3 temporary; peak workspace is a few N x N arrays.
        far = np.zeros((len(ids), len(ids)), dtype=bool)
        for axis in range(3):
            far |= np.abs(points[:, None, axis] - points[None, :, axis]) > threshold
        return far
    initial_far = far_pairs(centers, np.zeros(len(ids)))
    def far_box(center, radius, box, inflation=0.):
        if not use_broad_phase:
            return False
        padding = radius + inflation + tolerance + rounding_margin
        return any(center[k] < box.lower_um[k] - padding or center[k] > box.upper_um[k] + padding for k in range(3))
    initial = []
    for i, id_ in enumerate(ids):
        body = first[id_]
        if capsule_wall_gap(body, extent) < -tolerance:
            initial.append(Contact((id_,), "wall", "domain", "initial_overlap"))
        for box in obstacles:
            if far_box(body.position_um, body.length_um / 2, box):
                continue
            if capsule_box_gap(body, box) < -tolerance:
                initial.append(Contact((id_,), "obstacle", box.obstacle_id, "initial_overlap"))
        for j in np.flatnonzero(~initial_far[i, i + 1:]) + i + 1:
            other = ids[j]
            if capsule_gap(body, first[other]) < -tolerance:
                initial.append(Contact((id_, other), "cell", other, "initial_overlap"))
    if initial:
        raise InitialOverlapError(initial)
    blocked: set[StableID] = set()
    contacts: set[Contact] = set()
    evaluations = 0
    while True:
        paths = {id_: _Path(first[id_], first[id_] if id_ in blocked else last[id_]) for id_ in ids}
        # Only skip pairs whose lower bound proves that the OLD certifier's
        # first midpoint test would succeed. A merely disjoint swept AABB can
        # otherwise hide its budget_exhausted/numerically_uncertain diagnosis.
        midpoints = np.asarray([paths[cid].origin + .5 * paths[cid].delta for cid in ids]).reshape(-1, 3)
        half_bounds = np.asarray([paths[cid].bound / 2 for cid in ids])
        motion_far = far_pairs(midpoints, half_bounds)
        new_blocked: set[StableID] = set()

        def check(cell_ids, kind, target_id, gap, speed):
            nonlocal evaluations
            reason, count = _certify(gap, speed, tolerance, int(max_subdivisions))
            evaluations += count
            if reason:
                contacts.add(Contact(tuple(cell_ids), kind, target_id, reason))
                new_blocked.update(id_ for id_ in cell_ids if paths[id_].moving)

        for i, id_ in enumerate(ids):
            path = paths[id_]
            if path.moving:
                check((id_,), "wall", "domain", lambda t: capsule_wall_gap(path.at(t), extent), path.bound)
                for box in obstacles:
                    if far_box(midpoints[i], radii[i], box, half_bounds[i]):
                        continue
                    check((id_,), "obstacle", box.obstacle_id, lambda t: capsule_box_gap(path.at(t), box), path.bound)
            for j in np.flatnonzero(~motion_far[i, i + 1:]) + i + 1:
                other = ids[j]
                second = paths[other]
                if path.moving or second.moving:
                    check((id_, other), "cell", other, lambda t: capsule_gap(path.at(t), second.at(t)), path.bound + second.bound)
        new_blocked -= blocked
        if not new_blocked:
            break
        blocked.update(new_blocked)
    result = tuple(first[c.cell_id] if c.cell_id in blocked else last[c.cell_id] for c in start)
    ordered_contacts = tuple(sorted(contacts, key=lambda c: (tuple(_key(i) for i in c.cell_ids), c.kind,
                                                           _key(c.target_id) if c.target_id is not None else ("", ""), c.reason)))
    return GuardResult(result, tuple(sorted(blocked, key=_key)), ordered_contacts, evaluations)
