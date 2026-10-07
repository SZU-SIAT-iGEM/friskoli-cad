from dataclasses import fields, replace
from copy import deepcopy
import numpy as np
import pytest

from friskoli_cad.science.pts_methylation import (
    PTSMethylationParameters, activity_coupling, adapted_methylation, advance,
    complex_activity, methylation_rate, self_consistent_motor_half)
from friskoli_cad.science.chemotaxis import motor_bias
from friskoli_cad.engine.presets import build_center_project, make_example
from friskoli_cad.project import simulation_from_project


def signal_node(project=None):
    project = project or build_center_project('a', 'small')
    return next(n for n in project['graph']['nodes'] if n['id'] == 'pts_signal')


def parameters():
    values = {k: v['value'] for k, v in signal_node()['parameters'].items()}
    return PTSMethylationParameters(**{f.name: values[f.name.lower()] for f in fields(PTSMethylationParameters)})


def initial_state():
    v = {k: x['value'] for k, x in signal_node()['parameters'].items()}
    return (v['initial_ei_fraction'], v['initial_methylation'], v['initial_chey_p_um'])


def trajectory(p, dt=.1, duration=40., flux=100.):
    state = initial_state()
    rows = []
    for _ in range(round(duration/dt)):
        e, m, a, y, b = advance(flux, *state, dt, p)
        state = e, m, y
        rows.append([float(x) for x in (e, m, a, y, b)])
    return np.asarray(rows)


def test_criterion_zero_holds_and_couplings_are_derived():
    """The motor reports the declared baseline, and neither coupling is a free gain."""
    p = parameters()
    y0 = initial_state()[2]
    assert motor_bias(np.array([y0]), half_uM=p.motor_half_uM, hill=p.motor_hill)[0] == pytest.approx(p.baseline_activity)
    assert p.motor_half_uM == pytest.approx(
        self_consistent_motor_half(y0, p.baseline_activity, p.motor_hill))
    assert activity_coupling(p) == (1., 1./(p.methylation_max - p.methylation_min))
    # The declared full PTS swing maps exactly onto the declared methylation range,
    # which is what lets adaptation undo any PTS drive without a free gain.
    assert adapted_methylation(0., p) == p.methylation_min
    assert adapted_methylation(1., p) == pytest.approx(p.methylation_max)
    # Starting state is the adapted fixed point, not a chosen number.
    e0, m0, _ = initial_state()
    assert complex_activity(e0, m0, p) == pytest.approx(p.baseline_activity)


def test_step_response_adapts_upstream_and_frozen_methylation_does_not():
    p = parameters()
    active = trajectory(p)
    frozen = trajectory(replace(p, adaptation_rate_s=0.))
    # A step up in uptake inhibits CheA, then methylation restores the baseline.
    assert active[:, 2].min() < .2
    assert abs(active[-1, 2] - p.baseline_activity) < 1e-4
    chey_ss = (p.chey_total_uM * p.baseline_activity * p.chea_total_uM * p.chey_phos_per_uM_s
               / (p.chey_phos_per_uM_s * p.chea_total_uM * p.baseline_activity + p.chey_dephos_s))
    assert abs(active[-1, 3] - chey_ss) < 1e-3
    assert active[-1, 1] > initial_state()[1]
    assert frozen[-1, 2] < .1
    np.testing.assert_array_equal(frozen[:, 1], initial_state()[1])
    np.testing.assert_array_equal(active[:, 0], frozen[:, 0])


def test_withdrawal_and_methylation_range_compensates_the_whole_pts_swing():
    p = parameters()
    e, m, _, y, _ = trajectory(p)[-1]
    off = advance(0., e, m, y, .2, p)
    assert off[2] > p.baseline_activity
    # The methylation range is exactly enough for the full PTS range: a saturating
    # step adapts back to baseline rather than pinning against methylation_max.
    saturated = trajectory(p, flux=1e9, duration=100.)
    assert saturated[-1, 1] == pytest.approx(p.methylation_max, rel=1e-6)
    assert saturated[-1, 2] == pytest.approx(p.baseline_activity, abs=1e-6)
    assert np.isfinite(saturated).all()
    assert methylation_rate(4., 0., 4.) == pytest.approx(1.)


