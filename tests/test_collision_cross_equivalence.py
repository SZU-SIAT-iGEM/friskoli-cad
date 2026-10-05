"""Fixed 3D kernel must preserve original collision distance and boundaries."""
import numpy as np

from friskoli_cad.engine import collision


def test_fixed_cross_preserves_distance_for_random_and_degenerate_segments(monkeypatch):
    rng = np.random.default_rng(20261002)
    cases = list(rng.normal(size=(300, 4, 3)))
    cases += [np.array(points, dtype=float) for points in (
        [[0, 0, 0], [0, 0, 0], [1, 0, 0], [1, 0, 0]],
        [[0, 0, 0], [1, 0, 0], [1, 0, 0], [2, 0, 0]],
        [[0, 0, 0], [1, 0, 0], [.5, 0, 0], [2, 0, 0]],
        [[0, 0, 0], [1, 0, 0], [0, 1e-12, 0], [1, 2e-12, 0]],
        [[0, 0, 0], [1, 0, 0], [.5, -1, 0], [.5, 1, 0]],
    )]
    cases += [case * scale for case in cases[-5:] for scale in (1e-8, 1e8)]
    original = collision._cross3
    expected = []
    with monkeypatch.context() as patch:
        patch.setattr(collision, '_cross3', np.cross)
        for points in cases:
            expected.append(collision._segment_distance(*points))
    assert collision._cross3 is original
    assert [collision._segment_distance(*points) for points in cases] == expected


def test_complete_trajectory_matches_original_cross_product(monkeypatch):
    from friskoli_cad.engine.presets import make_example
    from friskoli_cad.project import simulation_from_project

    project = make_example('center-pts-a-small-strong')
    current, previous = simulation_from_project(project), simulation_from_project(project)
    for _ in range(12):
        current.step(.1)
        with monkeypatch.context() as patch:
            patch.setattr(collision, '_cross3', np.cross)
            previous.step(.1)
        # Compare actual numerical state; provenance fingerprints correctly
        # refer to the same installed implementation in this controlled assay.
        assert current.checkpoint() == previous.checkpoint()
