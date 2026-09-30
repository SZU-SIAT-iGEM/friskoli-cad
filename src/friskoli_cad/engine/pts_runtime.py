"""Static, well-mixed PTS transactions with one settlement per bulk owner.

Only this versioned profile interprets a bulk-inventory binding as a resource
reference. All requests use the same step-start state; signals consume accepted
flux. No World, inventory, signal or time is committed on a rejected step.
"""
from copy import deepcopy
from dataclasses import fields
import math
from fractions import Fraction
from types import MappingProxyType

import numpy as np

from friskoli_cad.protocol import ProtocolError, FrameSequenceValidator
from friskoli_cad.science import pts
from .compiler import compile_graph
from .motion import heading_from_orientation
from .profiles import PTS_PROFILE, pts_schedule
from .runtime import MOLECULES_PER_UM3_PER_UM, SimulationError, Snapshot
from .settlement import InventorySnapshot, propose_settlement, commit_settlement

# A numerical acceptance budget relative to the material transferred this step,
# never to the (possibly enormous) stock. It is not a biological tolerance.
TRANSFER_RTOL = 1e-10


def _check_transfer_rounding(before, after, transferred):
    exact_delta = Fraction(float(after)) - Fraction(float(before))
    residual = abs(exact_delta - Fraction(float(transferred)))
    bound = TRANSFER_RTOL * abs(float(transferred)) + 8 * math.ulp(float(transferred))
    if residual > Fraction(bound):
        raise SimulationError("profile.precision", "Transfer rounding exceeds the profile's material budget")


def parameters(node, cls):
    return cls(**{entry.name: node.parameters[entry.name.lower()].value for entry in fields(cls)})


def _problem(message, path="/graph", code="profile.contract"):
    raise ProtocolError(code, path, message)


def validate_pts_project(project, manifests):
    """Validate ownership and time meaning beyond structural port matching."""
    if project.get("execution_profile") != PTS_PROFILE:
        _problem("Unsupported execution profile", "/execution_profile")
    if project["controls"]:
        _problem("This static bulk profile has no external schedule", "/controls")
    plan = compile_graph(project["graph"], manifests)
    groups = {node.owner_id for node in plan.nodes if node.owner_kind == "population"}
    if groups != set(project["groups"]):
        _problem("Every project group must be represented by the graph", "/groups")
    bulk = {node.id: node for node in plan.nodes if node.module_id == "bulk.finite_uniform"}
    if not bulk:
        _problem("A finite bulk inventory owner is required")
    species = [node.parameters["species"].value for node in bulk.values()]
    if len(set(species)) != len(species):
        _problem("Each bulk species must have exactly one inventory owner", code="profile.inventory_owner")
    owners = [node.owner_id for node in bulk.values()]
    if len(owners) != len(set(owners)):
        _problem("Bulk owner IDs must be unique", code="profile.inventory_owner")
    for node in bulk.values():
        if node.parameters["allocation_policy"].value not in ("strict", "proportional"):
            _problem("Allocation policy must be strict or proportional", code="profile.policy")
    intracellular_owners = set()
    for node in plan.nodes:
        for binding in node.inputs.values():
            source = plan.by_id[binding.source_node]
            expected = "previous_step" if source.id in bulk else "same_step"
            if binding.timing != expected:
                _problem(f"{node.id}: {source.id} must use {expected}", code="profile.timing")
        try:
            if node.module_id == "pts.capacity_rebuilt":
                parameters(node, pts.RebuiltCapacityParameters)
            elif node.module_id == "pts.capacity_simplified":
                parameters(node, pts.SimplifiedCapacityParameters)
            elif node.module_id == "uptake.pts_request":
                pts.pts_request(0., 0., node.parameters["turnover_s"].value,
                                node.parameters["half_saturation_um"].value)
            elif node.module_id == "signal.pts_accepted":
                p = parameters(node, pts.SignalParameters)
                pts.signal_readout(node.parameters["initial_ei_fraction"].value,
                                   node.parameters["initial_chey_p_um"].value, p)
        except ValueError as error:
            _problem(f"{node.id}: {error}", code="profile.parameter")
        if node.module_id == "bulk.sample_uniform":
            if node.inputs["concentration"].source_node not in bulk:
                _problem("Uniform sample must bind a bulk owner")
        if node.module_id == "uptake.bulk_settlement":
            owner = (node.owner_id, node.parameters["species"].value)
            if owner in intracellular_owners:
                _problem("A population/species has multiple intracellular owners", code="profile.intracellular_owner")
            intracellular_owners.add(owner)
            inventory = node.inputs["inventory"].source_node
            request = plan.by_id[node.inputs["requested_flux"].source_node]
            if inventory not in bulk or request.module_id != "uptake.pts_request":
                _problem("Settlement must bind a bulk owner and a PTS request")
            sample = plan.by_id[request.inputs["concentration"].source_node]
            if sample.module_id != "bulk.sample_uniform" or sample.inputs["concentration"].source_node != inventory:
                _problem("Sampling and settlement must use the same inventory owner", code="profile.support")
        if node.module_id == "signal.pts_accepted":
            source = plan.by_id[node.inputs["accepted_flux"].source_node]
            if source.module_id != "uptake.bulk_settlement":
                _problem("Signal must use accepted flux from settlement", code="profile.signal_source")


