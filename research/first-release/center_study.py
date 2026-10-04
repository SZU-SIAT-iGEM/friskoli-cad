"""Matched run-level center-substrate experiments; no success-direction gate."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np

from friskoli_cad.engine.presets import build_center_project
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import canonical_bytes


def case(mechanism, scale, feedback, seed, duration, dt, spacing, out):
    project = build_center_project(mechanism, scale, feedback=feedback, seed=seed, spacing_um=spacing)
    label = f'{mechanism}-{scale}-{int(feedback)}-{seed}-dt{dt:g}-dx{spacing:g}'
    sim = simulation_from_project(project)
    series = []
    start = time.perf_counter()
    steps = round(duration/dt)
    if not math.isclose(steps*dt,duration): raise ValueError('duration must divide dt')
    for i in range(steps):
        sim.step(dt)
        if (i+1)%max(1,round(1/dt))==0:
            series.append({'time_s':sim.time_s,**sim.current.metrics['by_group']['cells']})
    data = {'kind':'direct-engine-study','mechanism':mechanism,'scale':scale,'feedback':feedback,
        'seed':seed,'dt_s':dt,'spacing_um':spacing,'duration_s':duration,'project':project,
        'project_sha256':hashlib.sha256(canonical_bytes(project)).hexdigest(),
        'wall_s':time.perf_counter()-start,'series':series,'metrics':sim.current.metrics['by_group']['cells'],
        'ledger':sim.ledger,'final_frame':sim.current.cell_frame,
        'field_range_um':{s:[float(np.min(v)),float(np.max(v))] for s,v in sim.fields.concentrations_uM.items()}}
    (out/(label+'.json')).write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    print(label,round(data['wall_s'],2),data['metrics']['degradation_per_initial_cell_molecules'],flush=True)
    return data


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seeds',type=int,default=4)
    parser.add_argument('--duration',type=float,default=120.)
    parser.add_argument('--dt',type=float,default=.1)
    parser.add_argument('--spacing',type=float,default=4.)
    parser.add_argument('--scale',choices=('small','medium'))
    parser.add_argument('--mechanism',choices=('a','b'))
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for scale in (args.scale,) if args.scale else ('small','medium'):
        for mechanism in (args.mechanism,) if args.mechanism else ('a','b'):
            for seed in range(1,args.seeds+1):
                for feedback in (False,True):
                    rows.append(case(mechanism,scale,feedback,seed,args.duration,args.dt,args.spacing,args.out))
    with (args.out/'runs.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['mechanism','scale','feedback','seed','dt_s','spacing_um','duration_s','wall_s',
            'project_sha256','region_fraction','ever_arrived_fraction','mean_residence_s',
            'cumulative_degradation_molecules','degradation_per_initial_cell_molecules']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows:writer.writerow({key:row.get(key,row['metrics'].get(key)) for key in fields})


if __name__=='__main__': main()
