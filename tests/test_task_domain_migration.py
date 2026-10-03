"""Explicit fixed-coordinate domain mapping and origin inventory provenance."""
from copy import deepcopy
import numpy as np
import pytest
from test_modular_science import project, add
from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.task_migration import DOMAIN_MAPPING, MAPPING, migrate_simulation, map_field_value
from friskoli_cad.engine.task_checkpoint import save_task_checkpoint, load_task_checkpoint
from friskoli_cad.engine.runtime import GridDomain


def expanded(p):
    target=deepcopy(p)
    target['domain']['counts_xyz'][0] *= 2
    return target


def test_domain_expansion_zero_fills_and_keeps_origin_across_binary_restore(tmp_path):
    p=project(); p['species']['nutrient']['initial_concentration']['value']=2.
    scheduled=add(p,'source.spatial_schedule',{'species':'nutrient','lower_um':[0.,0.,0.],
        'upper_um':[200.,100.,2.],'events':[{'kind':'pulse','time_s':.03,'amount_molecules':100.}]})
    sim=simulation_from_project(p,seed=17); sim.step(.01)
    original=sim.checkpoint()
    target=expanded(p)
    with pytest.raises(ValueError): migrate_simulation(sim,target,MAPPING)
    moved,audit=migrate_simulation(sim,target,DOMAIN_MAPPING)
    assert audit['kind']=='physical-domain-conservative'
    assert moved.initial_amounts == sim.initial_amounts
    assert moved.streams.to_dict()==sim.streams.to_dict()
    assert moved.current.cell_frame==sim.current.cell_frame
    for species,old in sim.fields.concentrations_uM.items():
        values=np.asarray(moved.fields.concentrations_uM[species]).reshape(moved.world.grid.shape)
        np.testing.assert_array_equal(values[:,:,:sim.world.grid.nx],np.asarray(old).reshape(sim.world.grid.shape))
        assert not values[:,:,sim.world.grid.nx:].any()
    path=tmp_path/'expanded.zip'; save_task_checkpoint(moved,path,maximum=64*1024*1024)
    restored=load_task_checkpoint(path,maximum=64*1024*1024)
    assert restored.migration_origin_project==p
    for _ in range(2): moved.step(.01); restored.step(.01)
    assert moved.current.cell_frame==restored.current.cell_frame
    assert moved.streams.to_dict()==restored.streams.to_dict()
    assert moved.state[scheduled]['cumulative_input']==restored.state[scheduled]['cumulative_input']==100.
    second,_=migrate_simulation(restored,expanded(target),DOMAIN_MAPPING)
    assert second.migration_origin_project==p
    assert sim.checkpoint()==original


def test_zero_crop_succeeds_nonzero_crop_and_source_support_loss_rejected():
    p=project(); p['species']['nutrient']['initial_concentration']['value']=1.
    sim=simulation_from_project(p,seed=17); sim.step(.01)
    grown,_=migrate_simulation(sim,expanded(p),DOMAIN_MAPPING)
    cropped,_=migrate_simulation(grown,p,DOMAIN_MAPPING)
    assert cropped.initial_amounts==sim.initial_amounts
    bad=deepcopy(p); bad['domain']['counts_xyz'][0]-=1
    with pytest.raises(ValueError,match='discard|outflow'):
        migrate_simulation(sim,bad,DOMAIN_MAPPING)
    q=project()
    add(q,'source.spatial_schedule',{'species':'nutrient','lower_um':[190.,0.,0.],
        'upper_um':[200.,100.,2.],'events':[{'kind':'pulse','time_s':5.,'amount_molecules':10.}]})
    scheduled=simulation_from_project(q); scheduled.step(.01)
    target=deepcopy(q); target['domain']['counts_xyz'][0]-=1
    with pytest.raises(ValueError,match='physical support'):
        migrate_simulation(scheduled,target,DOMAIN_MAPPING)


def test_tensor_and_vector_components_preserve_amount_on_noninteger_overlap():
    source=GridDomain.volume(3,2,2,2.,3.,4.)
    target=GridDomain.volume(5,3,3,1.7,2.5,3.7)
    for shape,tail in [('field.vector',(3,)),('field.tensor',(2,3))]:
        values=np.arange(np.prod(source.shape+tail),dtype=float).reshape(source.shape+tail)
        spec={'shape':shape,'unit':'molecule','quantity':'amount','tensor_shape':list(tail)}
        mapped=map_field_value(values,spec,source,target,'components')
        np.testing.assert_allclose(mapped.sum(axis=(0,1,2)),values.sum(axis=(0,1,2)),rtol=1e-13)
        assert mapped.shape==target.shape+tail
        with pytest.raises(ValueError,match='quantity/unit'):
            map_field_value(values,{**spec,'unit':'um/s'},source,target,'velocity')


