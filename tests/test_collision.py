"""Analytic geometric fixtures only; no inferred biological parameter values."""
from dataclasses import replace
import math

import numpy as np
import pytest

from friskoli_cad.engine.collision import (
    BoxObstacle, Capsule, InitialOverlapError, capsule_box_gap, capsule_gap,
    capsule_wall_gap, guard_motion,
)


def body(id_="a", p=(5, 5, 5), h=(1, 0, 0), length=2, diameter=1):
    return Capsule(id_, p, h, length, diameter)


def guard(start, end, **kwargs):
    return guard_motion(start, end, extent_um=kwargs.pop("extent_um", (20, 20, 20)), **kwargs)


@pytest.mark.parametrize("heading", [(1, 0, 0), (-1, 0, 0)])
def test_parallel_and_reversed_finite_spines(heading):
    a = body(length=4)
    b = body("b", p=(5, 7, 5), h=heading, length=4)
    assert capsule_gap(a, b) == pytest.approx(1)
    end_to_end = body("b", p=(10, 5, 5), h=heading, length=4)
    assert capsule_gap(a, end_to_end) == pytest.approx(1)


def test_perpendicular_interior_closest_points_and_sphere_limit():
    a = body(length=6)
    b = body("b", p=(5, 5, 7), h=(0, 1, 0), length=6)
    assert capsule_gap(a, b) == pytest.approx(1)
    assert capsule_gap(a, replace(b, position_um=(5, 5, 5))) == pytest.approx(-1)
    sphere = body(length=1)
    diagonal = body("b", (8, 9, 5), length=1)
    assert capsule_gap(sphere, diagonal) == pytest.approx(4)


def test_near_parallel_crossing_segments_do_not_use_parallel_approximation():
    a = body(p=(0, 0, 0), length=4)
    epsilon = 1e-8
    b = body("b", (0, 0, 0), (math.cos(epsilon), math.sin(epsilon), 0), length=4)
    assert capsule_gap(a, b) == pytest.approx(-1)


@pytest.mark.parametrize("position,expected", [
    ((2, 5, 5), 1.5), ((2, 2, 5), math.sqrt(8) - .5),
    ((2, 2, 2), math.sqrt(12) - .5), ((5, 5, 5), -.5),
])
def test_box_face_edge_corner_and_inside(position, expected):
    box = BoxObstacle("cube", (4, 4, 4), (6, 6, 6))
    assert capsule_box_gap(body(p=position, length=1), box) == pytest.approx(expected)


def test_box_distance_minimum_in_spine_interior():
    box = BoxObstacle("cube", (4, 4, 4), (6, 6, 6))
    capsule = body(p=(5, 2, 5), length=10)
    assert capsule_box_gap(capsule, box) == pytest.approx(1.5)
    assert capsule_box_gap(replace(capsule, position_um=(5, 5, 5)), box) == -.5


def test_wall_uses_body_support_and_real_thin_layer_thickness():
    tilted = body(p=(5, 5, 1), h=(0, 0, 1), length=2)
    assert capsule_wall_gap(tilted, (10, 10, 2)) == 0
    assert not guard([tilted], [tilted], extent_um=(10, 10, 2), geometry="thin_layer").blocked_ids
    too_long = replace(tilted, length_um=3)
    with pytest.raises(InitialOverlapError) as error:
        guard([too_long], [too_long], extent_um=(10, 10, 2), geometry="thin_layer")
    assert error.value.contacts[0].kind == "wall"


def test_empty_population_and_stationary_tangent_spheres():
    assert guard([], []).capsules == ()
    a, b = body(length=1), body("b", (6, 5, 5), length=1)
    assert capsule_gap(a, b) == 0
    assert guard([a, b], [a, b]).blocked_ids == ()


def test_initial_pair_and_obstacle_overlap_are_explicit():
    a, b = body(), body("b", (5.2, 5, 5))
    with pytest.raises(InitialOverlapError) as error:
        guard([a, b], [a, b])
    assert error.value.contacts[0].cell_ids == ("a", "b")
    box = BoxObstacle("cube", (4, 4, 4), (6, 6, 6))
    with pytest.raises(InitialOverlapError) as error:
        guard([a], [a], obstacles=[box])
    assert error.value.contacts[0].target_id == "cube"


def test_safe_translation_and_rotation_are_accepted():
    a = body()
    end = replace(a, position_um=(6, 6, 6), heading=(0, 1, 0))
    result = guard([a], [end])
    assert result.capsules == (end,)
    assert not result.contacts


def test_high_speed_wall_exit_rejects_whole_proposal():
    a = body()
    end = replace(a, position_um=(10005, 5, 5))
    result = guard([a], [end])
    assert result.capsules == (a,)
    assert result.blocked_ids == ("a",)
    assert result.contacts[0].kind == "wall"


