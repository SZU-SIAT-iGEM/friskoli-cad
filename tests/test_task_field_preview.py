"""Output-only conservative field previews retain the computation grid."""
import copy
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from friskoli_cad.engine.runtime import GridDomain
from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.protocol.task_validation import validate_submission, _validator
from friskoli_cad.tasks import TaskService
from friskoli_cad.tasks.service import TaskError
from friskoli_cad.tasks.metadata import estimate
from friskoli_cad.tasks.worker import concentration_preview

class FieldPreviewTests(unittest.TestCase):
    def test_anisotropic_means_preserve_volume_integral_and_input(self):
        domain=GridDomain.volume(8,6,4,.5,2,3)
        values=np.arange(192,dtype=float).reshape(domain.shape)
        before=values.copy()
        result=concentration_preview(values,domain,[4,2,2])
        actual=np.array(result['values_zyx'])
        expected=np.array([[[values[z:z+2,y:y+2,x:x+4].mean() for x in range(0,8,4)] for y in range(0,6,2)] for z in range(0,4,2)])
        np.testing.assert_array_equal(actual,expected)
        np.testing.assert_array_equal(values,before)
        self.assertEqual(result['field_domain']['counts_xyz'],[2,3,2])
        self.assertEqual(result['field_domain']['spacing_um_xyz'],[2,4,6])
        self.assertAlmostEqual(values.sum()*3,actual.sum()*48)
        self.assertEqual(concentration_preview(values,domain,[1,1,1])['values_zyx'],values.tolist())
    def test_finite_block_mean_when_unscaled_sum_overflows(self):
        from friskoli_cad.protocol.task_validation import canonical_bytes
        domain = GridDomain.thin_layer(4, 1, 1e-6, 1e-6, 1e-6)
        values = np.array([1e308, 1e308, 0., 0.]).reshape(domain.shape)
        result = concentration_preview(values, domain, [2, 1, 1])
        self.assertEqual(result['values_zyx'], [[[1e308, 0.]]])
        canonical_bytes(result)
        # Ordinary reduction remains bit-for-bit identical.
        ordinary = np.arange(24, dtype=float).reshape(2, 3, 4) / 7
        from friskoli_cad.tasks.artifacts import nonnegative_mean
        np.testing.assert_array_equal(nonnegative_mean(ordinary, axis=(0, 2)), ordinary.mean(axis=(0, 2)))

    def test_stride_must_divide_grid(self):
        with self.assertRaises(ValueError): concentration_preview(np.ones((2,4,4)),GridDomain.volume(4,4,2,1,1,1),[3,1,1])
    def test_admission_and_estimate(self):
        with tempfile.TemporaryDirectory() as directory, TaskService(directory) as service:
            project=make_example('chemotaxis-pts-a')
            body={'task_contract_version':'0.5.0','request_id':'preview','edit_revision':'1','project':project,'version_lock':service.version_lock(project),'execution':{'semantics':'chemotaxis-spatial-v1','backend':'numpy-cpu','seed':17,'dt_s':.05,'steps':10000},'output_plan':{'frame_every_steps':100,'observables':list(project['run']['channels']),'include_fields':True}}
            validate_submission(body)
            baseline=service.preflight(body)
            counts=project['domain']['counts_xyz']
            body['output_plan']['field_stride_xyz']=counts
            reduced=service.preflight(body)
            self.assertEqual(reduced['frames'],101)
            self.assertEqual(reduced['estimate']['memory_bytes'],baseline['estimate']['memory_bytes'])
            self.assertLess(reduced['estimate']['output_bytes'],baseline['estimate']['output_bytes'])
            body['output_plan']['field_stride_xyz']=[counts[0]+1,1,1]
            with self.assertRaises(TaskError) as raised: service.preflight(body)
            self.assertEqual(raised.exception.code,'task.field_stride')
            body['output_plan']['field_stride_xyz']=[1,1,1]
            body['execution']['backend']='numpy-cupy-cuda'
            with patch('friskoli_cad.tasks.service.available_backends',return_value=['numpy-cpu']):
                with self.assertRaises(TaskError) as raised: service.preflight(body)
                self.assertEqual(raised.exception.code,'task.execution_unsupported')
    def test_worker_legacy_bytes_and_new_envelope(self):
        from friskoli_cad.project import simulation_from_project
        from friskoli_cad.tasks.worker import run_worker
        from friskoli_cad.protocol.task_validation import canonical_loads
        project=make_example('chemotaxis-pts-a')
        body={'task_contract_version':'0.5.0','project':project,'execution':{'semantics':'chemotaxis-spatial-v1','backend':'numpy-cpu','seed':17,'dt_s':.05,'steps':2},'output_plan':{'frame_every_steps':2,'observables':list(project['run']['channels']),'include_fields':True,'field_stride_xyz':project['domain']['counts_xyz']}}
        class Channel:
            def __init__(self):self.messages=[]
            def send_bytes(self,data):self.messages.append(canonical_loads(data))
            def recv_bytes(self):return b"\x01"
            def close(self):pass
        def simulation(document,seed,field_backend):
            self.assertEqual(field_backend,'numpy-cpu')
            return simulation_from_project(document,seed=seed)
        results=[]
        for version in ('0.5.0','0.4.0'):
            body['task_contract_version']=version
            channel=Channel()
            with patch('friskoli_cad.tasks.worker.source_hashes',return_value={}), patch('friskoli_cad.tasks.worker.simulation_from_project',side_effect=simulation):
                run_worker(body,channel,{'chunk_bytes':4*1024*1024,'cells':2000,'voxels':262144},{})
            self.assertTrue(all(m['kind']=='step' for m in channel.messages),channel.messages)
            results.append([m for m in channel.messages if 'frame' in m])
        self.assertEqual(len(results[0]),2)
        for preview,legacy in zip(*results):
            self.assertEqual(preview['frame'],legacy['frame'])
            self.assertEqual(preview['metrics'],legacy['metrics'])
            for key,item in preview['concentrations'].items():
                self.assertEqual(item['field_domain']['counts_xyz'],[1,1,1])
                self.assertAlmostEqual(item['values_zyx'][0][0][0],np.asarray(legacy['concentrations'][key]['values_zyx']).mean())
                self.assertEqual(set(legacy['concentrations'][key]),{'unit','values_zyx'})
            envelope={k:v for k,v in preview.items() if k not in ('kind','final','step')}
            envelope.update(sequence=0,step_index=preview['step'])
            _validator('FrameEnvelope','0.5.0').validate(envelope)

    def test_large_grid_preview_estimate_keeps_full_memory(self):
        from friskoli_cad.engine.profiles import registry_for_profile,CHEMOTAXIS_PROFILE
        project=make_example('chemotaxis-pts-a'); project['domain'].update(geometry='volume',counts_xyz=[256]*3,spacing_um_xyz=[.5]*3)
        body={'project':project,'execution':{'steps':10000},'output_plan':{'frame_every_steps':100,'observables':[],'include_fields':True,'field_stride_xyz':[8]*3}}
        budget=estimate(body,registry_for_profile(CHEMOTAXIS_PROFILE))
        self.assertEqual(budget['voxels'],256**3)
        full=copy.deepcopy(body);full['output_plan']['field_stride_xyz']=[1]*3
        original=estimate(full,registry_for_profile(CHEMOTAXIS_PROFILE))
        self.assertEqual(original['memory_bytes'],budget['memory_bytes'])
        self.assertGreater(original['output_bytes'],budget['output_bytes']*100)

if __name__=='__main__':unittest.main()
