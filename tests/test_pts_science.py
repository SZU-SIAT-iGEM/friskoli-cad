"""Source comparison, analytic limits and numerical contracts for the PTS surrogate."""
from dataclasses import asdict, replace
from importlib.resources import files
import json

import numpy as np
import pytest

from friskoli_cad.science.pts import (
    RebuiltCapacityParameters, SimplifiedCapacityParameters, SignalParameters,
    advance_accepted_signal, capsule_area_um2, pts_request, rebuilt_capacity,
    simplified_capacity, signal_readout, steady_state_signal,
)


def data(name):
    return json.loads(files("friskoli_cad.science").joinpath("data", name).read_text(encoding="utf-8"))


@pytest.fixture
def signal():
    # Explicit source-derived comparison parameters, not an application default.
    return SignalParameters(6, .001, 20, .3, 5, 8, 2, 10, 10.3, 3.1)


def test_locked_source_function_outputs(signal):
    f = data("fixtures.json")
    v = f["inputs"]
    for model, capacity in (
        ("rebuilt-v2", rebuilt_capacity(v["area_um2"], v["g_requested"],
                                       RebuiltCapacityParameters(500, .5, .55, 3e-5))),
        ("simplified-v4", simplified_capacity(v["area_um2"], v["g_requested"],
                                             SimplifiedCapacityParameters(500, 10, f["simplified-v4"]["reference_area_um2"]))),
    ):
        for name, expected in zip(f["capacity_output_order"], f[model]["capacity"]):
            np.testing.assert_allclose(getattr(capacity, name), expected, rtol=2e-13, atol=1e-14)
        np.testing.assert_allclose(pts_request(v["concentration_uM"], capacity.functional_copies, 20, 30),
                                   f[model]["request"], rtol=2e-13, atol=1e-14)
        result = advance_accepted_signal(v["accepted_flux_molecules_s"], v["ei_fraction"],
                                         v["chey_p_uM"], v["dt_s"], signal)
        for name, expected in zip(f["signal_output_order"], f[model]["signal"]):
            np.testing.assert_allclose(getattr(result, name), expected, rtol=2e-13, atol=1e-14)
    np.testing.assert_allclose(capsule_area_um2([.8, 2.], .8), f["rebuilt-v2"]["geometry"])


def test_geometry_and_distinct_capacity_laws():
    assert capsule_area_um2(2., 2.) == pytest.approx(4 * np.pi)
    a = rebuilt_capacity([0, 1, 2], 100, RebuiltCapacityParameters(2, .5, .4, .1))
    b = simplified_capacity([0, 1, 2], 100, SimplifiedCapacityParameters(2, 3, 1))
    np.testing.assert_allclose(a.capacity_copies, [0, 2, 4])
    np.testing.assert_allclose(b.capacity_copies, [0, 6, 12])
    np.testing.assert_array_equal(a.functional_copies + a.excess_copies, a.target_copies)
    assert rebuilt_capacity(10, 0, RebuiltCapacityParameters(2, .5, .4, .1)).functional_copies == 0
    with pytest.raises(ValueError):
        capsule_area_um2(.5, .8)


def test_request_zero_half_saturation_and_strong_input():
    np.testing.assert_allclose(pts_request([0., 30., 1e308], 5, 2, 30), [0, 5, 10])
    assert pts_request(1e308, 2, 4, 1e308) == 4
    assert pts_request(10, 0, 20, 30) == 0
    assert pts_request(10, 50, 0, 30) == 0


def test_zero_accepted_influx_ei_rephosphorylation(signal):
    dt = .13
    result = advance_accepted_signal(0, .8, 2., dt, signal)
    assert result.ei_fraction == pytest.approx(.8 * np.exp(-20 * dt))
    # Equal request cannot justify equal signaling after unequal allocations.
    limited = advance_accepted_signal([0, 10000], [.8, .8], [2, 2], dt, signal)
    assert limited.ei_fraction[0] < limited.ei_fraction[1]
    assert limited.chea_active_uM[0] > limited.chea_active_uM[1]


def test_steady_state_fixed_point_and_zero_signal(signal):
    ss = steady_state_signal([0, 100, 1e6], signal)
    later = advance_accepted_signal([0, 100, 1e6], ss.ei_fraction, ss.chey_p_uM, 100, signal)
    for name in asdict(ss):
        np.testing.assert_allclose(getattr(later, name), getattr(ss, name), rtol=1e-14)
    assert ss.ei_fraction[0] == 0
    assert ss.chea_active_uM[0] == signal.chea_total_uM
    assert ss.chey_p_uM[0] == 4
    assert signal_readout(0, 0, signal).motor_bias == 0
    assert signal_readout(0, 3.1, signal).motor_bias == .5


