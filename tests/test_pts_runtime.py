"""End-to-end shared finite bulk and accepted-signal profile contracts."""
from copy import deepcopy
from importlib.resources import files
import json

import numpy as np
import pytest

from friskoli_cad.engine import Simulation, SimulationError
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.science.pts import SignalParameters, advance_accepted_signal


def example():
    return json.loads(files("friskoli_cad").joinpath("examples", "pts_bulk.project.json").read_text(encoding="utf-8"))


def node(project, name):
    return next(n for n in project["graph"]["nodes"] if n["id"] == name)


def by_id(snapshot):
    return {cell["id"]: cell for cell in snapshot.cell_frame["cells"]}


def signal_parameters():
    evidence = json.loads(files("friskoli_cad.science").joinpath("data", "evidence.json").read_text())
    return SignalParameters(**evidence["parameter_sets"]["constructed-minimal-v1"]["signal"])


def test_profile_initial_state_and_shared_depletion_analytic():
    project = example()
    sim = simulation_from_project(project)
    initial = sim.current
    before = sim.inventory["bulk"].amounts[0]
    assert before == pytest.approx(10)
    assert sim.time_s == 0 and sim.frame_index == 0
    for cell in initial.cell_frame["cells"]:
        g = "a" if cell["id"].startswith("a") else "b"
        ch = cell["channels"]
        assert ch[g+"_settle.accepted_flux"] == 0
        assert ch[g+"_settle.accepted_amount"] == 0
        assert ch[g+"_settle.cumulative_uptake"] == 0
        assert ch[g+"_signal.ei_fraction"] == .6
        assert ch[g+"_signal.chey_p"] == .2
    after = sim.step(500.)
    accepted = []
    old_cells = by_id(initial)
    # Same area, A capacity density 2 copies/um2, B density 6: shares 1:1:3.
    expected_amounts = {"a0": 2., "a1": 2., "b0": 6.}
    for cell in after.cell_frame["cells"]:
        g = "a" if cell["id"].startswith("a") else "b"
        ch = cell["channels"]
        amount = ch[g+"_settle.accepted_amount"]
        accepted.append(amount)
        assert amount == pytest.approx(expected_amounts[cell["id"]], abs=1e-12)
        assert ch[g+"_settle.accepted_flux"] == pytest.approx(amount/500)
        assert ch[g+"_settle.cumulative_uptake"] == amount
        assert ch[g+"_request.requested_flux"] > ch[g+"_settle.accepted_flux"]
        expected = advance_accepted_signal(amount/500, .6, .2, 500., signal_parameters())
        assert ch[g+"_signal.ei_fraction"] == pytest.approx(float(expected.ei_fraction))
        assert ch[g+"_signal.chey_p"] == pytest.approx(float(expected.chey_p_uM))
        assert cell["position_um"] == old_cells[cell["id"]]["position_um"]
    assert sim.inventory["bulk"].amounts[0] + sum(accepted) == pytest.approx(before, abs=1e-12)
    assert sim.time_s == 500 and sim.frame_index == 1
    assert sim.inventory["bulk"].amounts[0] >= 0


def test_no_influx_after_empty_bulk_rephosphorylates_existing_ei():
    project = example()
    project["species"]["substrate"]["initial_concentration"]["value"] = 0
    sim = simulation_from_project(project)
    result = sim.step(.3)
    for cell in result.cell_frame["cells"]:
        g = "a" if cell["id"].startswith("a") else "b"
        ch = cell["channels"]
        assert ch[g+"_request.requested_flux"] == 0
        assert ch[g+"_settle.accepted_amount"] == 0
        assert ch[g+"_signal.ei_fraction"] == pytest.approx(.6*np.exp(-.3))


def test_group_node_and_cell_permutation_does_not_change_allocations():
    left = example(); right = deepcopy(left)
    right["groups"] = dict(reversed(list(right["groups"].items())))
    right["graph"]["nodes"].reverse(); right["graph"]["edges"].reverse()
    right["run"]["groups"].reverse()
    for key in ("ids", "positions_um", "orientation_xyzw", "initial_geometry"):
        right["groups"]["a"][key].reverse()
    first = simulation_from_project(left).step(500)
    second = simulation_from_project(right).step(500)
    assert by_id(first) == by_id(second)


def test_strict_shortage_rejects_without_any_state_commit():
    project = example()
    node(project, "bulk")["parameters"]["allocation_policy"]["value"] = "strict"
    sim = simulation_from_project(project)
    current = sim.current
    frame = deepcopy(current.cell_frame)
    inventory = dict(sim.inventory)
    ledger = dict(sim.ledger)
    fields = {name: value.copy() for name, value in current.concentration_fields.items()}
    with pytest.raises((SimulationError, ValueError)):
        sim.step(500)
    assert sim.current.cell_frame == frame
    assert dict(sim.inventory) == inventory
    assert sim.ledger == ledger
    assert sim.time_s == 0 and sim.frame_index == 0
    for name, value in fields.items():
        np.testing.assert_array_equal(sim.current.concentration_fields[name], value)
    # Failed proposal cannot contaminate a later accepted short step.
    retry = sim.step(.01)
    fresh = simulation_from_project(project).step(.01)
    assert retry.cell_frame == fresh.cell_frame


