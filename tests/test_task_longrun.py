import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
import numpy as np
from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.task_checkpoint import save_task_checkpoint, load_task_checkpoint
from friskoli_cad.tasks.arrays import write_array, read_array
from friskoli_cad.tasks import TaskService


def body(service, steps=5):
    project = make_example('chemotaxis-pts-a')
    return {'task_contract_version':'0.6.0','request_id':'longrun','edit_revision':'1',
        'project':project, 'version_lock':service.version_lock(project),
        'execution':{'semantics':'chemotaxis-spatial-v1','backend':'numpy-cpu','seed':17,'dt_s':.01,'steps':steps},
        'output_plan':{'frame_every_steps':3,'checkpoint_every_steps':2,'observables':list(project['run']['channels']),'include_fields':True}}


def wait(service, identifier):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        item = service.get(identifier)
        if item['status'] in ('completed','failed','paused','cancelled','interrupted'):
            return item
        time.sleep(.02)
    raise AssertionError('Task did not stop')


class LongrunTests(unittest.TestCase):
    def test_collision_workspace_scales_with_declared_population_squared(self):
        from test_modular_science import project, add, edge
        from friskoli_cad.engine.science_extensions import modular_registry
        from friskoli_cad.tasks.metadata import estimate
        p=project()
        old=next(n for n in p['graph']['nodes'] if n['module_id']=='metabolism.reserve_balance')
        nid=old['id']; p['graph']['nodes'].remove(old)
        add(p,'metabolism.shared_inventory',{'species':'nutrient','initial_molecules':1e6,
            'maintenance_molecules_s':1.,'max_growth_per_min':60.,'volume_yield_um3_molecule':.001},nid=nid,population=True)
        division=add(p,'division.volume_adder',{'added_volume_um3':.01,'minimum_volume_um3':0.,'daughter_fraction':.5},population=True)
        edge(p,nid,'volume',division,'volume')
        submission={'project':p,'task_contract_version':'0.6.0','execution':{'backend':'numpy-cpu','steps':1},
                    'output_plan':{'frame_every_steps':1,'include_fields':False,'observables':[]}}
        sizes={}
        for cap in (300,1000,2000):
            p['system_limits']={'max_cells':cap}
            sizes[cap]=estimate(submission,modular_registry())['memory_bytes']
        self.assertGreaterEqual(sizes[2000]-sizes[300],32*(2000**2-300**2))
        self.assertGreater((sizes[2000]-sizes[1000])/1000,(sizes[1000]-sizes[300])/700)

    def test_project_cell_cap_stops_task_without_committing_division(self):
        from test_modular_science import project, add, edge
        from friskoli_cad.tasks import TaskError
        from friskoli_cad.engine.task_migration import MAPPING
        p=project()
        old=next(n for n in p['graph']['nodes'] if n['module_id']=='metabolism.reserve_balance')
        nid=old['id']; p['graph']['nodes'].remove(old)
        add(p,'metabolism.shared_inventory',{'species':'nutrient','initial_molecules':1e6,
            'maintenance_molecules_s':1.,'max_growth_per_min':60.,'volume_yield_um3_molecule':.001},nid=nid,population=True)
        p['run']['channels'][nid+'.used_molecules']['quantity']='consumed_amount'
        division=add(p,'division.volume_adder',{'added_volume_um3':.01,'minimum_volume_um3':0.,'daughter_fraction':.5},population=True)
        edge(p,nid,'volume',division,'volume')
        initial_count=sum(len(g['ids']) for g in p['groups'].values())
        p['system_limits']={'max_cells':initial_count}
        with tempfile.TemporaryDirectory() as folder, TaskService(folder) as service:
            submission=body(service,1); submission['project']=p
            submission['execution'].update(semantics='modular-spatial-v1',dt_s=.2)
            submission['output_plan']['observables']=list(p['run']['channels'])
            submission['version_lock']=service.version_lock(p)
            self.assertEqual(service.capabilities('modular-spatial-v1')['limits']['cells'],service.limits.cells)
            too_large=copy.deepcopy(submission); too_large['project']['system_limits']['max_cells']=service.limits.cells+1
            with self.assertRaises(TaskError): service.preflight(too_large)
            larger=copy.deepcopy(submission); larger['project']['system_limits']['max_cells']=300
            self.assertGreater(service.preflight(larger)['estimate']['memory_bytes'],service.preflight(submission)['estimate']['memory_bytes'])
            task,_=service.submit(submission,'cell-cap')
            stopped=wait(service,task['run_id'])
            self.assertEqual(stopped['status'],'failed')
            self.assertEqual(stopped['issues'][0]['code'],'resource.cell_limit')
            self.assertEqual(stopped['progress']['committed_step'],0)
            path,_=service.artifact(task['run_id'],'checkpoint')
            restored=load_task_checkpoint(path,maximum=service.limits.estimated_memory_bytes)
            self.assertEqual(sum(len(g.ids) for g in restored.world.groups.values()),initial_count)
            target=copy.deepcopy(p); target['system_limits']['max_cells']=initial_count*2
            request={'project':target,'mapping':MAPPING}
            preview=service.migrate(task['run_id'],request)
            migrated=service.migrate(task['run_id'],{**request,'request_id':'raised-cap','edit_revision':'2',
                'preview_sha256':preview['preview_sha256']},'raised-cap',preview=False)
            completed=wait(service,migrated['task']['run_id'])
            self.assertEqual(completed['status'],'completed',completed['issues'])

    def test_child_first_frame_preserves_committed_boundary_events(self):
        from unittest.mock import patch
        from friskoli_cad.tasks.worker import run_worker
        from friskoli_cad.protocol.task_validation import canonical_loads
        from dataclasses import asdict
        from friskoli_cad.tasks import TaskLimits
        class Channel:
            def __init__(self): self.messages=[]
            def send_bytes(self,data): self.messages.append(canonical_loads(data))
            def recv_bytes(self): return b'\x01'
            def close(self): pass
        project = make_example('chemotaxis-pts-a')
        simulation = simulation_from_project(project,seed=17)
        simulation.step(.01)
        events = [{'event_id':'boundary-event', 'step':1}]
        with tempfile.TemporaryDirectory() as folder:
            resume = Path(folder)/'resume.zip'
            save_task_checkpoint(simulation,resume,maximum=64*1024*1024,task_context={
                'last_output_step':1,'last_output_events':events,'last_output_deaths':[],
                'pending_events':[],'pending_deaths':[]})
            submission={'task_contract_version':'0.6.0','project':project,
                'execution':{'semantics':'chemotaxis-spatial-v1','backend':'numpy-cpu','seed':17,'dt_s':.01,'steps':2},
                'output_plan':{'frame_every_steps':2,'observables':list(project['run']['channels']),'include_fields':False}}
            channel=Channel()
            with patch('friskoli_cad.tasks.worker.source_hashes',return_value={}):
                run_worker(submission,channel,asdict(TaskLimits()),{},str(Path(folder)/'final.zip'),str(resume))
            frames=[m for m in channel.messages if 'frame' in m]
            self.assertEqual(frames[0]['step'],1,channel.messages)
            self.assertEqual(frames[0]['frame']['events'],events)
            self.assertNotIn(events[0],frames[-1]['frame']['events'])

    def test_migration_quantity_mapping_and_unknown_shape_rejection(self):
        from friskoli_cad.engine.task_migration import map_field_value
        simulation = simulation_from_project(make_example('chemotaxis-pts-a'), seed=17)
        target_project = copy.deepcopy(simulation.project)
        target_project['domain']['counts_xyz'][0] *= 2
        target_project['domain']['spacing_um_xyz'][0] /= 2
        target = simulation_from_project(target_project, seed=17)
        source, grid = simulation.world.grid, target.world.grid
        values = np.ones(source.shape)
        amount = map_field_value(values, {'shape':'field.scalar','quantity':'amount','unit':'molecule'}, source, grid, 'amount')
        self.assertAlmostEqual(float(amount.sum()), float(values.sum()))
        concentration = map_field_value(values, {'shape':'field.scalar','quantity':'concentration','unit':'uM'}, source, grid, 'concentration')
        np.testing.assert_allclose(concentration, 1.)
        for spec in ({'shape':'field.tensor','unit':'uM'}, {'shape':'field.scalar','unit':'1'}):
            with self.assertRaises(ValueError): map_field_value(values,spec,source,grid,'unknown')

    def test_binary_checkpoint_rng_continuation(self):
        project = make_example('chemotaxis-pts-a')
        continuous = simulation_from_project(project, seed=17)
        for _ in range(3): continuous.step(.01)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.zip'
            save_task_checkpoint(continuous, path, maximum=64*1024*1024)
            restored = load_task_checkpoint(path, maximum=64*1024*1024)
            for _ in range(3):
                a, b = continuous.step(.01), restored.step(.01)
                self.assertEqual(a.cell_frame, b.cell_frame)
                self.assertEqual(continuous.streams.to_dict(), restored.streams.to_dict())

    def test_binary_array_tamper_and_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            values = np.arange(600., dtype=float).reshape(10,10,6)
            desc = write_array(values, folder, 'array_0', 1024)
            np.testing.assert_array_equal(values, read_array(desc,folder,4800,1024))
            with self.assertRaises(ValueError): read_array(desc,folder,4799,1024)
            (Path(folder)/desc['segments'][0]['name']).write_bytes(b'x'*1024)
            with self.assertRaises(ValueError): read_array(desc,folder,4800,1024)

    def test_spawn_binary_frames_checkpoint_and_pause_resume(self):
        with tempfile.TemporaryDirectory() as folder, TaskService(folder) as service:
            request = body(service, 12)
            task, _ = service.submit(request,'pause')
            service.pause(task['run_id'])
            stopped = wait(service,task['run_id'])
            self.assertEqual(stopped['status'],'paused',stopped['issues'])
            resumed, _ = service.resume(task['run_id'],{'request_id':'next','edit_revision':'1'},'resume')
            completed = wait(service,resumed['run_id'])
            self.assertEqual(completed['status'],'completed',completed['issues'])
            self.assertEqual(completed['progress']['committed_step'],12)
            manifest = service.manifest(resumed['run_id'])
            self.assertEqual(manifest['parent_run_id'],task['run_id'])
            chunk = json.loads(service.chunk(resumed['run_id'],manifest['chunks'][-1]['chunk_id']))
            field = next(iter(chunk['frames'][0]['concentrations'].values()))
            self.assertNotIn('values_zyx',field)
            for segment in field['array']['segments']:
                path, metadata = service.artifact(resumed['run_id'],segment['name'][:-4])
                self.assertEqual(path.stat().st_size,metadata['bytes'])

    def test_twelve_hour_admission_preserves_dt(self):
        with tempfile.TemporaryDirectory() as folder, TaskService(folder) as service:
            request = body(service,4320000)
            request['output_plan']['frame_every_steps'] = 43200
            request['output_plan']['include_fields'] = False
            result = service.preflight(request)
            self.assertTrue(result['valid'])
            self.assertEqual(request['execution']['dt_s']*request['execution']['steps'],43200)

    def test_service_migration_checks_preview_and_resource_budget(self):
        from dataclasses import replace
        from friskoli_cad.engine.task_migration import MAPPING
        from friskoli_cad.tasks import TaskError
        with tempfile.TemporaryDirectory() as folder, TaskService(folder) as service:
            submission=body(service,80)
            task,_=service.submit(submission,'migration-source')
            deadline=time.monotonic()+30
            while service.get(task['run_id'])['progress']['committed_step'] < 1:
                assert time.monotonic() < deadline
                time.sleep(.005)
            service.pause(task['run_id'])
            self.assertEqual(wait(service,task['run_id'])['status'],'paused')
            target=copy.deepcopy(submission['project'])
            target['domain']['counts_xyz'][0] *= 2
            target['domain']['spacing_um_xyz'][0] /= 2
            request={'project':target,'mapping':MAPPING}
            old_limits=service.limits
            service.limits=replace(old_limits,voxels=1)
            try:
                with self.assertRaises(TaskError): service.migrate(task['run_id'],request)
            finally:
                service.limits=old_limits
            preview=service.migrate(task['run_id'],request)
            execute={**request,'request_id':'migrated','edit_revision':'2','preview_sha256':'0'*64}
            with self.assertRaises(TaskError): service.migrate(task['run_id'],execute,'migrate',preview=False)
            self.assertEqual(service.get(task['run_id'])['status'],'paused')
            execute['preview_sha256']=preview['preview_sha256']
            result=service.migrate(task['run_id'],execute,'migrate',preview=False)
            completed=wait(service,result['task']['run_id'])
            self.assertEqual(completed['status'],'completed',completed['issues'])
            self.assertEqual(completed['parent_run_id'],task['run_id'])

    def test_same_domain_migration_preserves_stocks_rng_and_rejects_hidden_changes(self):
        from friskoli_cad.engine.task_migration import migrate_simulation, MAPPING
        project = make_example('chemotaxis-pts-a')
        simulation = simulation_from_project(project,seed=17)
        simulation.step(.01)
        target = copy.deepcopy(project)
        target['domain']['counts_xyz'][0] *= 2
        target['domain']['spacing_um_xyz'][0] /= 2
        candidate, audit = migrate_simulation(simulation,target,MAPPING)
        self.assertEqual(candidate.streams.to_dict(),simulation.streams.to_dict())
        self.assertEqual(candidate.frame_index,simulation.frame_index)
        for item in audit['inventories'].values():
            self.assertAlmostEqual(item['before_molecules'],item['after_molecules'],places=6)
        candidate.step(.01)
        bad = copy.deepcopy(target)
        bad['domain']['spacing_um_xyz'][0] *= 2
        with self.assertRaises(ValueError): migrate_simulation(simulation,bad,MAPPING)
        self.assertEqual(simulation.project,project)

    def test_new_task_contracts_validate_published_binary_results(self):
        from friskoli_cad.protocol.task_validation import _validator
        with tempfile.TemporaryDirectory() as folder, TaskService(folder) as service:
            task,_ = service.submit(body(service,2),'schema')
            stopped = wait(service,task['run_id'])
            self.assertEqual(stopped['status'],'completed',stopped['issues'])
            _validator('Task','0.6.0').validate(stopped)
            manifest = service.manifest(task['run_id'])
            _validator('Manifest','0.6.0').validate(manifest)
            for chunk in manifest['chunks']:
                _validator('ChunkBody','0.6.0').validate(json.loads(service.chunk(task['run_id'],chunk['chunk_id'])))

    def test_nonzero_pause_and_service_reopen_resume_matches_continuous_rng(self):
        with tempfile.TemporaryDirectory() as folder:
            with TaskService(folder) as service:
                submission = body(service,80)
                submission['output_plan']['frame_every_steps'] = 1
                task,_ = service.submit(submission,'nonzero-pause')
                deadline = time.monotonic()+30
                while service.get(task['run_id'])['progress']['committed_step'] < 2:
                    assert time.monotonic() < deadline
                    time.sleep(.005)
                service.pause(task['run_id'])
                stopped = wait(service,task['run_id'])
                self.assertEqual(stopped['status'],'paused',stopped['issues'])
                self.assertGreaterEqual(stopped['checkpoint']['step_index'],2)
            with TaskService(folder) as service:
                resumed,_ = service.resume(task['run_id'],{'request_id':'after-reopen','edit_revision':'1'},'after-reopen')
                complete = wait(service,resumed['run_id'])
                self.assertEqual(complete['status'],'completed',complete['issues'])
                path,_ = service.artifact(resumed['run_id'],'checkpoint')
                restored = load_task_checkpoint(path,maximum=service.limits.estimated_memory_bytes)
                continuous = simulation_from_project(submission['project'],seed=17)
                for _ in range(80): continuous.step(.01)
                self.assertEqual(restored.current.cell_frame,continuous.current.cell_frame)
                self.assertEqual(restored.streams.to_dict(),continuous.streams.to_dict())

    def test_modular_profile_uses_registered_plan_binary_checkpoint_and_migration(self):
        from test_modular_science import project
        from friskoli_cad.protocol.task_validation import _validator
        from friskoli_cad.engine.task_migration import migrate_simulation, MAPPING
        with tempfile.TemporaryDirectory() as folder, TaskService(folder) as service:
            submission = body(service,2)
            submission['project'] = project()
            submission['execution']['semantics'] = 'modular-spatial-v1'
            submission['output_plan']['observables'] = list(submission['project']['run']['channels'])
            submission['version_lock'] = service.version_lock(submission['project'])
            self.assertTrue(service.preflight(submission)['valid'])
            task,_ = service.submit(submission,'modular')
            complete = wait(service,task['run_id'])
            self.assertEqual(complete['status'],'completed',complete['issues'])
            manifest = service.manifest(task['run_id'])
            self.assertIn('execution_plan',manifest['compiled_plan'])
            _validator('Manifest','0.6.0').validate(manifest)
            checkpoint,_ = service.artifact(task['run_id'],'checkpoint')
            restored = load_task_checkpoint(checkpoint,maximum=service.limits.estimated_memory_bytes)
            target = copy.deepcopy(submission['project'])
            target['domain']['counts_xyz'][0] *= 2
            target['domain']['spacing_um_xyz'][0] /= 2
            candidate,_ = migrate_simulation(restored,target,MAPPING)
            self.assertEqual(candidate.frame_index,2)
            candidate.step(.01)
            field_node = next(node for node in restored.plan.nodes if any(
                spec.get('shape') == 'field.scalar' for spec in restored.registry.get(node.module_id,node.module_version).manifest.get('state',{}).values()))
            manifest = restored.registry.get(field_node.module_id,field_node.module_version).manifest
            state_spec = next(spec for spec in manifest['state'].values() if spec.get('shape') == 'field.scalar')
            policy = state_spec.pop('on_migration')
            try:
                with self.assertRaisesRegex(ValueError,'state migration mapper'):
                    migrate_simulation(restored,target,MAPPING)
            finally:
                state_spec['on_migration'] = policy
