"""Constructed reference for contact-limited enzymatic release from finite solids.

This pure proposal releases soluble molecules; it does not transfer them into
cells. Geometry is fixed during a proposal. No measured biological defaults are
provided. For several materials, callers must share each cell's enzyme budget
across the contacted materials rather than reusing all copies for every box.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
import math
from numbers import Real
from types import MappingProxyType
from typing import Mapping, Sequence

from .collision import Capsule, BoxObstacle, StableID, capsule_box_gap
from .settlement import InventorySnapshot, SettlementLedger, SettlementError, propose_settlement


class DegradationError(ValueError):
    """Invalid or numerically unrepresentable degradation proposal."""


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise DegradationError(name + " must be numeric")
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise DegradationError(name + " must be representable") from error
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise DegradationError(name + " must be finite and " + ("positive" if positive else "nonnegative"))
    return value


@dataclass(frozen=True)
class DegradableBox:
    id: str
    species: str
    lower_um: tuple[float, float, float]
    upper_um: tuple[float, float, float]
    remaining_molecules: float

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip() or not isinstance(self.species, str) or not self.species.strip():
            raise DegradationError("material id and species must be nonempty strings")
        box = BoxObstacle(self.id, self.lower_um, self.upper_um)
        object.__setattr__(self, "lower_um", box.lower_um)
        object.__setattr__(self, "upper_um", box.upper_um)
        object.__setattr__(self, "remaining_molecules", _number(self.remaining_molecules, "remaining_molecules"))


@dataclass(frozen=True)
class DegradationProposal:
    material: DegradableBox
    released_molecules: float
    contributions_molecules: Mapping[StableID, float]
    requested_molecules: Mapping[StableID, float]
    contact_ids: tuple[StableID, ...]
    ledger: SettlementLedger

    def __post_init__(self):
        object.__setattr__(self, "contributions_molecules", MappingProxyType(dict(self.contributions_molecules)))
        object.__setattr__(self, "requested_molecules", MappingProxyType(dict(self.requested_molecules)))
        object.__setattr__(self, "contact_ids", tuple(self.contact_ids))


def propose_contact_degradation(material: DegradableBox, capsules: Sequence[Capsule],
    enzyme_copies: Mapping[StableID, float], *, kcat_s: float, contact_range_um: float,
    dt_s: float) -> DegradationProposal:
    """Share finite material among explicit contact requests kcat * copies * dt.

    A contact is step-start capsule-to-box clearance <= the finite declared
    reaction range. A blocked movement toward a distant box does not constitute
    contact. One enzyme copy releases at most kcat_s molecules per second.
    Zero enzyme, zero rate, exhausted material or no contact produces no release.
    The returned ledger exposes the floating-point conservation residual.
    """
    if not isinstance(material, DegradableBox):
        raise DegradationError("material must be DegradableBox")
    capsules = tuple(capsules)
    if any(not isinstance(c, Capsule) for c in capsules):
        raise DegradationError("capsules must contain Capsule values")
    ids = tuple(c.cell_id for c in capsules)
    if len(set(ids)) != len(ids):
        raise DegradationError("duplicate cell IDs")
    if not isinstance(enzyme_copies, Mapping) or set(enzyme_copies) != set(ids):
        raise DegradationError("enzyme_copies must explicitly cover exactly the capsule IDs")
    copies = {identifier: _number(enzyme_copies[identifier], "enzyme_copies") for identifier in ids}
    kcat = _number(kcat_s, "kcat_s")
    distance = _number(contact_range_um, "contact_range_um")
    dt = _number(dt_s, "dt_s", positive=True)
    box = BoxObstacle(material.id, material.lower_um, material.upper_um)
    contacts = tuple(c.cell_id for c in capsules if capsule_box_gap(c, box) <= distance)
    contact_set = set(contacts)
    flux = tuple(kcat * copies[identifier] if identifier in contact_set else 0.0 for identifier in ids)
    if any(not math.isfinite(value) for value in flux):
        raise DegradationError("enzyme turnover overflows")
    if any(identifier in contact_set and kcat > 0 and copies[identifier] > 0 and value == 0
           for identifier, value in zip(ids, flux)):
        raise DegradationError("enzyme turnover underflows")
    try:
        proposal = propose_settlement(InventorySnapshot(material.species, (material.id,),
            (material.remaining_molecules,), owner_id=material.id), cell_ids=ids,
            support_cell_ids=ids, support_weights=tuple((1.0,) for _ in ids),
            requested_flux=flux, dt_s=dt, policy="proportional")
    except SettlementError as error:
        raise DegradationError(str(error)) from error
    return DegradationProposal(replace(material, remaining_molecules=proposal.ledger.after[0]),
        proposal.ledger.total_accepted, proposal.accepted_by_cell,
        dict(zip(ids, proposal.requested_amount)), contacts, proposal.ledger)
