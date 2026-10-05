"""Constructed molecule-accounting fixtures, not biological parameter claims."""

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import math

import numpy as np
import pytest

from friskoli_cad.engine.settlement import (
    InventorySnapshot, SettlementError, commit_settlement, propose_settlement,
)


def settle(stock=(10.0,), flux=(8.0, 12.0), ids=("a", "b"), weights=None, **kwargs):
    snapshot = InventorySnapshot("cellobiose", tuple(range(len(stock))), stock)
    if weights is None:
        weights = [[1.0] for _ in ids]
    return propose_settlement(snapshot, cell_ids=ids, support_cell_ids=ids,
                              support_weights=weights, requested_flux=flux,
                              dt_s=kwargs.pop("dt_s", 1.0), **kwargs)


def assert_independent_ledger(proposal):
    """Recompute from distinct output arrays, using exact binary64 fractions."""
    stock_before = sum(map(Fraction, proposal.before.amounts), Fraction())
    stock_after = sum(map(Fraction, proposal.after.amounts), Fraction())
    cells = sum(map(Fraction, proposal.accepted_amount), Fraction())
    residual = stock_before - stock_after - cells
    assert abs(residual) <= Fraction(proposal.ledger.total_conservation_bound)
    assert float(residual) == proposal.ledger.total_conservation_residual
    assert residual == Fraction(proposal.ledger.total_conservation_residual_exact)
    for k, (before, after) in enumerate(zip(proposal.before.amounts, proposal.after.amounts, strict=True)):
        transferred = sum((Fraction(row[k]) for row in proposal.accepted_by_support), Fraction())
        assert transferred <= Fraction(before)
        assert Fraction(before) - Fraction(after) - transferred == Fraction(proposal.ledger.conservation_residual[k])
        assert Fraction(before) - Fraction(after) - transferred == Fraction(proposal.ledger.conservation_residual_exact[k])
    assert all(v >= 0 and math.isfinite(v) for v in proposal.after.amounts)


def test_shared_inventory_hand_calculation_and_strict_default():
    with pytest.raises(SettlementError, match="insufficient"):
        settle()
    p = settle(policy="proportional")
    assert p.requested_amount == (8, 12)
    assert p.accepted_amount == (4, 6)
    assert p.accepted_flux == (4, 6)
    assert p.after.amounts == (0,)
    assert dict(p.accepted_by_cell) == {"a": 4, "b": 6}
    assert_independent_ledger(p)


@pytest.mark.parametrize("order", [(2, 0, 1), (1, 2, 0), (2, 1, 0)])
def test_cell_permutation_invariance_with_nonbinary_support(order):
    ids = (101, 202, 303)
    flux = (8.0, 12.0, 0.31)
    weights = ((0.3, 0.7), (0.6, 0.4), (0.2, 0.8))
    base = settle((4.0, 10.0), flux, ids, weights, policy="proportional")
    shuffled = settle((4.0, 10.0), tuple(flux[i] for i in order), tuple(ids[i] for i in order),
                      tuple(weights[i] for i in order), policy="proportional")
    assert shuffled.after == base.after
    assert dict(shuffled.accepted_by_cell) == dict(base.accepted_by_cell)
    assert shuffled.ledger == base.ledger
    assert_independent_ledger(base)


def test_multireservoir_columns_are_independent_and_no_demand_redistribution():
    # Requests: A=[2,6], B=[6,6]. Reservoir 0 has only 4; reservoir 1 has 20.
    p = settle((4, 20), (8, 12), weights=((0.25, 0.75), (0.5, 0.5)), policy="proportional")
    assert p.accepted_by_support == ((1, 6), (3, 6))
    assert p.accepted_amount == (7, 9)
    assert p.after.amounts == (0, 8)
    assert_independent_ledger(p)


