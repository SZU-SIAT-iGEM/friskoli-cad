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