def test_time_step_refinement_and_invalid_states():
    p = parameters()
    reference = trajectory(p, dt=.00625, duration=2.)[-1]
    errors = [np.linalg.norm(trajectory(p, dt=dt, duration=2.)[-1]-reference) for dt in (.2,.1,.05)]
    assert errors[2] < errors[1] < errors[0]
    for bad in (-1., 4.1, np.nan):
        with pytest.raises(ValueError): advance(0., 0., bad, 10./3., .1, p)


@pytest.mark.parametrize('mechanism', ['a','b'])
def test_no_concentration_bypass_when_pts_is_disabled(mechanism):
    project = build_center_project(mechanism, 'small')
    for node in project['graph']['nodes']:
        if node['id'] == 'uptake_request': node['parameters']['turnover_s']['value'] = 0.
    other = deepcopy(project)
    for node in other['graph']['nodes']:
        if node['id'] == 'initial_product':
            node['parameters']['values_um']['value'] = (np.asarray(node['parameters']['values_um']['value'])*100).tolist()
    # The 100x field would (correctly) pre-adapt a different initial state; this test is
    # about a runtime bypass, so both sides start from the same fixed point.
    ref = signal_node(project)['parameters']
    for node in other['graph']['nodes']:
        if node['id'] == 'pts_signal':
            for k in ('initial_ei_fraction', 'initial_methylation', 'initial_chey_p_um', 'motor_half_um'):
                node['parameters'][k]['value'] = ref[k]['value']
    first, second = simulation_from_project(project), simulation_from_project(other)
    for _ in range(4):
        first.step(.1); second.step(.1)
        for port in ('ei_fraction','methylation','activity','chey_p','motor_bias'):
            np.testing.assert_array_equal(first.outputs['pts_signal'][port],second.outputs['pts_signal'][port])
    a0 = ref['baseline_activity']['value']
    np.testing.assert_allclose(first.outputs['pts_signal']['activity'], a0, atol=2e-2)
    np.testing.assert_array_equal(first.world.groups['cells'].positions_um, second.world.groups['cells'].positions_um)


def test_presets_share_signal_and_only_accept_settled_flux():
    a, b = [build_center_project(m, 'small', release='strong', spacing_um=1.) for m in ('a','b')]
    assert signal_node(a) == signal_node(b)
    for m in ('a','b'):
        for feedback in (True,False):
            p = build_center_project(m, 'small', feedback=feedback, release='strong', spacing_um=1.)
            assert p == make_example(p['id'])
            assert all(n['id']!='memory_motor' for n in p['graph']['nodes'])
            incoming = [e for e in p['graph']['edges'] if e['to']['node']=='pts_signal']
            assert len(incoming)==1
            assert incoming[0]['from']=={'node':'accepted_uptake','port':'accepted_flux'}


def test_invalid_initial_methylation_rejected_before_run():
    p = build_center_project('a','small')
    signal_node(p)['parameters']['initial_methylation']['value']=5.
    with pytest.raises(Exception, match='methylation'): simulation_from_project(p)