def test_reservoir_permutation_invariance():
    a = settle((4, 20), (8, 12), weights=((0.25, 0.75), (0.5, 0.5)), policy="proportional")
    b = settle((20, 4), (8, 12), weights=((0.75, 0.25), (0.5, 0.5)), policy="proportional")
    assert a.accepted_amount == b.accepted_amount
    assert a.after.amounts == b.after.amounts[::-1]


@pytest.mark.parametrize("stock,flux,accepted", [((0,), (2, 3), (0, 0)), ((10,), (0, 0), (0, 0)), ((10,), (4, 6), (4, 6))])
def test_zero_and_exact_exhaustion(stock, flux, accepted):
    p = settle(stock, flux, policy="proportional")
    assert p.accepted_amount == accepted
    assert_independent_ledger(p)


def test_empty_population_is_valid_and_preserves_stock():
    p = settle((3, 7), (), (), (), policy="strict")
    assert p.after.amounts == (3, 7)
    assert p.accepted_amount == ()
    assert_independent_ledger(p)


def test_amount_and_flux_have_distinct_dt_semantics():
    p = settle((10,), (16, 24), dt_s=0.5, policy="proportional")
    assert p.requested_amount == (8, 12)
    assert p.accepted_amount == (4, 6)
    assert p.accepted_flux == (8, 12)
    assert_independent_ledger(p)


@pytest.mark.parametrize("dt", [0, -1, math.nan, math.inf, -math.inf, True])
def test_invalid_dt_rejected(dt):
    with pytest.raises(SettlementError):
        settle(dt_s=dt)


@pytest.mark.parametrize("bad", [-1, math.nan, math.inf, -math.inf, True])
def test_invalid_amount_flux_and_support_rejected(bad):
    with pytest.raises(SettlementError):
        settle((bad,))
    with pytest.raises(SettlementError):
        settle(flux=(bad, 0))
    with pytest.raises(SettlementError):
        settle(weights=((bad,), (1,)))


@pytest.mark.parametrize("ids", [("a", "a"), (1, 1), ("", "b"), (" ", "b"), (True, 2), (1.0, 2)])
def test_bad_cell_ids_rejected(ids):
    with pytest.raises(SettlementError):
        settle(ids=ids)


def test_support_ids_cannot_be_silently_realigned_by_length():
    with pytest.raises(SettlementError, match="same order"):
        propose_settlement(InventorySnapshot("s", (0,), (10,)), cell_ids=(1, 2),
                           support_cell_ids=(2, 1), support_weights=((1,), (1,)),
                           requested_flux=(1, 2), dt_s=1)


@pytest.mark.parametrize("weights", [((0,), (1,)), ((2,), (1,)), ((0.99,), (1,)), ((1, 0), (1, 0)), ((1,),), (1, 1)])
def test_bad_support_rows_and_shapes(weights):
    with pytest.raises(SettlementError):
        settle(weights=weights)


def test_bad_inventory_identity_and_shape():
    for args in [("", (0,), (1,)), ("s", (0, 0), (1, 1)), ("s", (), ()), ("s", (0, 1), (1,))]:
        with pytest.raises(SettlementError):
            InventorySnapshot(*args)
    with pytest.raises(SettlementError):
        InventorySnapshot("s", (0,), (1,), revision=-1)
    with pytest.raises(SettlementError):
        settle(policy="first-come")


def test_overflow_and_underflow_rejected_without_mutation():
    with pytest.raises(SettlementError, match="overflows"):
        settle((1e308,), (1e308, 0), dt_s=10)
    with pytest.raises(SettlementError, match="overflows"):
        settle((1e308, 1e308), (0, 0), weights=((1, 0), (0, 1)))
    with pytest.raises(SettlementError, match="overflows"):
        settle((1e308,), (1e308, 1e308), policy="proportional")
    with pytest.raises(SettlementError, match="underflows"):
        settle(flux=(math.ulp(0.0), 0), dt_s=0.5)
    with pytest.raises(SettlementError, match="allocation underflows"):
        settle((1, 1), (math.ulp(0.0), 0), weights=((0.5, 0.5), (0.5, 0.5)))


