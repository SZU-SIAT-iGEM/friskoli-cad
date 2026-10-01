"""Constructed invariants and outputs independently frozen from reviewed A/B."""
import json
from pathlib import Path

import numpy as np
import pytest

from friskoli_cad.science import chemotaxis as c, materials as m, physiology as p
from friskoli_cad.science.pts import SignalParameters

FIXTURE = json.loads((Path(__file__).parents[1] / 'src/friskoli_cad/science/data/n3_fixtures.json').read_text())
YIELD = 1.1672551805315156e-9


def mwc():
    # Constructed numerical example, not a fitted receptor.
    return c.MWCParameters(4, 1, 100, 2, 1, .3, .4)


def ah():
    return p.RebuiltHealthParameters(.01,.001,.25,.05,3,.2,.03,50000,.002,.0005,4,.05,1,4,.15)


def test_a_source_memory_bias():
    memory = c.advance_concentration_memory([1,5], [3,2], .25, memory_tau_s=3)
    np.testing.assert_allclose(memory, FIXTURE['rebuilt']['memory'], rtol=2e-14)
    bias = c.rebuilt_motor_bias([.2,.7],memory,[3,2],gradient_strength_per_uM=2)
    np.testing.assert_allclose(bias,FIXTURE['rebuilt']['bias'],rtol=2e-14)


def test_b_source_memory_bias():
    memory=c.advance_chey_memory([3,4],[2,5],.25,adaptation_tau_s=3)
    motor=c.adapted_chey_signal([2,5],memory,baseline_uM=2.59,total_uM=8)
    np.testing.assert_allclose(memory,FIXTURE['simplified']['memory'],rtol=2e-14)
    np.testing.assert_allclose(motor,FIXTURE['simplified']['motor'],rtol=2e-14)
    np.testing.assert_allclose(c.motor_bias(motor,half_uM=3.1,hill=10.3),FIXTURE['simplified']['bias'],rtol=2e-14)


def test_memory_semigroup_and_zero_tau():
    whole=c.advance_concentration_memory(1,5,3,memory_tau_s=2)
    part=c.advance_concentration_memory(1,5,1,memory_tau_s=2)
    np.testing.assert_allclose(c.advance_concentration_memory(part,5,2,memory_tau_s=2),whole,rtol=1e-15)
    assert c.advance_concentration_memory(1,5,0,memory_tau_s=0)==1
    assert c.advance_concentration_memory(1,5,1,memory_tau_s=0)==5


def test_a_and_b_are_not_interchangeable_adaptation():
    # A retains raw motor bias after concentration adapts. B returns fixed Y0.
    assert c.rebuilt_motor_bias(.8,10,10,gradient_strength_per_uM=2)==.8
    assert c.adapted_chey_signal(5,5,baseline_uM=2.59,total_uM=8)==2.59
    np.testing.assert_array_equal(c.tumble_hazard([0,1],minimum_s=.1,maximum_s=10),[.1,10])


def test_mcp_initialization_and_attractant_sign():
    pars=mwc()
    memory=c.mcp_adapted_methylation([0,1,100,1e100],pars)
    np.testing.assert_allclose(c.mcp_activity([0,1,100,1e100],memory,pars),.4,atol=1e-13)
    initial=c.mcp_adapted_methylation(1,pars)
    assert c.mcp_activity(10,initial,pars)<.4
    assert c.mcp_activity(.1,initial,pars)>.4


def test_mcp_step_response_recovers_without_consuming_ligand():
    pars=mwc(); ligand=np.array([10.]); before=ligand.copy()
    state=c.mcp_adapted_methylation(1,pars)
    immediate=c.mcp_activity(ligand,state,pars)
    for _ in range(200):
        step=c.advance_mcp_adaptation(ligand,state,.5,pars)
        assert step.activity >= immediate
        assert step.activity <= .4+2e-14
        state=step.methylation; immediate=step.activity
    np.testing.assert_allclose(immediate,.4,atol=1e-12)
    np.testing.assert_array_equal(ligand,before)


