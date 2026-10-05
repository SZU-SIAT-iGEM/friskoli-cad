"""Constructed geometric and mass-balance fixtures; not experimental calibration."""
from dataclasses import replace
import math
import pytest
from friskoli_cad.engine.collision import Capsule
from friskoli_cad.engine.degradation import DegradableBox, DegradationError, propose_contact_degradation


def box(stock=100): return DegradableBox("solid", "s", (2,0,0), (4,4,4), stock)
def cell(identifier="a", x=1.5): return Capsule(identifier, (x,1,1), (0,1,0), 1,1)
def step(material=None, capsules=None, copies=None, **kwargs):
    return propose_contact_degradation(box() if material is None else material,
        [cell()] if capsules is None else capsules, {"a":2} if copies is None else copies,
        **{"kcat_s":3,"contact_range_um":0,"dt_s":.5,**kwargs})


def test_contact_releases_to_external_pool_without_mutating_inputs():
    material=box();capsules=[cell()];copies={"a":2}
    proposal=step(material,capsules,copies)
    assert proposal.released_molecules == 3
    assert proposal.contributions_molecules == {"a":3}
    assert proposal.material.remaining_molecules == 97
    assert material.remaining_molecules == 100 and copies == {"a":2}
    with pytest.raises(TypeError): proposal.contributions_molecules["a"] = 0


def test_declared_surface_range_excludes_distant_blocked_trajectories():
    assert step(capsules=[cell(x=1.4)], contact_range_um=.05).released_molecules == 0
    assert step(capsules=[cell(x=1.4)], contact_range_um=.11).released_molecules == 3
    assert step(capsules=[cell(x=-100)], contact_range_um=1).released_molecules == 0


def test_capsule_spine_geometry_not_center_distance_determines_contact():
    capsule=Capsule("a", (1,1,1), (1,0,0), 2,1)
    assert step(capsules=[capsule]).released_molecules == 3
    capsule=replace(capsule,heading=(0,1,0))
    assert step(capsules=[capsule]).released_molecules == 0


def test_shared_stock_scales_all_contacts_without_cell_order_bias():
    cells=[cell("a"),replace(cell("b"),position_um=(1.5,3,1))]
    proposal=step(box(6),cells,{"a":2,"b":6},dt_s=1)
    assert proposal.contributions_molecules == {"a":1.5,"b":4.5}
    assert proposal.released_molecules == 6 and proposal.material.remaining_molecules == 0
    reverse=step(box(6),cells[::-1],{"a":2,"b":6},dt_s=1)
    assert reverse.contributions_molecules == proposal.contributions_molecules
    assert proposal.ledger.total_conservation_residual == 0


@pytest.mark.parametrize("kwargs", [{"copies":{"a":0}}, {"kcat_s":0}, {"material":box(0)}, {"capsules":[],"copies":{}}])
def test_zero_mechanisms_do_not_release(kwargs): assert step(**kwargs).released_molecules == 0


@pytest.mark.parametrize("kwargs", [{"kcat_s":float("inf")}, {"contact_range_um":float("inf")},
    {"dt_s":0}, {"copies":{"a":-1}}, {"copies":{}}, {"copies":{"a":float("nan")}},
    {"kcat_s":1e308,"copies":{"a":1e308}}, {"contact_range_um":-1}])
def test_invalid_parameters_reject_without_partial_mutation(kwargs):
    material=box()
    with pytest.raises((DegradationError,ValueError)): step(material=material,**kwargs)
    assert material.remaining_molecules == 100


def test_fractional_stock_stays_nonnegative_and_ledger_bounds_roundoff():
    proposal=step(box(.1),[cell("a"),cell("b")],{"a":1,"b":2},dt_s=1)
    assert proposal.material.remaining_molecules >= 0
    assert proposal.released_molecules <= .1
    assert abs(proposal.ledger.total_conservation_residual) <= proposal.ledger.total_conservation_bound