def test_small_transfer_cannot_vanish_in_large_inventory():
    with pytest.raises(SettlementError, match="cannot represent"):
        settle((1e30,), (1, 0))


def test_ordinary_decimal_subtraction_has_explicit_roundoff_not_false_shortage():
    p = settle((10,), (0.1, 0))
    assert p.after.amounts == (9.9,)
    assert p.accepted_amount == (0.1, 0)
    assert p.ledger.conservation_residual[0] != 0
    assert p.ledger.conservation_bound[0] == math.ulp(9.9) / 2
    assert_independent_ledger(p)


@pytest.mark.parametrize("scale", [1e-100, 1e-15, 1, 1e100])
def test_scale_independent_mass_audit(scale):
    p = settle((scale * 10,), (scale * 8, scale * 12), policy="proportional")
    assert p.accepted_amount == pytest.approx((scale * 4, scale * 6), rel=1e-15, abs=0)
    assert_independent_ledger(p)


def test_strict_has_no_shortage_epsilon():
    with pytest.raises(SettlementError, match="insufficient"):
        settle((1,), (math.nextafter(1, math.inf), 0))


def test_original_numpy_arrays_detached_and_readonly_result():
    stock = np.array([10.0])
    flux = np.array([8.0, 12.0])
    weights = np.ones((2, 1))
    ids = np.array([7, 9])
    snapshot = InventorySnapshot("s", np.array([0]), stock)
    with pytest.raises(SettlementError):
        propose_settlement(snapshot, cell_ids=ids, support_cell_ids=ids,
                           support_weights=weights, requested_flux=flux, dt_s=1)
    p = propose_settlement(snapshot, cell_ids=ids, support_cell_ids=ids,
                           support_weights=weights, requested_flux=flux, dt_s=1, policy="proportional")
    np.testing.assert_array_equal(stock, [10])
    np.testing.assert_array_equal(flux, [8, 12])
    np.testing.assert_array_equal(weights, [[1], [1]])
    stock[0] = 999
    flux[:] = 0
    weights[:] = 0
    ids[:] = 0
    assert snapshot.amounts == (10,)
    assert p.cell_ids == (7, 9)
    assert p.requested_flux == (8, 12)
    assert p.support_weights == ((1,), (1,))
    with pytest.raises(FrozenInstanceError):
        p.dt_s = 2
    with pytest.raises(TypeError):
        p.accepted_by_cell[7] = 999


def test_commit_revision_and_owner_and_tampering():
    p = settle(policy="proportional")
    assert p.before.amounts == (10,)
    updated = commit_settlement(p.before, p)
    assert updated.amounts == (0,)
    assert updated.revision == 1
    with pytest.raises(SettlementError, match="stale"):
        commit_settlement(updated, p)
    with pytest.raises(SettlementError, match="mismatched"):
        commit_settlement(replace(p.before, owner_id="another"), p)
    with pytest.raises(SettlementError, match="altered"):
        commit_settlement(p.before, replace(p, accepted_amount=(5, 5)))
    assert p.before.amounts == (10,)


def test_independent_species_have_independent_ledgers():
    a = settle(policy="proportional")
    b_before = InventorySnapshot("glucose", (0,), (100,))
    b = propose_settlement(b_before, cell_ids=a.cell_ids, support_cell_ids=a.cell_ids,
                           support_weights=a.support_weights, requested_flux=(2, 3), dt_s=1)
    assert commit_settlement(a.before, a).amounts == (0,)
    assert commit_settlement(b.before, b).amounts == (95,)
    assert a.ledger.species == "cellobiose"
    assert b.ledger.species == "glucose"
    assert_independent_ledger(a)
    assert_independent_ledger(b)
    with pytest.raises(SettlementError):
        commit_settlement(b.before, a)