def test_mcp_backward_euler_converges_to_independent_rk4():
    pars=mwc(); initial=float(c.mcp_adapted_methylation(1,pars))
    def rhs(x):
        activity=1/(1+np.exp(4*(2*(1-x)+np.log(1+10)-np.log(1+10/100))))
        return .3*(.4-activity)
    ref=initial; h=.0005
    for _ in range(4000):
        a=rhs(ref);b=rhs(ref+h*a/2);d=rhs(ref+h*b/2);e=rhs(ref+h*d)
        ref+=h*(a+2*b+2*d+e)/6
    errors=[]
    for dt in [.2,.1,.05]:
        state=initial
        for _ in range(round(2/dt)):
            state=float(c.advance_mcp_adaptation(10,state,dt,pars).methylation)
        errors.append(abs(state-ref))
    assert errors[0]/errors[1]>1.8
    assert errors[1]/errors[2]>1.8


def test_mcp_large_step_is_bounded_by_stable_equilibrium():
    pars=mwc();old=c.mcp_adapted_methylation(1,pars)
    target=c.mcp_adapted_methylation(10,pars)
    result=c.advance_mcp_adaptation(10,old,1e6,pars)
    assert old<result.methylation<target


def test_chey_activity_exact_constant_input():
    pars=SignalParameters(6,.001,20,.3,5,8,2,10,10.3,3.1)
    out=c.advance_chey_from_activity(.4,1,.3,pars)
    equilibrium=4*8/14
    np.testing.assert_allclose(out,equilibrium+(1-equilibrium)*np.exp(-14*.3))


def test_direct_hydrolysis_stock_and_enzyme_limits():
    np.testing.assert_allclose(m.direct_hydrolysis_rate([10,20],[10,5],[10,10],turnover_s=3),[30,30])
    np.testing.assert_array_equal(m.direct_hydrolysis_rate([0,10,10],[10,0,0],[10,10,0],turnover_s=3),0)
    with pytest.raises(ValueError):m.direct_hydrolysis_rate(10,2,1,turnover_s=3)


@pytest.mark.parametrize('policy,key',[('rebuilt_monod','rebuilt'),('simplified_yield','simplified')])
def test_growth_source_values(policy,key):
    kwargs={'half_saturation_uM':10} if policy=='rebuilt_monod' else {}
    out=p.advance_growth([1000,100],[.5,.8],.25,policy=policy,max_growth_per_min=.005,volume_yield_um3_molecule=YIELD,**kwargs)
    expected=FIXTURE[key]['growth']
    np.testing.assert_allclose(out.actual_growth_per_min,expected[0],rtol=2e-14)
    np.testing.assert_allclose(out.intracellular_molecules,expected[1],atol=0)
    np.testing.assert_allclose(out.volume_um3,expected[2],rtol=2e-14)


def test_growth_laws_differ_and_mass_is_used_once():
    args=dict(max_growth_per_min=.01,volume_yield_um3_molecule=1e-5)
    a=p.advance_growth(100,.5,.1,policy='rebuilt_monod',half_saturation_uM=10,**args)
    b=p.advance_growth(100,.5,.1,policy='simplified_yield',**args)
    assert a.used_molecules<b.used_molecules
    for out in [a,b]:
        np.testing.assert_allclose(out.intracellular_molecules+out.used_molecules,100)
        np.testing.assert_allclose(out.volume_um3-.5,out.used_molecules*1e-5,atol=1e-16)
    zero=p.advance_growth(0,.5,10,policy='simplified_yield',**args)
    assert zero.volume_um3==.5 and zero.used_molecules==0