def test_high_dynamic_range_small_voxel_survives_local_overlap_refinement():
    source=GridDomain.thin_layer(2,1,1.,1.,1.)
    values=np.array([[[1e20,1.]]])
    for count in (4,6):
        target=GridDomain.thin_layer(count,1,.5,1.,1.)
        mapped=map_field_value(values,{'shape':'field.scalar','unit':'uM'},source,target,'dynamic-range')
        np.testing.assert_array_equal(mapped[0,0,:4],[1e20,1e20,1.,1.])
        assert not mapped[0,0,4:].any()


def test_declared_float32_cannot_silently_underflow_small_amount():
    source=GridDomain.thin_layer(1,1,1.,1.,1.)
    target=GridDomain.thin_layer(2,1,.5,1.,1.)
    values=np.array([[[np.nextafter(np.float32(0),np.float32(1))]]],dtype=np.float32)
    with pytest.raises(ValueError,match='dtype'):
        map_field_value(values,{'shape':'field.scalar','unit':'molecule','dtype':'float32'},source,target,'small-amount')


def test_migration_origin_cannot_change_scientific_project_data():
    from friskoli_cad.engine.modular_checkpoint import restore_checkpoint
    from friskoli_cad.engine.spatial_checkpoint import _hash
    p=project(); sim=simulation_from_project(p); sim.step(.01)
    moved,_=migrate_simulation(sim,expanded(p),DOMAIN_MAPPING)
    payload=moved.checkpoint()
    payload['migration_origin_project']['random_seed'] += 1
    payload['payload_sha256']=_hash({k:v for k,v in payload.items() if k!='payload_sha256'})
    with pytest.raises(ValueError,match='origin'):
        restore_checkpoint(moved.project,payload)


def test_domain_crop_rejects_cells_outside_target_and_tiny_nonzero_inventory():
    p=project(); sim=simulation_from_project(p); sim.step(.01)
    target=deepcopy(p); target['domain']['counts_xyz'][0]=5
    with pytest.raises(ValueError): migrate_simulation(sim,target,DOMAIN_MAPPING)
    source=GridDomain.thin_layer(2,1,1.,1.,1.)
    target=GridDomain.thin_layer(1,1,1.,1.,1.)
    with pytest.raises(ValueError,match='nonzero inventory'):
        map_field_value(np.array([[[0.,1e-300]]]),{'shape':'field.scalar','unit':'uM'},source,target,'tiny')


def test_task_domain_migration_publishes_audit_and_resumable_origin(tmp_path):
    import time
    from test_task_longrun import body, wait
    from friskoli_cad.tasks import TaskService
    from friskoli_cad.protocol.task_validation import _validator
    p=project(); p['species']['nutrient']['initial_concentration']['value']=2.
    with TaskService(tmp_path/'service') as service:
        request=body(service,30); request['project']=p
        request['execution']['semantics']='modular-spatial-v1'
        request['output_plan']['observables']=list(p['run']['channels'])
        request['version_lock']=service.version_lock(p)
        task,_=service.submit(request,'domain-source')
        deadline=time.monotonic()+30
        while service.get(task['run_id'])['progress']['committed_step']<1:
            assert time.monotonic()<deadline
            time.sleep(.005)
        service.pause(task['run_id']); assert wait(service,task['run_id'])['status']=='paused'
        payload={'project':expanded(p),'mapping':DOMAIN_MAPPING}
        _validator('MigrationPreviewRequest','0.6.0').validate(payload)
        preview=service.migrate(task['run_id'],payload)
        payload.update(request_id='expanded',edit_revision='2',preview_sha256=preview['preview_sha256'])
        _validator('MigrationRequest','0.6.0').validate(payload)
        result=service.migrate(task['run_id'],payload,'domain-child',preview=False)
        done=wait(service,result['task']['run_id'])
        assert done['status']=='completed',done['issues']
        assert done['migration_audit']['domain_rule']['coordinates']=='unchanged-physical-xyz'
        _validator('Task','0.6.0').validate(done)
        _validator('Manifest','0.6.0').validate(service.manifest(done['run_id']))
        saved,_=service.artifact(done['run_id'],'checkpoint')
        restored=load_task_checkpoint(saved,maximum=service.limits.estimated_memory_bytes)
        assert restored.migration_origin_project==p
