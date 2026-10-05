"""Source comparison, analytic limits and numerical contracts for the PTS surrogate."""
from importlib.resources import files
import json

import numpy as np
import pytest

from friskoli_cad.science.pts import (
    RebuiltCapacityParameters, SimplifiedCapacityParameters,
    capsule_area_um2, pts_request, rebuilt_capacity,
    simplified_capacity,
)


def data(name):
    return json.loads(files("friskoli_cad.science").joinpath("data", name).read_text(encoding="utf-8"))


def test_locked_source_function_outputs():
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
