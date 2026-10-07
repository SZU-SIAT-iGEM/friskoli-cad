"""已退休的中心点运动原语（center-point motion）。

提取自 engine/motion.py。在 ``modular-spatial-v1`` 之前，整图运动由
``motion.periodic_turn`` 与 ``motion.reflective_run`` 两个模块按固定速度直行、
按固定间隔转向、碰到长方体边界反射来实现。当前运动改由
``engine/hazard_walk.py`` 的积分风险率行走推进，胶囊几何约束由
``engine/collision.py`` 负责，边界反射不再参与。

本文件只作追溯，不被任何模块导入，也不参与当前执行。
保留者：``heading_from_orientation`` 与 ``orientation_after_heading`` 仍留在
``engine/motion.py``。

原文依赖（归档不执行，仅记录）：
    import numpy as np
"""

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
