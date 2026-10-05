"""Immutable, finite-inventory reference settlement (no World mutation).

The exact-rational arithmetic is deliberately a reference implementation, not a
large-grid backend. Every reservoir is settled once against its step-start stock.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from numbers import Integral, Real
from types import MappingProxyType
from typing import Mapping, Sequence


StableID = str | int


class SettlementError(ValueError):
    """Invalid input, insufficient stock, or unrepresentable conservative step."""


def _ids(values: Sequence[StableID], name: str) -> tuple[StableID, ...]:
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (str, Integral)):
            raise SettlementError(f"{name} must contain string or integer stable IDs")
        value = int(value) if isinstance(value, Integral) else value
        if isinstance(value, str) and not value.strip():
            raise SettlementError(f"{name} contains an empty ID")
        result.append(value)
    if len(set(result)) != len(result):
        raise SettlementError(f"{name} contains duplicate IDs")
    return tuple(result)


def _number(value: Real, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise SettlementError(f"{name} must be a real number")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise SettlementError(f"{name} is not representable") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise SettlementError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _finite_sum(values: Sequence[float], name: str) -> float:
    try:
        result = math.fsum(values)
    except OverflowError as exc:
        raise SettlementError(f"{name} overflows") from exc
    if not math.isfinite(result):
        raise SettlementError(f"{name} is not finite")
    return result


def _floor_float(value: Fraction) -> float:
    """Largest nearby binary64 value no greater than the nonnegative exact value."""
    try:
        result = float(value)
    except OverflowError as exc:
        raise SettlementError("allocation overflows") from exc
    if not math.isfinite(result):
        raise SettlementError("allocation overflows")
    if Fraction(result) > value:
        result = math.nextafter(result, 0.0)
    if value > 0 and result == 0:
        raise SettlementError("allocation underflows")
    return result


@dataclass(frozen=True)
class InventorySnapshot:
    """One owner, one species, and reservoir amounts in molecule at a revision."""

    species: str
    reservoir_ids: tuple[StableID, ...]
    amounts: tuple[float, ...]
    revision: int = 0
    owner_id: str = "bulk"

    def __post_init__(self) -> None:
        if not isinstance(self.species, str) or not self.species:
            raise SettlementError("species must be a nonempty string")
        if not isinstance(self.owner_id, str) or not self.owner_id:
            raise SettlementError("owner_id must be a nonempty string")
        if isinstance(self.revision, bool) or not isinstance(self.revision, Integral) or self.revision < 0:
            raise SettlementError("revision must be a nonnegative integer")
        ids = _ids(self.reservoir_ids, "reservoir_ids")
        amounts = tuple(_number(v, "inventory amount") for v in self.amounts)
        if not ids or len(ids) != len(amounts):
            raise SettlementError("inventory needs matching, nonempty reservoir IDs and amounts")
        _finite_sum(amounts, "total inventory")
        object.__setattr__(self, "reservoir_ids", ids)
        object.__setattr__(self, "amounts", amounts)
        object.__setattr__(self, "revision", int(self.revision))


@dataclass(frozen=True)
class SettlementLedger:
    """Actual stock and accepted transfers, all amounts in molecule.

    Residual is evaluated from exact binary64 values: before - after - accepted.
    It is reported, never added to a cell or removed by normalizing the stock.
    """

    species: str
    owner_id: str
    reservoir_ids: tuple[StableID, ...]
    before: tuple[float, ...]
    after: tuple[float, ...]
    accepted_by_reservoir: tuple[float, ...]
    conservation_residual: tuple[float, ...]
    conservation_residual_exact: tuple[str, ...]
    conservation_bound: tuple[float, ...]
    total_before: float
    total_after: float
    total_accepted: float
    total_conservation_residual: float
    total_conservation_residual_exact: str
    total_conservation_bound: float


@dataclass(frozen=True)
class SettlementProposal:
    before: InventorySnapshot
    after: InventorySnapshot
    cell_ids: tuple[StableID, ...]
    support_cell_ids: tuple[StableID, ...]
    support_weights: tuple[tuple[float, ...], ...]
    requested_flux: tuple[float, ...]
    requested_amount: tuple[float, ...]
    accepted_amount: tuple[float, ...]
    accepted_flux: tuple[float, ...]
    accepted_by_support: tuple[tuple[float, ...], ...]
    dt_s: float
    policy: str
    ledger: SettlementLedger

    @property
    def accepted_by_cell(self) -> Mapping[StableID, float]:
        return MappingProxyType(dict(zip(self.cell_ids, self.accepted_amount, strict=True)))


def propose_settlement(
    snapshot: InventorySnapshot,
    *,
    cell_ids: Sequence[StableID],
    support_cell_ids: Sequence[StableID],
    support_weights: Sequence[Sequence[float]],
    requested_flux: Sequence[float],
    dt_s: float,
    policy: str = "strict",
) -> SettlementProposal:
    """Settle all cell requests together from a fixed support and inventory.

    Support has shape cell x reservoir. Rows must sum to one to within the
    input-summation roundoff bound; exact rational row normalization then defines
    the support. Zero rows are invalid, including for a zero-flux cell.
    ``strict`` rejects any oversubscribed column, with no shortage tolerance.
    ``proportional`` independently scales each oversubscribed column; unmet
    demand is not moved to another column. Cell ordering has no effect.
    """
    if not isinstance(snapshot, InventorySnapshot):
        raise SettlementError("snapshot must be InventorySnapshot")
    if policy not in ("strict", "proportional"):
        raise SettlementError("policy must be strict or proportional")
    dt = _number(dt_s, "dt_s", positive=True)
    ids = _ids(cell_ids, "cell_ids")
    support_ids = _ids(support_cell_ids, "support_cell_ids")
    if ids != support_ids:
        raise SettlementError("support_cell_ids must match cell_ids in the same order")
    flux = tuple(_number(v, "requested_flux") for v in requested_flux)
    if len(flux) != len(ids):
        raise SettlementError("requested_flux must match cell_ids")
    try:
        weights = tuple(tuple(_number(v, "support weight") for v in row) for row in support_weights)
    except TypeError as exc:
        raise SettlementError("support_weights must be a cell x reservoir matrix") from exc
    if len(weights) != len(ids) or any(len(row) != len(snapshot.amounts) for row in weights):
        raise SettlementError("support_weights must have shape cell x reservoir")
    rational_weights = []
    for row in weights:
        exact_sum = sum(map(Fraction, row), Fraction())
        # Each input weight is a rounded binary64 value; a half-ULP allowance
        # per entry plus one ULP at unity covers ordinary normalized supports.
        bound = sum((Fraction(math.ulp(v)) / 2 for v in row), Fraction(math.ulp(1.0)))
        if exact_sum == 0 or abs(exact_sum - 1) > bound:
            raise SettlementError("each support row must sum to one; zero rows are invalid")
        rational_weights.append(tuple(Fraction(v) / exact_sum for v in row))
    requested = tuple(v * dt for v in flux)
    if any(not math.isfinite(v) for v in requested):
        raise SettlementError("requested_flux * dt_s overflows")
    if any(j > 0 and amount == 0 for j, amount in zip(flux, requested, strict=True)):
        raise SettlementError("requested_flux * dt_s underflows")
    _finite_sum(requested, "total requested amount")
    demand = tuple(tuple(Fraction(amount) * w for w in row)
                   for amount, row in zip(requested, rational_weights, strict=True))
    column_demand = tuple(sum((row[k] for row in demand), Fraction())
                          for k in range(len(snapshot.amounts)))
    if policy == "strict" and any(d > Fraction(stock) for d, stock in zip(column_demand, snapshot.amounts, strict=True)):
        raise SettlementError("insufficient inventory for strict settlement")
    scales = tuple(min(Fraction(1), Fraction(stock) / d) if d else Fraction(1)
                   for d, stock in zip(column_demand, snapshot.amounts, strict=True))
    allocation = tuple(tuple(_floor_float(d * scale) for d, scale in zip(row, scales, strict=True))
                       for row in demand)
    accepted = tuple(_finite_sum(row, "cell accepted amount") for row in allocation)
    accepted_flux = tuple(amount / dt for amount in accepted)
    if any(not math.isfinite(v) for v in accepted_flux):
        raise SettlementError("accepted flux overflows")
    exact_debits = tuple(sum((Fraction(row[k]) for row in allocation), Fraction())
                         for k in range(len(snapshot.amounts)))
    debits = tuple(float(value) for value in exact_debits)
    remaining = tuple(float(Fraction(stock) - debit) for stock, debit in zip(snapshot.amounts, exact_debits, strict=True))
    residuals = tuple(Fraction(stock) - Fraction(after) - debit
                      for stock, after, debit in zip(snapshot.amounts, remaining, exact_debits, strict=True))
    # Nearest rounding of the exact remainder contributes at most half an ULP
    # of the remainder, with no fixed molecule floor. Reject a transfer whose
    # entire debit disappears, even if the remainder's ULP would allow it.
    if any(debit > 0 and before == after for before, after, debit in
           zip(snapshot.amounts, remaining, exact_debits, strict=True)):
        raise SettlementError("inventory subtraction cannot represent this transfer conservatively")
    bounds = tuple(math.ulp(after) / 2 if debit else 0.0
                   for after, debit in zip(remaining, exact_debits, strict=True))
    if any(abs(residual) > Fraction(bound) for residual, bound in zip(residuals, bounds, strict=True)):
        raise SettlementError("inventory subtraction cannot represent this transfer conservatively")
    after = InventorySnapshot(snapshot.species, snapshot.reservoir_ids, remaining,
                              snapshot.revision + 1, snapshot.owner_id)
    total_accepted = _finite_sum(accepted, "total accepted amount")
    total_residual = (sum(map(Fraction, snapshot.amounts), Fraction())
                      - sum(map(Fraction, remaining), Fraction())
                      - sum(map(Fraction, accepted), Fraction()))
    # Row totals introduce at most half an ULP each; they are included rather
    # than silently comparing the same rounded total on both sides of a ledger.
    total_bound_exact = sum(map(Fraction, bounds), Fraction()) + sum(
        (Fraction(math.ulp(v)) / 2 for v in accepted if v), Fraction())
    if abs(total_residual) > total_bound_exact:
        raise SettlementError("cell/inventory ledger does not conserve the transferred amount")
    total_bound = float(total_bound_exact)
    if Fraction(total_bound) < total_bound_exact:
        total_bound = math.nextafter(total_bound, math.inf)
    ledger = SettlementLedger(
        snapshot.species, snapshot.owner_id, snapshot.reservoir_ids,
        snapshot.amounts, remaining, debits, tuple(float(v) for v in residuals),
        tuple(str(v) for v in residuals), bounds,
        _finite_sum(snapshot.amounts, "total inventory"), _finite_sum(remaining, "total remaining"),
        total_accepted, float(total_residual), str(total_residual), total_bound,
    )
    return SettlementProposal(snapshot, after, ids, support_ids, weights, flux, requested,
                              accepted, accepted_flux, allocation, dt, policy, ledger)


def commit_settlement(current: InventorySnapshot, proposal: SettlementProposal) -> InventorySnapshot:
    """Return a validated new snapshot; an owner must atomically replace its state.

    This pure operation permits explicit branches from an immutable snapshot.
    Reusing a proposal against the committed revision is rejected as stale.
    """
    if not isinstance(proposal, SettlementProposal) or current != proposal.before:
        raise SettlementError("stale settlement or mismatched inventory owner/species")
    verified = propose_settlement(
        current, cell_ids=proposal.cell_ids, support_cell_ids=proposal.support_cell_ids,
        support_weights=proposal.support_weights, requested_flux=proposal.requested_flux,
        dt_s=proposal.dt_s, policy=proposal.policy,
    )
    if verified != proposal:
        raise SettlementError("settlement proposal has been altered")
    return verified.after
