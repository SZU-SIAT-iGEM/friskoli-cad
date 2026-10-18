"""Wiki signal figures preserve the CAD equations and distinguish adaptation levels."""
from pathlib import Path
import runpy

import numpy as np
import pytest

SCRIPT = runpy.run_path(str(Path(__file__).parents[1] / 'research/wiki_signal.py'))


def test_steady_response_adapts_downstream_without_erasing_pts_dose_response():
    settings, parameters = SCRIPT['model']()
    rows = SCRIPT['static_response'](np.geomspace(.001, 100., 15), settings, parameters)
    adapted = [r for r in rows if r['adaptation']]
    frozen = [r for r in rows if not r['adaptation']]
    np.testing.assert_allclose([r['motor_bias'] for r in adapted], parameters.baseline_activity, atol=2e-14)
    np.testing.assert_allclose([r['d_bias_d_ln_C'] for r in adapted], 0., atol=2e-14)
    assert adapted[-1]['ei_dephosphorylated_fraction'] > adapted[0]['ei_dephosphorylated_fraction']
    assert frozen[-1]['motor_bias'] < frozen[0]['motor_bias']
    assert max(r['equilibrium_step_residual'] for r in rows) < 1e-12


def test_step_input_is_identical_and_pts_state_does_not_adapt_with_chea():
    settings, parameters = SCRIPT['model']()
    rows = SCRIPT['step_response'](settings, parameters, dt=.2)
    on = [r for r in rows if r['adaptation']]
    off = [r for r in rows if not r['adaptation']]
    for key in ('time_s', 'concentration_uM', 'uptake_molecules_s', 'ei_dephosphorylated_fraction'):
        np.testing.assert_array_equal([r[key] for r in on], [r[key] for r in off])
    np.testing.assert_array_equal([r['methylation'] for r in off], off[0]['methylation'])
    summary = SCRIPT['response_summary'](rows)
    adapted_up = next(r for r in summary if r['adaptation'] and r['phase'] == 'up')
    frozen_up = next(r for r in summary if not r['adaptation'] and r['phase'] == 'up')
    assert adapted_up['signed_peak_bias_change'] < 0
    assert adapted_up['adaptation_time_5pct_s'] is not None
    assert adapted_up['remaining_response_fraction'] < .05
    assert frozen_up['adaptation_time_5pct_s'] is None
    assert frozen_up['remaining_response_fraction'] > .95


def test_lhs_is_reproducible_and_occupies_each_log_stratum():
    intervals = {'tau_ei_s': (.5, 2.)}
    rows = SCRIPT['latin_hypercube'](16, 73, intervals)
    assert rows == SCRIPT['latin_hypercube'](16, 73, intervals)
    strata = sorted(int(np.log(r['tau_ei_s'] / .5) / np.log(4.) * 16) for r in rows)
    assert strata == list(range(16))
    with pytest.raises(ValueError):
        SCRIPT['latin_hypercube'](16, 73, {})


def test_small_step_retains_backgrounds_and_reports_a_transient_gain():
    settings, parameters = SCRIPT['model']()
    rows = SCRIPT['small_step'](np.array([.01, 1., 100.]), settings, parameters, dt=.2, duration=4.)
    assert len(rows) == 3
    assert rows[1]['peak_bias_decrease'] > rows[0]['peak_bias_decrease']
    for row in rows:
        assert row['transient_gain_per_ln_C'] == pytest.approx(row['peak_bias_decrease'] / np.log(1.1))
    with pytest.raises(ValueError):
        SCRIPT['step_response'](settings, parameters, dt=.3)
