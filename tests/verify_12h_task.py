"""Opt-in 43,200 x 1 s foundational task check, not PTS-A or calibration.

Run against a fixed checkout: python tests/verify_12h_task.py OUTPUT_DIRECTORY.
The independent one-voxel finite-inventory case has constant motor bias, zero
diffusion, 0.01 um/s motion, saturating uptake, reserve and starvation survival.
One-second steps are selected for this constructed numerical case from outset.
"""
import json
from pathlib import Path
import sys
import time
import numpy as np
from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.engine.task_checkpoint import load_task_checkpoint
from friskoli_cad.tasks import TaskService


def main(directory):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    project = make_example('foundation-control')
    project['id'] = 'longrun-foundation-1s'
    project['domain']['spacing_um_xyz'] = [a*b for a,b in zip(project['domain']['counts_xyz'],project['domain']['spacing_um_xyz'])]
    project['domain']['counts_xyz'] = [1,1,1]
    for group in project['groups'].values():
        for key,value in group.items():
            if isinstance(value,list): group[key] = value[:1]
    project['graph']['nodes'] = [n for n in project['graph']['nodes'] if n['id'] != 'nutrient_source']
    project['species']['nutrient']['initial_concentration']['value'] = 1.
    for node in project['graph']['nodes']:
        if node['id'] == 'nutrient_field': node['parameters']['diffusivity_um2_s']['value'] = 0.
        if node['id'] == 'motility': node['parameters']['speed_um_s']['value'] = .01
    (directory/'project.json').write_text(json.dumps(project,indent=2))
    started = time.monotonic()
    with TaskService(directory/'tasks') as service:
        submission = {'task_contract_version':'0.6.0','request_id':'12h','edit_revision':'1',
            'project':project,'version_lock':service.version_lock(project),
            'execution':{'semantics':project['execution_profile'],'backend':'numpy-cpu','seed':17,'dt_s':1.,'steps':43200},
            'output_plan':{'frame_every_steps':3600,'checkpoint_every_steps':3600,'observables':list(project['run']['channels']),'include_fields':True}}
        (directory/'submission.json').write_text(json.dumps(submission,indent=2))
        task,_ = service.submit(submission,'initial')
        service.pause(task['run_id'])
        resumed = False
        while True:
            task = service.get(task['run_id'])
            report = {'task':task,'elapsed_s':time.monotonic()-started,'resumed':resumed,
                'scope':'43200 x 1 s foundational numerical case; no PTS-A 4320000-step or biological calibration claim'}
            (directory/'progress.json').write_text(json.dumps(report,indent=2))
            if task['status'] == 'paused' and not resumed:
                task,_ = service.resume(task['run_id'],{'request_id':'12h-resumed','edit_revision':'1'},'resumed')
                resumed = True
            elif task['status'] in ('completed','failed','cancelled','interrupted'):
                break
            time.sleep(.5)
        if task['status'] != 'completed':
            raise RuntimeError(task['issues'])
        path,_ = service.artifact(task['run_id'],'checkpoint')
        simulation = load_task_checkpoint(path,maximum=service.limits.estimated_memory_bytes)
        assert simulation.frame_index == 43200 and simulation.time_s == 43200
        assert all(np.isfinite(v).all() and (np.asarray(v)>=0).all() for v in simulation.fields.concentrations_uM.values())
        report.update(verified=True, finite_nonnegative=True, end_time_s=simulation.time_s,
                      manifest=service.manifest(task['run_id']))
        (directory/'verification.json').write_text(json.dumps(report,indent=2))
        print(json.dumps({'verified':True,'steps':simulation.frame_index,'time_s':simulation.time_s,'elapsed_s':time.monotonic()-started}))


if __name__ == '__main__':
    main(sys.argv[1])