def test_obstacle_tunneling_with_legal_endpoints_is_blocked():
    a = body(p=(2, 5, 5), length=1)
    end = replace(a, position_um=(18, 5, 5))
    box = BoxObstacle("thin-barrier", (8, 0, 0), (8.01, 10, 10))
    assert capsule_box_gap(a, box) > 0 and capsule_box_gap(end, box) > 0
    result = guard([a], [end], obstacles=[box])
    assert result.blocked_ids == ("a",)
    assert any(c.target_id == "thin-barrier" for c in result.contacts)


def test_pair_swap_blocks_both_movers_despite_legal_endpoints():
    a, b = body(p=(4, 5, 5), length=1), body("b", (12, 5, 5), length=1)
    result = guard([a, b], [replace(a, position_um=b.position_um), replace(b, position_um=a.position_um)])
    assert result.blocked_ids == ("a", "b")
    assert result.capsules == (a, b)


def test_offset_collision_not_at_midpoint_still_blocks():
    a, b = body(p=(2, 5, 5), length=1), body("b", (6, 5, 5), length=1)
    end = replace(a, position_um=(18, 5, 5))
    assert capsule_gap(replace(a, position_um=(10, 5, 5)), b) > 0
    result = guard([a, b], [end, b])
    assert result.blocked_ids == ("a",)


def test_rotation_sweeps_into_obstacle_with_clear_endpoint_poses():
    a = body(p=(8, 8, 8), length=8)
    end = replace(a, heading=(0, 1, 0))
    box = BoxObstacle("corner", (10, 10, 7), (10.4, 10.4, 9))
    assert capsule_box_gap(a, box) > 0 and capsule_box_gap(end, box) > 0
    result = guard([a], [end], obstacles=[box])
    assert result.blocked_ids == ("a",)


def test_antiparallel_rotation_has_deterministic_sweep_and_spheres_do_not_sweep():
    a = body(p=(8, 8, 8), length=8)
    box = BoxObstacle("top", (7, 10, 7), (9, 10.4, 9))
    result = guard([a], [replace(a, heading=(-1, 0, 0))], obstacles=[box])
    assert result.blocked_ids == ("a",)
    sphere = replace(a, length_um=1)
    sphere_end = replace(sphere, heading=(-1, 0, 0))
    assert guard([sphere], [sphere_end], obstacles=[box]).capsules == (sphere_end,)


def test_freezing_a_blocked_mover_rechecks_following_mover():
    a = body("a", (6, 5, 5), length=1)
    b = body("b", (3, 5, 5), length=1)
    end_a = replace(a, position_um=(10, 5, 5))
    end_b = replace(b, position_um=(7, 5, 5))
    box = BoxObstacle("stop", (9, 4, 4), (9.1, 6, 6))
    result = guard([a, b], [end_a, end_b], obstacles=[box])
    assert result.blocked_ids == ("a", "b")
    assert result.capsules == (a, b)
    assert any(c.cell_ids == ("a", "b") for c in result.contacts)


def test_input_and_end_order_do_not_change_id_outcomes_or_diagnostics():
    a, b = body("a", (4, 5, 5), length=1), body("b", (12, 5, 5), length=1)
    c = body("c", (5, 12, 5), length=1)
    end = [replace(a, position_um=b.position_um), replace(b, position_um=a.position_um),
           replace(c, position_um=(6, 12, 5))]
    first = guard([a, b, c], end)
    second = guard([c, b, a], [end[1], end[2], end[0]])
    assert {x.cell_id: x for x in first.capsules} == {x.cell_id: x for x in second.capsules}
    assert first.blocked_ids == second.blocked_ids
    assert first.contacts == second.contacts
    assert first.evaluations == second.evaluations


def test_budget_exhaustion_is_blocked_result_without_cell_death():
    a = body(p=(2, 5, 5), length=1)
    end = replace(a, position_um=(18, 5, 5))
    result = guard([a], [end], max_subdivisions=1)
    assert result.blocked_ids == ("a",)
    assert any(c.reason == "budget_exhausted" for c in result.contacts)
    assert result.capsules[0].length_um == a.length_um


@pytest.mark.parametrize("bad", [(0, 0, 0), (2, 0, 0), (math.nan, 0, 0)])
def test_invalid_heading(bad):
    with pytest.raises(ValueError):
        body(h=bad)


def test_invalid_geometry_dimensions_ids_and_tolerance():
    with pytest.raises(ValueError):
        body(length=.1)
    a = body()
    for start, end in [([a, a], [a, a]), ([a], []), ([a], [replace(a, length_um=3)])]:
        with pytest.raises(ValueError):
            guard(start, end)
    with pytest.raises(ValueError, match="coordinate scale"):
        guard([a], [a], tolerance_um=1e-16)
