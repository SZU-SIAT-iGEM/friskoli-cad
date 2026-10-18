"""Spatial figure scripts preserve controls, initial inventory and numerical audits."""
from pathlib import Path
import runpy

import numpy as np
import pytest

from friskoli_cad.project import simulation_from_project

SCRIPT = runpy.run_path(str(Path(__file__).parents[1] / 'research/wiki_spatial.py'))


def test_volume_average_initial_field_preserves_mass_across_grids():
    amounts = []
    for dx in (4., 2., 1.):
        counts = [round(x / dx) for x in (64., 64., 32.)]
        values = SCRIPT['gaussian_average'](counts, [dx] * 3)
        amounts.append(values.sum() * dx**3)
    np.testing.assert_allclose(amounts, amounts[0], rtol=1e-14)


def test_feedback_control_changes_only_the_declared_edge_and_constant_node():
    on = SCRIPT['project_for'](seed=12)
    off = SCRIPT['project_for'](seed=12, feedback=False)
    assert SCRIPT['control_signature'](on) == SCRIPT['control_signature'](off)
    off['domain']['spacing_um_xyz'][0] = 2.
    assert SCRIPT['control_signature'](on) != SCRIPT['control_signature'](off)


def test_population_scan_is_nested_and_initial_geometry_runs():
    assert SCRIPT['positions'](16)[:8] == SCRIPT['positions'](8)
    for n in (8, 16, 32):
        simulation = simulation_from_project(SCRIPT['project_for'](cells=n))
        simulation.step(.02)
        assert all(row['passed'] for row in SCRIPT['audit'](simulation))


def test_perturbed_signal_starts_at_its_declared_adapted_state():
    simulation = simulation_from_project(SCRIPT['project_for'](factors={'ei_coupling': 2., 'uptake': .5}))
    np.testing.assert_allclose(simulation.outputs['pts_signal']['activity'], 1/3, atol=1e-14)
    np.testing.assert_allclose(simulation.outputs['pts_signal']['motor_bias'], 1/3, atol=1e-14)


def test_actual_case_writes_per_step_audits_and_counts_one_signal_evaluation(tmp_path):
    study = SCRIPT['Study'](tmp_path, duration=.04, dt=.02)
    result = study.run(seed=7)
    assert result['audit_passed']
    assert result['signal_advance_calls'] == result['expected_signal_calls'] == 2
    assert len(result['audits']) == 2
    assert study.run(seed=7) is result
    assert len(list((tmp_path / 'cases').glob('*.json'))) == 1
    simulation = simulation_from_project(result['project'])
    simulation.step(.02)
    simulation.ledger['sugar']['consumed'] += 1.
    assert not SCRIPT['audit'](simulation)[0]['passed']


def test_zero_release_zero_initial_field_cannot_form_an_artificial_gradient(tmp_path):
    study = SCRIPT['Study'](tmp_path, duration=.04)
    result = study.run(seed=7, profile='contact', zero_initial=True, factors={'release': 0.})
    assert result['metrics']['qualified_probe_fraction'] == 0
    assert result['metrics']['relative_gradient_per_um'] == 0
    assert result['metrics']['converted_molecules'] == 0


def test_resolution_decision_preserves_unresolved_statistics():
    compare = SCRIPT['comparison']
    assert compare([1.] * 4, [1.] * 4, metric='arrival_fraction')['status'] == 'within_threshold'
    assert compare([0.] * 4, [1.] * 4, metric='arrival_fraction')['status'] == 'exceeds_threshold'
    assert compare([0.] * 4, [-1., 1., -1., 1.], metric='arrival_fraction')['status'] == 'unresolved_at_this_seed_count'


def test_diffusion_reference_checks_analytic_solution_on_both_grids(tmp_path):
    rows = SCRIPT['diffusion_reference'](tmp_path, duration=.2, dt=.02)
    assert len(rows) == 4
    assert rows[-1]['relative_L2_error'] < rows[0]['relative_L2_error']
    assert all(abs(row['mass_change_molecules']) < 1e-6 for row in rows)


def test_morris_coordinates_recover_known_log_linear_elementary_effects(tmp_path):
    class AnalyticStudy:
        destination = tmp_path
        def run(self, *, seed, factors=None):
            factors = factors or {}
            response = 100. + sum((i + 1) * np.log(factors.get(name, 1.)) for i, name in enumerate(SCRIPT['FACTORS']))
            return {'case_sha256': 'analytic-fixture', 'metrics': {key: float(response) for key in SCRIPT['ENDPOINTS']}}
    SCRIPT['sensitivity'](AnalyticStudy(), [1, 2], trajectories=4)
    import csv
    with (tmp_path / 'M14-Morris-summary.csv').open(encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    for index, row in enumerate(rows):
        expected = (index + 1) * np.log(4.)
        assert float(row['mu']) == pytest.approx(expected)
        assert float(row['mu_star']) == pytest.approx(expected)
        assert abs(float(row['sigma'])) < 1e-12
    with (tmp_path / 'M14-Morris-paths.csv').open(encoding='utf-8') as stream:
        paths = list(csv.DictReader(stream))
    for row in paths:
        assert all(-1e-12 <= float(row['u_' + key]) <= 1 + 1e-12 for key in SCRIPT['FACTORS'])
