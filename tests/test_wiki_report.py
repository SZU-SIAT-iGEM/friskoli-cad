"""Figure indexes reject mismatched implementations and failed numerical audits."""
import csv
import json
from pathlib import Path
import runpy

import pytest

SCRIPT = runpy.run_path(str(Path(__file__).parents[1] / 'research/wiki_report.py'))


def write_table(path, records):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


@pytest.fixture
def inputs(tmp_path):
    signal, spatial = tmp_path / 'signal', tmp_path / 'spatial'
    signal.mkdir()
    spatial.mkdir()
    (signal / 'manifest.json').write_text(json.dumps({'source_hashes': {'model': 'same'}, 'dt_s': .05,
        'A': {'equilibrium_residual_max': 1e-15}, 'C': {'n_parameter_samples': 4, 'parameter_seed': 42}}))
    (spatial / 'manifest.json').write_text(json.dumps({'source_hashes': {'model': 'same'},
        'motility_seeds': [1, 2], 'duration_s': 2., 'Morris': {'trajectories': 4}}))
    state = {'ei_dephosphorylated_fraction': .2, 'methylation': .8, 'chea_activity': 1/3, 'chey_p_uM': 5., 'motor_bias': 1/3}
    for name in ('A-dose-response.csv', 'B-step-response.csv'):
        write_table(signal / name, [state])
    write_table(signal / 'B-response-summary.csv', [{'adaptation': False, 'phase': 'up', 'signed_peak_bias_change': -.1,
        'half_peak_response_time_s': .2, 'adaptation_time_5pct_s': '', 'remaining_response_fraction': 1.}])
    write_table(signal / 'C-uncertainty.csv', [{'background_uM': .1, 'transient_gain_per_ln_C_q50': .2}])
    write_table(spatial / 'conservation-and-call-audit.csv', [{'audit_passed': True, 'signal_advance_calls': 100,
        'expected_signal_calls': 100, 'max_field_balance_error': 1e-8, 'max_uptake_consumption_error': 0.}])
    return signal, spatial


def test_report_keeps_absent_adaptation_and_input_hashes(inputs, tmp_path):
    signal, spatial = inputs
    SCRIPT['build_report'](signal, spatial, tmp_path / 'report')
    body = (tmp_path / 'report/README.md').read_text(encoding='utf-8')
    assert '本时程未回落' in body
    assert 'M07' in body
    assert len(json.loads((tmp_path / 'report/input-sha256.json').read_text())) >= 6


def test_report_rejects_failed_audits_and_different_model_sources(inputs, tmp_path):
    signal, spatial = inputs
    manifest = json.loads((spatial / 'manifest.json').read_text())
    manifest['source_hashes'] = {'model': 'different'}
    (spatial / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='matching CAD source'):
        SCRIPT['build_report'](signal, spatial, tmp_path / 'report')
    manifest['source_hashes'] = {'model': 'same'}
    (spatial / 'manifest.json').write_text(json.dumps(manifest))
    write_table(spatial / 'conservation-and-call-audit.csv', [{'audit_passed': False, 'signal_advance_calls': 200,
        'expected_signal_calls': 100, 'max_field_balance_error': 1., 'max_uptake_consumption_error': 1.}])
    with pytest.raises(ValueError, match='all spatial audits'):
        SCRIPT['build_report'](signal, spatial, tmp_path / 'report')


def test_contact_rates_remain_undefined_at_time_zero_and_keep_enrichment_separate(inputs, tmp_path):
    signal, spatial = inputs
    records = [{'mechanism': 'a', 'feedback': feedback, 'seed': seed, 'time_s': time,
                'enrichment_vs_uniform_fluid': .5 * time, 'contact_entries': time}
               for feedback in (True, False) for seed in (1, 2) for time in (0., 1.)]
    write_table(spatial / 'M16-layered-timeseries.csv', records)
    figures = SCRIPT['build_report'](signal, spatial, tmp_path / 'report')
    assert any(r['id'] == 'M16-contact' for r in figures)
    with (tmp_path / 'report/M16-contact-enrichment.csv').open(encoding='utf-8') as stream:
        rates = list(csv.DictReader(stream))
    assert rates[0]['contact_entry_rate_per_founder_s'] == ''
    assert float(rates[1]['contact_entry_rate_per_founder_s']) == pytest.approx(.125)
    assert float(rates[1]['enrichment_vs_uniform_fluid']) == pytest.approx(.5)


def test_endpoint_summary_pairs_by_seed_and_retains_unchanged_repeats():
    records = [{'mechanism': 'a', 'feedback': feedback, 'seed': seed, 'time_s': 2.,
                'contact_s_per_cell': value, 'contact_entries': 1}
               for feedback, seed, value in [('True', 2, 5.), ('False', 1, 2.),
                                             ('True', 1, 2.), ('False', 2, 3.)]]
    summary = SCRIPT['paired_endpoint_summary'](records, 2., {'samples': 2000, 'seed': 9201, 'interval': [.025, .975]})
    metrics = summary['endpoint_by_mechanism']['a']['metrics']
    assert metrics['contact_s_per_cell']['paired_values'] == [0., 2.]
    assert metrics['contact_s_per_cell']['paired_difference'] == [1., 0., 2.]
    assert metrics['contact_s_per_cell']['n_positive'] == 1
    assert metrics['contact_entries']['paired_difference'] == [0., 0., 0.]


def test_endpoint_summary_rejects_missing_or_duplicate_seed_pairs():
    record = {'mechanism': 'a', 'feedback': 'True', 'seed': 1, 'time_s': 2., 'contact_entries': 1}
    settings = {'samples': 2000, 'seed': 9201, 'interval': [.025, .975]}
    with pytest.raises(ValueError, match='matching feedback and control seeds'):
        SCRIPT['paired_endpoint_summary']([record], 2., settings)
    with pytest.raises(ValueError, match='duplicate seed'):
        SCRIPT['paired_endpoint_summary']([record, record], 2., settings)
