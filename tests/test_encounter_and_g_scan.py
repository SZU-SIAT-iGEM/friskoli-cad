"""Contracts of the encounter study and G scan research scripts."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'research'))
import encounter_study as es  # noqa: E402
import g_scan  # noqa: E402
from friskoli_cad.engine.presets import encounter_geometry, encounter_steady_field  # noqa: E402


@pytest.fixture(scope='module')
def field():
    return encounter_steady_field(1.)[0]


def test_steady_field_is_a_maintained_outward_gradient(field):
    _, center, _, upper = encounter_geometry()
    iz, iy = int(center[2] // 4.), int(center[1] // 4.)
    x = (np.arange(field.shape[2]) + .5) * 4.
    outside = x > upper[0]
    profile = field[iz, iy, outside]
    assert .9 < profile[0] <= 1. and np.all(np.diff(profile) < 0) and profile[-1] < .01
    assert np.all(field >= 0)


def test_matched_pair_differs_only_in_the_motor_edge(field):
    on, note_on = es.project_for(seed=3, feedback=True, field=field, cells=8)
    off, note_off = es.project_for(seed=3, feedback=False, field=field, cells=8)
    assert note_on == note_off
    assert on['groups'] == off['groups'] and on['domain'] == off['domain']
    strip = lambda p: {n['id']: n for n in p['graph']['nodes'] if n['id'] != 'constant_motor'}
    assert strip(on) == strip(off)
    edges = lambda p: {e['id'] for e in p['graph']['edges']}
    assert edges(on) ^ edges(off) == {'pts_signal_motor_bias_to_motility_motor_bias',
                                      'constant_motor_motor_bias_to_motility_motor_bias'}
    gaps = es.surface_gap(np.asarray(on['groups']['cells']['positions_um']))
    # A circle around a cube: 36 um from the faces, 36 + 8 - 8*sqrt(2) um from the vertical edges.
    assert gaps.max() == pytest.approx(36.) and gaps.min() == pytest.approx(36. + 8. - 8. * np.sqrt(2.))


def test_short_run_records_contacts_without_nan_and_holds_the_field(field):
    project, _ = es.project_for(seed=1, feedback=True, field=field, cells=8, start_gap_um=6.)
    result = es.run_case(project, duration=1., dt=.05)
    json.dumps(result, default=es._plain)
    s = result['summary']
    assert 0 <= s['ever_contacted_fraction'] <= 1 and s['entries_per_cell'] >= 0
    assert all(e['dwell_s'] >= 0 for e in result['episodes'])


def test_g_scan_capacity_thresholds_and_couplings():
    assert g_scan.copies('a', g_scan.REFERENCE_G)[2] == g_scan.copies('b', g_scan.REFERENCE_G)[2] == pytest.approx(100.)
    assert g_scan.copies('a', g_scan.PRESET_G)[2] == g_scan.copies('b', g_scan.PRESET_G)[2] == pytest.approx(200.)
    assert g_scan.copies('a', 50.)[2] > g_scan.copies('b', 50.)[2]
    gains, backgrounds = np.array([1., 2., 8.]), np.array([.15, 1.])
    rows = g_scan.scan(gains, backgrounds, duration=5.)
    gain = lambda m, c, g, b: next(r['transient_gain_per_ln_C'] for r in rows if r['mechanism'] == m
                                   and r['coupling'] == c and r['g_requested'] == g and r['background_uM'] == b)
    # Re-deriving the coupling from the copy number cancels expression from sensing.
    assert gain('a', 'rederived', 1., .15) == pytest.approx(gain('a', 'rederived', 8., .15), rel=1e-9)
    # A fixed per-molecule coupling lets more transporters raise the low-background gain.
    assert gain('a', 'fixed', 8., .15) > gain('a', 'fixed', 2., .15) > gain('a', 'fixed', 1., .15)


def test_cli_keeps_one_record_per_condition_and_seed(tmp_path):
    out = tmp_path / 'study'
    es.main(['--out', str(out), '--seeds', '1', '2', '--cells', '8', '--duration', '0.2', '--no-plots'])
    records = sorted(p.name for p in out.glob('center-pts-a-encounter*.json'))
    assert records == ['center-pts-a-encounter-control-s1.json', 'center-pts-a-encounter-control-s2.json',
                       'center-pts-a-encounter-s1.json', 'center-pts-a-encounter-s2.json']
    assert len((out / 'runs.csv').read_text().strip().splitlines()) == 5