def test_copies_no_growth_dilution_and_exact_turnover():
    assert p.advance_copy_number(1000,0,0,600)==1000
    np.testing.assert_allclose(p.advance_copy_number(1000,0,.03,60),1000*np.exp(-.03))
    full=p.advance_copy_number(1000,800,.03,60)
    half=p.advance_copy_number(1000,800,.03,30)
    np.testing.assert_allclose(p.advance_copy_number(half,800,.03,30),full,rtol=1e-15)
    # New exact policy intentionally differs from the reviewed source Euler.
    exact=p.advance_copy_number([1000,3000],800,.03,.25)
    assert np.max(abs(exact-np.array(FIXTURE['simplified']['copy_euler'])))<.001


def test_rebuilt_expression_feedback_against_independent_source():
    synthesis = p.rebuilt_expression_rate([.8,.1],[.003,0],[.2,.9],
        expression_copies_min=800, health_floor=.25, health_hill=2,
        metabolic_floor=.6, metabolic_half_growth_per_min=.001,
        burden_half_fraction=.35, burden_hill=4)
    np.testing.assert_allclose(synthesis, FIXTURE['rebuilt']['expression_rate'], rtol=2e-14)


def test_health_source_comparison():
    a=p.advance_rebuilt_health([.8,.01],[.003,0],[.1,.4],[.2,.3],[0,100000],.25,ah())
    np.testing.assert_allclose(a.health,FIXTURE['rebuilt']['health'],rtol=2e-14)
    b=p.advance_simplified_health([.8,.01],[.003,0],[.1,2],.25,repair_per_min=.01,burden_per_min=.006,starvation_per_min=.002,max_growth_per_min=.005,death_max_per_min=1,death_threshold=.05)
    np.testing.assert_allclose(b.health,FIXTURE['simplified']['health'],rtol=2e-14)
    np.testing.assert_allclose(b.death_hazard_per_min,FIXTURE['simplified']['death_hazard'],rtol=2e-14)


def test_division_geometry_contents_and_new_poles():
    read=p.division_split(1,123,456,.43,radius_um=.4)
    assert read.first_volume_um3+read.second_volume_um3==1
    assert read.first_substrate_molecules+read.second_substrate_molecules==123
    assert read.first_inp_copies+read.second_inp_copies==456
    before=p.capsule_geometry_from_volume(1,.4).area_um2
    after=p.capsule_geometry_from_volume(read.first_volume_um3,.4).area_um2+p.capsule_geometry_from_volume(read.second_volume_um3,.4).area_um2
    np.testing.assert_allclose(after-before,read.extra_pole_area_um2,rtol=2e-14)
    with pytest.raises(ValueError):p.division_split(.5,10,20,.5,radius_um=.4)
    with pytest.raises(ValueError):p.division_split(1,10,20,.1,radius_um=.4)


def test_surface_adder_is_fixed_reference_increment():
    delta=p.surface_adder_delta(reference_birth_volume_um3=.5,target_volume_um3=1,radius_um=.4,cv=.15,normal_deviate=0,minimum_area_um2=1e-6)
    np.testing.assert_allclose(delta,2.5)
    geo=p.capsule_geometry_from_volume(.8,.4)
    np.testing.assert_allclose(p.capsule_volume_um3(geo.total_length_um,.8),.8)


@pytest.mark.parametrize('bad',[True,'1',float('nan'),float('inf'),-1])
def test_invalid_science_inputs_reject(bad):
    with pytest.raises(ValueError):c.advance_concentration_memory(1,2,bad,memory_tau_s=3)
    with pytest.raises(ValueError):m.direct_hydrolysis_rate(1,1,1,turnover_s=bad)


def test_shape_and_input_immutability():
    with pytest.raises(ValueError):c.advance_chey_memory([1,2],[3],1,adaptation_tau_s=3)
    original=np.array([1.,2.]);saved=original.copy()
    output=c.advance_concentration_memory(original,4,1,memory_tau_s=3)
    output[:]=0
    np.testing.assert_array_equal(original,saved)
