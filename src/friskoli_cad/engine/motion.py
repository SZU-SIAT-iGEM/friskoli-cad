"""Geometry helpers for illustrative, center-point cell motion."""

from __future__ import annotations

import numpy as np


def heading_from_orientation(orientation_xyzw: np.ndarray) -> np.ndarray:
    """Rotate the body +X axis by each unit quaternion."""
    quaternions = np.asarray(orientation_xyzw, dtype=np.float64)
    quaternions = quaternions / np.linalg.norm(quaternions, axis=1)[:, None]
    x, y, z, w = quaternions.T
    return np.column_stack((
        1 - 2 * (y * y + z * z),
        2 * (x * y + w * z),
        2 * (x * z - w * y),
    ))


def orientation_after_heading(
    orientation_xyzw: np.ndarray, heading: np.ndarray
) -> np.ndarray:
    """Align the old body's +X axis to a new heading while retaining its roll."""
    previous = np.asarray(orientation_xyzw, dtype=np.float64)
    previous = previous / np.linalg.norm(previous, axis=1)[:, None]
    former = heading_from_orientation(previous)
    direction = np.asarray(heading, dtype=np.float64)
    direction = direction / np.linalg.norm(direction, axis=1)[:, None]
    rotation = np.column_stack((
        np.cross(former, direction), 1 + np.sum(former * direction, axis=1)
    ))
    opposite = np.linalg.norm(rotation, axis=1) < 1e-12
    if np.any(opposite):
        reference = np.zeros((int(opposite.sum()), 3), dtype=np.float64)
        reference[:, 0] = 1
        reference[np.abs(former[opposite, 0]) > 0.9] = (0, 1, 0)
        rotation[opposite, :3] = np.cross(former[opposite], reference)
        rotation[opposite, 3] = 0
    rotation /= np.linalg.norm(rotation, axis=1)[:, None]
    rotated_xyz = (
        rotation[:, 3, None] * previous[:, :3]
        + previous[:, 3, None] * rotation[:, :3]
        + np.cross(rotation[:, :3], previous[:, :3])
    )
    rotated_w = (
        rotation[:, 3] * previous[:, 3]
        - np.sum(rotation[:, :3] * previous[:, :3], axis=1)
    )
    return np.column_stack((rotated_xyz, rotated_w))


def turn_about_z(heading: np.ndarray, angles_rad: np.ndarray) -> np.ndarray:
    """Turn headings about the vertical axis without changing their Z component."""
    cosine, sine = np.cos(angles_rad), np.sin(angles_rad)
    result = np.array(heading, dtype=np.float64, copy=True)
    result[:, 0] = cosine * heading[:, 0] - sine * heading[:, 1]
    result[:, 1] = sine * heading[:, 0] + cosine * heading[:, 1]
    return result


def reflect_in_box(
    positions_um: np.ndarray, heading: np.ndarray, distance_um: float,
    extent_um: tuple[float, float, float], *, thin_layer: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Fold straight center-point paths at each box face, including repeated hits."""
    positions = np.array(positions_um, dtype=np.float64, copy=True)
    directions = np.array(heading, dtype=np.float64, copy=True)
    for axis, extent in enumerate(extent_um):
        if thin_layer and axis == 2:
            continue
        unfolded = positions[:, axis] + distance_um * directions[:, axis]
        phase = np.mod(unfolded, 2 * extent)
        positions[:, axis] = np.where(phase <= extent, phase, 2 * extent - phase)
        directions[:, axis] *= np.where(phase <= extent, 1, -1)
        # An exact face hit has already reflected for the next interval.
        lower = phase == 0
        upper = phase == extent
        directions[lower, axis] = np.abs(directions[lower, axis])
        directions[upper, axis] = -np.abs(directions[upper, axis])
        positions[upper, axis] = np.nextafter(extent, 0)
    return positions, directions