def _freeze(nested):
    result = {}
    for node_id, values in nested.items():
        frozen = {}
        for name, value in values.items():
            array = np.asarray(value, dtype=float).copy()
            if not np.isfinite(array).all():
                raise SimulationError("profile.number", f"{node_id}.{name} is not finite")
            array.setflags(write=False)
            frozen[name] = array
        result[node_id] = MappingProxyType(frozen)
    return MappingProxyType(result)


class PTSSimulation:
    def __init__(self, world, project, registry):
        self.world, self.registry = world, registry
        self.project = deepcopy(project)
        self.run = deepcopy(project["run"])
        self.plan = compile_graph(project["graph"], registry.manifests)
        self.schedule = pts_schedule(self.plan)
        self.time_s, self.frame_index = 0., 0
        self.frame_validator = FrameSequenceValidator(self.run)
        volume = math.prod(world.grid.extent_um)
        self._molecules_per_uM = volume * MOLECULES_PER_UM3_PER_UM
        if not math.isfinite(self._molecules_per_uM) or self._molecules_per_uM <= 0:
            raise SimulationError("profile.volume", "Bulk volume exceeds float64 range", "/domain")
        for group_id, group in world.groups.items():
            heading = heading_from_orientation(group.orientation_xyzw)
            for i, capsule in enumerate(group.geometry):
                if capsule is None:
                    raise SimulationError("geometry.missing", "PTS capacity needs explicit capsule geometry", f"/groups/{group_id}/initial_geometry/{i}")
                half_extent = .5 * (capsule.diameter_um + (capsule.length_um - capsule.diameter_um) * np.abs(heading[i]))
                if np.any(group.positions_um[i] - half_extent < 0) or np.any(group.positions_um[i] + half_extent > world.grid.extent_um):
                    raise SimulationError("cell.geometry", "Static capsule does not fit in the domain", f"/groups/{group_id}/initial_geometry/{i}")
        inventories = {}
        self._bulk_nodes = {n.id: n for n in self.plan.nodes if n.module_id == "bulk.finite_uniform"}
        for node in self._bulk_nodes.values():
            species = node.parameters["species"].value
            amount = project["species"][species]["initial_concentration"]["value"] * self._molecules_per_uM
            inventories[node.id] = InventorySnapshot(species, (node.owner_id,), (amount,), owner_id=node.owner_id)
        self.inventory = MappingProxyType(inventories)
        self.outputs, self.state, self.ledger = {}, {}, MappingProxyType({})
        outputs, state, _, _ = self._propose(None)
        self.outputs, self.state = _freeze(outputs), _freeze(state)
        self.current = self._snapshot(self.outputs, 0., 0)
        self.frame_validator.accept(self.current.cell_frame)

    def _bulk_outputs(self, snapshot):
        amount = snapshot.amounts[0]
        return {"inventory": np.asarray(amount), "concentration": np.asarray(amount / self._molecules_per_uM)}

    def _propose(self, dt):
        outputs, state, inventories, ledger = {}, {}, dict(self.inventory), {}
        old_bulk = {name: self._bulk_outputs(stock) for name, stock in self.inventory.items()}
        def inputs(node):
            return {name: (old_bulk if binding.timing == "previous_step" else outputs)[binding.source_node][binding.source_port]
                    for name, binding in node.inputs.items()}
        for node_id in self.schedule["prepare_nodes"]:
            node = self.plan.by_id[node_id]
            module = node.module_id
            p = node.parameters
            incoming = inputs(node)
            if module == "bulk.finite_uniform":
                value = old_bulk[node.id]
                state[node.id] = {"inventory": value["inventory"]}
            elif module == "pts.capsule_area":
                group = self.world.groups[node.owner_id]
                value = {"surface_area": pts.capsule_area_um2(
                    [c.length_um for c in group.geometry], [c.diameter_um for c in group.geometry])}
            elif module in ("pts.capacity_rebuilt", "pts.capacity_simplified"):
                cls, fn = (pts.RebuiltCapacityParameters, pts.rebuilt_capacity) if module.endswith("rebuilt") else (pts.SimplifiedCapacityParameters, pts.simplified_capacity)
                result = fn(incoming["surface_area"], p["g_requested"].value, parameters(node, cls))
                value = {"functional_copies": result.functional_copies, "g_effective": result.g_effective}
            elif module == "bulk.sample_uniform":
                value = {"concentration": np.full(len(self.world.groups[node.owner_id].ids), incoming["concentration"])}
            elif module == "uptake.pts_request":
                value = {"requested_flux": pts.pts_request(incoming["concentration"], incoming["functional_copies"], p["turnover_s"].value, p["half_saturation_um"].value)}
            else:
                raise SimulationError("profile.module", f"Unsupported mechanism {module}")
            outputs[node.id] = value
            state.setdefault(node.id, {})
        for group in self.schedule["settlements"]:
            bulk_id = group["inventory_node"]
            bulk = self._bulk_nodes[bulk_id]
            participants = [self.plan.by_id[n] for n in group["participant_nodes"]]
            cell_ids, requested = [], []
            for node in participants:
                cell_ids.extend(self.world.groups[node.owner_id].ids)
                requested.extend(inputs(node)["requested_flux"])
            if dt is None:
                accepted_amount = accepted_flux = np.zeros(len(cell_ids))
            else:
                proposal = propose_settlement(self.inventory[bulk_id], cell_ids=cell_ids,
                    support_cell_ids=cell_ids, support_weights=[(1.,)] * len(cell_ids),
                    requested_flux=requested, dt_s=dt, policy=bulk.parameters["allocation_policy"].value)
                inventories[bulk_id] = commit_settlement(self.inventory[bulk_id], proposal)
                _check_transfer_rounding(inventories[bulk_id].amounts[0], self.inventory[bulk_id].amounts[0],
                                         math.fsum(proposal.accepted_amount))
                ledger[bulk_id] = proposal.ledger
                accepted_amount, accepted_flux = np.asarray(proposal.accepted_amount), np.asarray(proposal.accepted_flux)
            offset = 0
            for node in participants:
                count = len(self.world.groups[node.owner_id].ids)
                amount, flux = accepted_amount[offset:offset + count], accepted_flux[offset:offset + count]
                before = (np.full(count, node.parameters["initial_intracellular_molecules"].value)
                          if dt is None else self.state[node.id]["cumulative_uptake"])
                cumulative = before + amount
                if np.any((amount > 0) & (cumulative == before)):
                    raise SimulationError("profile.precision", "Intracellular update is below float64 resolution")
                for old, new, transfer in zip(before, cumulative, amount, strict=True):
                    if not math.isfinite(float(new)):
                        raise SimulationError("profile.precision", "Intracellular inventory overflows")
                    _check_transfer_rounding(old, new, transfer)
                outputs[node.id] = {"accepted_amount": amount, "accepted_flux": flux, "cumulative_uptake": cumulative}
                state[node.id] = {"cumulative_uptake": cumulative}
                offset += count
            outputs[bulk_id] = self._bulk_outputs(inventories[bulk_id])
            state[bulk_id] = {"inventory": outputs[bulk_id]["inventory"]}
        for node_id in self.schedule["signal_nodes"]:
            node = self.plan.by_id[node_id]
            count = len(self.world.groups[node.owner_id].ids)
            p = parameters(node, pts.SignalParameters)
            if dt is None:
                result = pts.signal_readout(np.full(count, node.parameters["initial_ei_fraction"].value),
                                           np.full(count, node.parameters["initial_chey_p_um"].value), p)
            else:
                result = pts.advance_accepted_signal(inputs(node)["accepted_flux"],
                    self.state[node.id]["ei_fraction"], self.state[node.id]["chey_p"], dt, p)
            outputs[node.id] = {"ei_fraction": result.ei_fraction, "chey_p": result.chey_p_uM,
                                "chea_active": result.chea_active_uM, "motor_bias": result.motor_bias}
            state[node.id] = {"ei_fraction": result.ei_fraction, "chey_p": result.chey_p_uM}
        return outputs, state, inventories, ledger

    def _snapshot(self, outputs, time_s, frame_index):
        cells = []
        for group_id in sorted(self.world.groups):
            group = self.world.groups[group_id]
            for index, cell_id in enumerate(group.ids):
                capsule = group.geometry[index]
                channels = {key: float(outputs[channel["node"]][channel["port"]][index])
                            for key, channel in self.run["channels"].items() if channel["group_id"] == group_id}
                cells.append({"id": cell_id, "group_id": group_id,
                    "position_um": group.positions_um[index].tolist(),
                    "orientation_xyzw": group.orientation_xyzw[index].tolist(),
                    "geometry": {"shape": "capsule", "length_um": capsule.length_um, "diameter_um": capsule.diameter_um},
                    "channels": channels})
        frame = {"protocol_version": "0.1.0", "frame_version": "0.2.0", "run_id": self.run["run_id"],
                 "frame_index": frame_index, "time_s": time_s, "cells": cells, "events": []}
        return Snapshot(frame, MappingProxyType({}), MappingProxyType({}), MappingProxyType({}), self.world.grid)

    def step(self, dt_s):
        if type(dt_s) not in (int, float) or not math.isfinite(dt_s) or dt_s <= 0:
            raise SimulationError("time.step", "dt_s must be positive and finite")
        next_time = self.time_s + dt_s
        if not math.isfinite(next_time) or next_time <= self.time_s:
            raise SimulationError("time.overflow", "Simulation time must advance within float64 range")
        try:
            outputs, state, inventories, ledger = self._propose(dt_s)
            outputs, state = _freeze(outputs), _freeze(state)
            snapshot = self._snapshot(outputs, next_time, self.frame_index + 1)
            validator = deepcopy(self.frame_validator)
            validator.accept(snapshot.cell_frame)
        except SimulationError:
            raise
        except (ValueError, OverflowError, FloatingPointError) as error:
            raise SimulationError("profile.step_rejected", str(error)) from error
        self.outputs, self.state = outputs, state
        self.inventory, self.ledger = MappingProxyType(inventories), MappingProxyType(ledger)
        self.time_s, self.frame_index = next_time, self.frame_index + 1
        self.frame_validator, self.current = validator, snapshot
        return snapshot
