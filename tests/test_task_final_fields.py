"""Full final fields use durable, independently downloadable, safe NPZ artifacts."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen
import numpy as np
from friskoli_cad.replay_service import ReplayServer
from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import _validator
from friskoli_cad.tasks import TaskError, TaskService

class FinalFieldArtifactTests(unittest.TestCase):
    def test_spawn_artifact_download_recovery_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            server=ReplayServer(('127.0.0.1',0),task_directory=directory)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                service=server.task_service
                project=make_example('chemotaxis-pts-a')
                body={'task_contract_version':'0.5.0','request_id':'npz','edit_revision':'1','project':project,'version_lock':service.version_lock(project),'execution':{'semantics':'chemotaxis-spatial-v1','backend':'numpy-cpu','seed':17,'dt_s':.05,'steps':2},'output_plan':{'frame_every_steps':2,'observables':[],'include_fields':False,'include_final_fields':True}}
                task,_=service.submit(body,'npz-final')
                run=task['run_id'];deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    task=service.get(run)
                    if task['status'] not in ('queued','running'):break
                    time.sleep(.02)
                self.assertEqual(task['status'],'completed',task['issues'])
                manifest=service.manifest(run)
                _validator('Manifest','0.5.0').validate(manifest)
                artifact=manifest['artifacts'][0]
                with urlopen(f'http://127.0.0.1:{server.server_port}'+artifact['href']) as response:
                    raw=response.read();self.assertEqual(response.headers['Content-Type'],'application/octet-stream')
                self.assertEqual(len(raw),artifact['bytes']);self.assertEqual(hashlib.sha256(raw).hexdigest(),artifact['sha256'])
                sim=simulation_from_project(project,seed=17);sim.step(.05);snap=sim.step(.05)
                with np.load(io.BytesIO(raw),allow_pickle=False) as data:
                    metadata=json.loads(data['metadata_utf8'].tobytes())
                    self.assertEqual(metadata['field_domain'],project['domain'])
                    for field in metadata['fields']:
                        np.testing.assert_array_equal(data[field['array_key']],snap.concentration_fields[field['species']])
                path,_=service.artifact(run,'final_fields')
                self.assertFalse(path.with_name('final-fields.pending.npz').exists())
            finally:
                server.shutdown();thread.join(5);server.server_close()
            with TaskService(directory) as restored:
                self.assertEqual(restored.manifest(run)['artifacts'],manifest['artifacts'])
                restored.artifact(run,'final_fields')
                with path.open('ab') as stream:stream.write(b'corrupt')
                with self.assertRaises(TaskError) as raised:restored.artifact(run,'final_fields')
                self.assertEqual(raised.exception.code,'task.output_corrupt')
    def test_finite_npz_statistics_when_concentration_sum_overflows(self):
        from types import SimpleNamespace
        from friskoli_cad.engine.runtime import GridDomain
        from friskoli_cad.engine.local_fields import FieldSpecies, make_local_field_state, _mass
        from friskoli_cad.tasks.artifacts import write_final_fields, field_total_molecules
        domain = GridDomain.thin_layer(2, 1, 1e-6, 1e-6, 1e-6)
        state = make_local_field_state(domain, [FieldSpecies('n', 1e308, 0.)])
        values = np.asarray(state.concentrations_uM['n']).reshape(domain.shape)
        snapshot = SimpleNamespace(domain=domain, cell_frame={'time_s': 1.},
            concentration_fields={'n': values}, concentration_units={'n': 'uM'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'final.npz'
            result = write_final_fields(snapshot, 1, path, 65536)
            self.assertEqual(result['fields'][0]['mean'], 1e308)
            self.assertEqual(result['fields'][0]['total_molecules'], _mass(values, domain.molecules_per_uM_voxel))
            with np.load(path, allow_pickle=False) as data:
                np.testing.assert_array_equal(data['field_0000'], values)
                metadata = json.loads(data['metadata_utf8'].tobytes())
                self.assertEqual(metadata['fields'], result['fields'])
        ordinary = np.arange(24, dtype=float).reshape(2, 3, 4) / 7
        self.assertEqual(field_total_molecules(ordinary, .123), float(ordinary.sum() * .123))

    def test_preview_budget_separates_artifact_from_single_frame(self):
        from friskoli_cad.tasks.artifacts import final_field_estimate
        from friskoli_cad.tasks.metadata import estimate
        from friskoli_cad.engine.profiles import registry_for_profile,CHEMOTAXIS_PROFILE
        project=make_example('chemotaxis-pts-a')
        project['domain'].update(geometry='volume',counts_xyz=[256]*3,spacing_um_xyz=[.5]*3)
        body={'project':project,'execution':{'steps':10000},'output_plan':{'frame_every_steps':100,'observables':[],'include_fields':True,'field_stride_xyz':[8]*3,'include_final_fields':True}}
        full=estimate(body,registry_for_profile(CHEMOTAXIS_PROFILE));raw=final_field_estimate(body)
        body['output_plan']['include_final_fields']=False
        preview=estimate(body,registry_for_profile(CHEMOTAXIS_PROFILE))
        self.assertEqual(full['output_bytes']-preview['output_bytes'],raw)
        self.assertGreaterEqual(raw,256**3*8)

    def test_cuda_worker_records_environment_when_available(self):
        from friskoli_cad.engine.field_backend import available_backends
        if 'numpy-cupy-cuda' not in available_backends():self.skipTest('CUDA unavailable')
        with tempfile.TemporaryDirectory() as directory, TaskService(directory) as service:
            project=make_example('chemotaxis-pts-a')
            body={'task_contract_version':'0.5.0','request_id':'cuda','edit_revision':'1','project':project,'version_lock':service.version_lock(project),'execution':{'semantics':'chemotaxis-spatial-v1','backend':'numpy-cupy-cuda','seed':17,'dt_s':.05,'steps':2},'output_plan':{'frame_every_steps':2,'observables':[],'include_fields':True,'field_stride_xyz':project['domain']['counts_xyz']}}
            task,_=service.submit(body,'cuda-small');deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                task=service.get(task['run_id'])
                if task['status'] not in ('queued','running'):break
                time.sleep(.02)
            self.assertEqual(task['status'],'completed',task['issues'])
            manifest=service.manifest(task['run_id'])
            _validator('Manifest','0.5.0').validate(manifest)
            self.assertEqual(manifest['provenance']['backend'],'numpy-cupy-cuda')
            self.assertTrue(manifest['provenance']['backend_environment']['cupy_version'])
            self.assertTrue(manifest['provenance']['backend_environment']['device_name'])

if __name__=='__main__':unittest.main()