def test_disabled_rates_preserve_state_and_nonunique_steady_rejected(signal):
    disabled = replace(signal, ei_dephos_per_molecule=0, ei_rephos_s=0,
                       chey_phos_per_uM_s=0, chey_dephos_s=0)
    result = advance_accepted_signal(100, .7, 2, 1e300, disabled)
    assert result.ei_fraction == .7 and result.chey_p_uM == 2
    with pytest.raises(ValueError, match="not unique"):
        steady_state_signal(0, disabled)


def test_strong_finite_rates_and_initial_boundary(signal):
    strong = replace(signal, ei_dephos_per_molecule=1, ei_rephos_s=1e308,
                     motor_hill=1e308)
    result = advance_accepted_signal([0, 1e308], [0, 1], [0, 8], 1e308, strong)
    for value in asdict(result).values():
        assert np.all(np.isfinite(value))
    np.testing.assert_allclose(result.ei_fraction, [0, .5])
    assert np.all((result.chey_p_uM >= 0) & (result.chey_p_uM <= 8))
    with pytest.raises(ValueError, match="finite float64"):
        advance_accepted_signal(1e308, 0, 0, .1, replace(signal, ei_dephos_per_molecule=1e308))


def test_total_chey_boundary_stays_exact_under_phosphorylation(signal):
    total = 2033.4255817221715
    p = replace(signal, chey_total_uM=total, chey_dephos_s=0)
    for dt in (1e-8, .01, 1., 1e8):
        result = advance_accepted_signal(np.geomspace(1e-6, 1e6, 100), 0, total, dt, p)
        np.testing.assert_array_equal(result.chey_p_uM, np.full(100, total))


def test_coupled_endpoint_integrator_converges_to_independent_rk4(signal):
    # Full coupled ODE reference; endpoint-frozen CheA must not be called exact.
    flux, duration = 10000., .2
    def rhs(state):
        e, y = state
        a = 5 * .3 / (.3 + 6 * e)
        return np.array([.001 * flux * (1-e) - 20*e, 2*a*(8-y)-10*y])
    ref = np.array([0., 4.])
    h = duration / 4000
    for _ in range(4000):
        k1 = rhs(ref); k2 = rhs(ref + h*k1/2); k3 = rhs(ref+h*k2/2); k4 = rhs(ref+h*k3)
        ref += h*(k1+2*k2+2*k3+k4)/6
    errors = []
    for count in (80, 160, 320):
        e, y = 0., 4.
        for _ in range(count):
            result = advance_accepted_signal(flux, e, y, duration/count, signal)
            e, y = result.ei_fraction, result.chey_p_uM
        assert e == pytest.approx(ref[0], abs=1e-13)
        errors.append(abs(y-ref[1]))
    assert 1.8 < errors[0]/errors[1] < 2.2
    assert 1.8 < errors[1]/errors[2] < 2.2


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "3", 1+2j])
def test_invalid_numeric_inputs_rejected(bad, signal):
    with pytest.raises(ValueError):
        pts_request(bad, 10, 20, 30)
    with pytest.raises(ValueError):
        advance_accepted_signal(0, 0, 0, bad, signal)
    with pytest.raises(ValueError):
        replace(signal, ei_rephos_s=bad)


@pytest.mark.parametrize("call", [
    lambda p: pts_request(1, 1, 1, 0),
    lambda p: pts_request([1, 2], [1], 1, 1),
    lambda p: pts_request([[1, 2]], [1, 2], 1, 1),
    lambda p: advance_accepted_signal(0, 1.1, 0, .1, p),
    lambda p: advance_accepted_signal(0, 0, 8.1, .1, p),
    lambda p: advance_accepted_signal(0, 0, 0, 0, p),
    lambda p: RebuiltCapacityParameters(1, 1.1, .5, 1),
    lambda p: SimplifiedCapacityParameters(1, 1, 0),
])
def test_parameter_domains_and_alignment(call, signal):
    with pytest.raises(ValueError):
        call(signal)


def test_pure_functions_do_not_mutate_inputs(signal):
    e, y, j = np.array([.2, .8]), np.array([1., 2.]), np.array([0., 1.])
    originals = [v.copy() for v in (e, y, j)]
    result = advance_accepted_signal(j, e, y, .1, signal)
    result.ei_fraction[0] = 1
    for value, original in zip((e, y, j), originals):
        np.testing.assert_array_equal(value, original)
    empty = advance_accepted_signal([], [], [], .1, signal)
    assert empty.ei_fraction.shape == (0,)


def test_evidence_has_version_locks_and_no_calibrated_parameter_claims():
    lock, evidence = data("source_lock.json"), data("evidence.json")
    assert len(lock["models"]) == 2
    assert sum(len(m["files"]) for m in lock["models"].values()) == 9
    for model in lock["models"].values():
        assert len(model["revision_commit"]) == 40
        for file in model["files"].values():
            assert len(file["sha256"]) == 64 and file["matches_committed_bytes"]
    assert evidence["maturity"] == "exploratory"
    assert all(p["provenance"] in {"source-derived", "constructed", "unknown"}
               for p in evidence["parameters"])
    assert all(p["calibration"] == "unknown" for p in evidence["parameters"])