def test_coupled_dynamics_converge_to_independent_rk4():
    p = parameters()
    a0 = p.baseline_activity
    _, gain_m = activity_coupling(p)
    def rhs(state, flux=100.):
        e, m, y = state
        a = np.clip(a0 - e + gain_m*(m - p.methylation_min), 0., 1.)
        on = flux*p.ei_dephos_per_molecule
        return np.array([on*(1-e) - p.ei_rephos_s*e, p.adaptation_rate_s*(a0-a),
                         a*p.chea_total_uM*p.chey_phos_per_uM_s*(p.chey_total_uM-y) - p.chey_dephos_s*y])
    reference = np.array(initial_state())
    h = 2./2000
    for _ in range(2000):
        k1 = rhs(reference); k2 = rhs(reference+h*k1/2)
        k3 = rhs(reference+h*k2/2); k4 = rhs(reference+h*k3)
        reference += h*(k1+2*k2+2*k3+k4)/6
    errors = []
    for dt in (.1,.05,.025):
        result = trajectory(p,dt=dt,duration=2.)[-1][[0,1,3]]
        errors.append(np.linalg.norm(result-reference))
    assert 1.7 < errors[0]/errors[1] < 2.3
    assert 1.7 < errors[1]/errors[2] < 2.3


def test_zero_rates_bounds_purity_and_empty_population():
    p = parameters()
    e, m, y, j = [np.array(x) for x in ([.2,.8],[1.,3.],[0.,10.],[0.,100.])]
    originals = [v.copy() for v in (e,m,y,j)]
    disabled = replace(p,ei_dephos_per_molecule=0.,ei_rephos_s=0.,adaptation_rate_s=0.,
                       chey_phos_per_uM_s=0.,chey_dephos_s=0.)
    result = advance(j,e,m,y,1e300,disabled)
    for actual, expected in zip((result[0],result[1],result[3]),(e,m,y)):
        np.testing.assert_array_equal(actual,expected)
    result[0][0]=1.
    result[1][0]=0.
    for actual, expected in zip((e,m,y,j),originals): np.testing.assert_array_equal(actual,expected)
    assert all(v.shape==(0,) for v in advance([],[],[],[],.1,p))
    zero = advance(0.,.8,2.,0.,.13,replace(p,ei_rephos_s=20.))
    assert zero[0] == pytest.approx(.8*np.exp(-20*.13))
    for dt in (1e-8,.1,100.):
        assert advance(100.,0.,2.,10.,dt,replace(p,chey_dephos_s=0.))[3] == 10.


@pytest.mark.parametrize('bad',[-1.,np.nan,np.inf,True,'3',1+2j])
def test_invalid_numeric_inputs(bad):
    p=parameters()
    with pytest.raises(ValueError): advance(bad,0.,2.,10./3.,.1,p)
    with pytest.raises(ValueError): advance(0.,0.,2.,10./3.,bad,p)
    with pytest.raises(ValueError): replace(p,ei_rephos_s=bad)


def test_catalog_removes_old_modules_and_checkpoint_keeps_methylation():
    from friskoli_cad.engine.science_extensions import modular_registry
    from friskoli_cad.engine.modular_checkpoint import restore_checkpoint
    r=modular_registry()
    for mid in ('signal.pts_accepted','signal.concentration_memory','signal.chey_memory'):
        with pytest.raises(Exception,match='no runtime'): r.get(mid,'1.0.0')
    p=build_center_project('b','small')
    sim=simulation_from_project(p); sim.step(.1)
    restored=restore_checkpoint(p,sim.checkpoint())
    sim.step(.1); restored.step(.1)
    assert sim.checkpoint()==restored.checkpoint()


def test_methylation_rolls_back_on_late_transaction_failure(monkeypatch):
    from friskoli_cad.protocol import FrameSequenceValidator
    p=build_center_project('a','small')
    sim=simulation_from_project(p); sim.step(.1)
    before=sim.checkpoint()
    def reject(*args, **kwargs): raise RuntimeError('late failure')
    monkeypatch.setattr(FrameSequenceValidator,'accept',reject)
    with pytest.raises(RuntimeError,match='late failure'): sim.step(.1)
    assert sim.checkpoint()==before


def test_increasing_flux_reduces_motor_bias_relative_to_decreasing_flux():
    p=parameters()
    e,m,_,y,_=trajectory(p)[-1]
    upward=advance(120.,e,m,y,.2,p)
    downward=advance(80.,e,m,y,.2,p)
    assert upward[4] < downward[4]