@pytest.mark.parametrize("which", ["bulk_current", "request_previous", "signal_previous", "wrong_group_signal", "duplicate_owner"])
def test_temporal_and_owner_counterexamples_rejected(which):
    project = example()
    edges = project["graph"]["edges"]
    if which == "bulk_current":
        next(e for e in edges if e["from"]["node"] == "bulk")["timing"] = "same_step"
    elif which == "request_previous":
        next(e for e in edges if e["from"]["node"] == "a_request")["timing"] = "previous_step"
    elif which == "signal_previous":
        next(e for e in edges if e["to"]["node"] == "a_signal")["timing"] = "previous_step"
    elif which == "wrong_group_signal":
        next(e for e in edges if e["to"]["node"] == "a_signal")["from"]["node"] = "b_settle"
    else:
        duplicate = deepcopy(node(project, "a_settle")); duplicate["id"] = "a_settle_duplicate"
        project["graph"]["nodes"].append(duplicate)
        for edge in list(edges):
            if edge["to"]["node"] == "a_settle":
                copy = deepcopy(edge); copy["id"] += "_duplicate"; copy["to"]["node"] = duplicate["id"]
                edges.append(copy)
    with pytest.raises((ProtocolError, SimulationError, ValueError)):
        simulation_from_project(project)


def test_unknown_profile_rejected_and_legacy_still_uses_original_simulation():
    project = example(); project["execution_profile"] = "unknown-profile"
    with pytest.raises((ProtocolError, SimulationError)):
        simulation_from_project(project)
    legacy = json.loads(files("friskoli_cad").joinpath("examples", "registry_readout.project.json").read_text())
    sim = simulation_from_project(legacy)
    assert type(sim) is Simulation
    before = sim.current.concentration_fields["substrate"].copy()
    after = sim.step(.1)
    np.testing.assert_array_equal(after.concentration_fields["substrate"], before)


def test_amount_and_concentration_units_cannot_be_substituted():
    project = example()
    node(project, "a_capacity")["parameters"]["reference_pts_copies"]["unit"] = "uM"
    with pytest.raises(ProtocolError):
        simulation_from_project(project)


def test_public_inventory_cannot_be_reassigned():
    sim = simulation_from_project(example())
    with pytest.raises(TypeError):
        sim.inventory["bulk"] = sim.inventory["bulk"]


def test_signal_failure_after_allocation_keeps_entire_transaction_uncommitted(monkeypatch):
    import friskoli_cad.engine.pts_runtime as runtime
    sim = simulation_from_project(example())
    frame = deepcopy(sim.current.cell_frame)
    stocks = dict(sim.inventory)
    state = {n: {k: v.copy() for k, v in data.items()} for n, data in sim.state.items()}
    def rejected(*args, **kwargs):
        raise ValueError("constructed late scientific rejection")
    monkeypatch.setattr(runtime.pts, "advance_accepted_signal", rejected)
    with pytest.raises(SimulationError, match="constructed late scientific rejection"):
        sim.step(500)
    assert sim.current.cell_frame == frame and dict(sim.inventory) == stocks
    assert sim.time_s == 0 and sim.frame_index == 0 and not sim.ledger
    for n, values in state.items():
        for k, value in values.items():
            np.testing.assert_array_equal(sim.state[n][k], value)


def test_large_intracellular_pool_cannot_hide_partial_transfer_rounding_loss():
    project = example()
    project["species"]["substrate"]["initial_concentration"]["value"] = 12.5/(602.214076*8)
    for group in ("a", "b"):
        node(project, group+"_settle")["parameters"]["initial_intracellular_molecules"]["value"] = 1e16
    sim = simulation_from_project(project)
    stocks, frame = dict(sim.inventory), deepcopy(sim.current.cell_frame)
    with pytest.raises(SimulationError):
        sim.step(500)
    assert dict(sim.inventory) == stocks and sim.current.cell_frame == frame
    assert sim.time_s == 0


def test_finite_bulk_plus_cumulative_intracellular_amount_conserved_across_steps():
    sim = simulation_from_project(example())
    initial = sim.inventory["bulk"].amounts[0]
    prior_amounts = {"a0": 0., "a1": 0., "b0": 0.}
    for _ in range(10):
        snapshot = sim.step(.1)
        total = sim.inventory["bulk"].amounts[0]
        for cell in snapshot.cell_frame["cells"]:
            g = "a" if cell["id"].startswith("a") else "b"
            ch = cell["channels"]
            current = ch[g+"_settle.cumulative_uptake"]
            assert current-prior_amounts[cell["id"]] == pytest.approx(ch[g+"_settle.accepted_amount"], abs=1e-15)
            prior_amounts[cell["id"]] = current
            total += current
        assert total == pytest.approx(initial, abs=1e-12)
