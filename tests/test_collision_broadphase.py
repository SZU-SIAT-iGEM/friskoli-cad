"""Compare conservative broad phase to unchanged full-pair narrow phase."""
from dataclasses import replace
import math
import numpy as np
import pytest
from friskoli_cad.engine.collision import Capsule, BoxObstacle, guard_motion, InitialOverlapError


def compare(start,end,**kwargs):
    options={'extent_um':(128.,128.,128.),**kwargs}
    try:
        reference=guard_motion(start,end,use_broad_phase=False,**options)
    except InitialOverlapError as original:
        with pytest.raises(InitialOverlapError) as optimized:
            guard_motion(start,end,use_broad_phase=True,**options)
        assert optimized.value.contacts==original.contacts
        return
    actual=guard_motion(start,end,use_broad_phase=True,**options)
    assert actual.capsules==reference.capsules
    assert actual.blocked_ids==reference.blocked_ids
    assert actual.contacts==reference.contacts
    assert actual.policy==reference.policy
    assert actual.evaluations<=reference.evaluations


def body(id,position,heading=(1.,0.,0.),length=2.,diameter=.8):
    return Capsule(id,position,heading,length,diameter)


@pytest.mark.parametrize('seed',range(12))
def test_random_translation_rotation_and_obstacles_match_full_pairs(seed):
    rng=np.random.default_rng(seed)
    start=[];end=[]
    for i in range(12):
        heading=rng.normal(size=3);heading/=np.linalg.norm(heading)
        position=(10.+(i%4)*5,10.+((i//4)%3)*5,12.)
        cell=body(str(i),position,tuple(heading),length=rng.uniform(1.,4.))
        start.append(cell)
        rotation=rng.normal(size=3);rotation/=np.linalg.norm(rotation)
        end.append(replace(cell,position_um=tuple(np.asarray(position)+rng.normal(size=3)*3.),heading=tuple(rotation)))
    boxes=[BoxObstacle('near',(35.,5.,5.),(38.,25.,25.)),BoxObstacle('far',(100.,100.,100.),(110.,110.,110.))]
    compare(start,end,obstacles=boxes,max_subdivisions=1 if seed%3==0 else 256)


def test_high_speed_crossing_rotation_and_blocked_propagation():
    start=[body('a',(5.,8.,8.)),body('b',(15.,8.,8.)),body('c',(25.,8.,8.))]
    end=[replace(start[0],position_um=(25.,8.,8.)),replace(start[1],position_um=(5.,8.,8.)),replace(start[2],position_um=(15.,8.,8.))]
    compare(start,end)
    rotating=[body('rotating',(10.,10.,10.),length=10.),body('fixed',(10.,14.,10.))]
    compare(rotating,[replace(rotating[0],heading=(0.,1.,0.)),rotating[1]])


def test_tangent_boundary_near_parallel_and_initial_overlap_diagnostics():
    a=body('a',(1.,10.,10.));b=body('b',(5.,10.,10.))
    compare([a,b],[replace(a,position_um=(.9,10.,10.)),b])
    tangent=body('tangent',(10.,10.8,10.),heading=(math.cos(1e-8),math.sin(1e-8),0.))
    compare([body('base',(10.,10.,10.)),tangent],[body('base',(10.,10.,10.)),replace(tangent,position_um=(10.001,10.8,10.))],max_subdivisions=1)
    invalid=[body('one',(10.,10.,10.)),body('two',(10.,10.,10.))]
    compare(invalid,invalid)
    box=BoxObstacle('solid',(9.,9.,9.),(11.,11.,11.))
    compare([invalid[0]],[invalid[0]],obstacles=[box])


def test_sparse_200_cells_skip_far_pairs_without_changing_output():
    start=[body(str(i),(8.+(i%10)*11.,8.+((i//10)%10)*11.,8.+(i//100)*20.)) for i in range(200)]
    end=[replace(c,position_um=(c.position_um[0]+.1,*c.position_um[1:])) for c in start]
    compare(start,end)
    optimized=guard_motion(start,end,extent_um=(128.,)*3)
    assert optimized.evaluations==0 # Swept spheres clear of walls and each other are proven safe without certification.


@pytest.mark.parametrize('seed',range(8))
def test_mixed_wall_obstacle_and_cluster_cells_match_full_certification(seed):
    rng=np.random.default_rng(100+seed)
    extent=(60.,30.,12.)
    start=[];end=[]
    for i in range(10):
        for j in range(4):
            for k in range(2):
                heading=rng.normal(size=3);heading/=np.linalg.norm(heading)
                position=np.asarray((3.+6.*i,3.75+7.5*j,3.+6.*k))+rng.uniform(-.8,.8,3)
                cell=body(f'{i}-{j}-{k}',tuple(position),tuple(heading))
                start.append(cell)
                step=rng.normal(size=3)*rng.choice([.2,2.,6.])
                rotation=rng.normal(size=3);rotation/=np.linalg.norm(rotation)
                end.append(replace(cell,position_um=tuple(position+step),heading=tuple(rotation)))
    boxes=[BoxObstacle('block',(29.5,5.,0.),(30.5,20.,12.))]
    reference=guard_motion(start,end,extent_um=extent,obstacles=boxes,use_broad_phase=False)
    assert reference.blocked_ids and len(reference.blocked_ids)<len(start)
    compare(start,end,extent_um=extent,obstacles=boxes)


def test_empty_and_sphere_limit():
    compare([],[])
    cells=[body('sphere',(10.,10.,10.),length=.8),body('capsule',(20.,10.,10.))]
    compare(cells,[replace(c,position_um=(c.position_um[0]+.1,*c.position_um[1:])) for c in cells])


@pytest.mark.parametrize('example',['center-pts-a-small','modular-foundation'])
def test_full_simulation_checkpoint_including_rng_matches_reference(example,monkeypatch):
    from friskoli_cad.engine import modular_runtime as spatial_runtime
    from friskoli_cad.engine.presets import make_example
    from friskoli_cad.replay_service import prepare_project
    project=make_example(example)
    monkeypatch.setattr(spatial_runtime,'guard_motion',lambda *args,**kwargs:guard_motion(*args,**kwargs,use_broad_phase=False))
    reference=prepare_project(project,dt_s=.01,steps=6)
    for _ in range(6):reference.step(.01)
    expected=reference.checkpoint()
    monkeypatch.setattr(spatial_runtime,'guard_motion',guard_motion)
    actual=prepare_project(project,dt_s=.01,steps=6)
    for _ in range(6):actual.step(.01)
    assert actual.checkpoint()==expected


def test_check_walls_false_ignores_domain_walls_but_still_guards_cells_and_solids():
    a=body('a',(1.,10.,10.))
    crossing=replace(a,position_um=(.2,10.,10.))
    assert guard_motion([a],[crossing],extent_um=(128.,)*3).blocked_ids==('a',)
    free=guard_motion([a],[crossing],extent_um=(128.,)*3,check_walls=False)
    assert free.blocked_ids==() and free.capsules[0].position_um==crossing.position_um
    box=BoxObstacle('solid',(5.,9.,9.),(6.,11.,11.))
    assert guard_motion([a],[replace(a,position_um=(5.5,10.,10.))],extent_um=(128.,)*3,obstacles=[box],check_walls=False).blocked_ids==('a',)
    pair=[body('p',(10.,10.,10.)),body('q',(14.,10.,10.))]
    moved=[replace(pair[0],position_um=(13.5,10.,10.)),pair[1]]
    assert guard_motion(pair,moved,extent_um=(128.,)*3,check_walls=False).blocked_ids==('p',)


def test_contested_mask_without_walls_ignores_boundary_contact():
    from friskoli_cad.engine.collision import contested_mask
    near_wall=[[.2,5.,5.]]
    assert contested_mask(near_wall,[1.],[.5],(10.,10.,10.),(),1e-9)[0]
    assert not contested_mask(near_wall,[1.],[.5],None,(),1e-9)[0]
