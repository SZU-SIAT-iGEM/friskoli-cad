"""Save completed study data and descriptive run-level paired intervals."""
import argparse
import csv
import json
from pathlib import Path
import shutil
import zipfile

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def write_csv(path, rows):
    with path.open('w',encoding='utf8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def interval(values):
    values=np.asarray(values,float);n=len(values)
    critical={4:3.182446305284,8:2.364624251593}[n]
    mean=float(values.mean());half=critical*float(values.std(ddof=1))/np.sqrt(n)
    return mean,float(mean-half),float(mean+half)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--center',type=Path,required=True)
    parser.add_argument('--chip',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    center=[json.loads(p.read_text(encoding='utf8')) for p in sorted(args.center.glob('*.json'))]
    if len(center)!=32:raise ValueError('Expected 32 completed center cases')
    chip_config=json.loads((args.chip/'configuration.json').read_text(encoding='utf8'))
    if chip_config['observation_axis']!='x':raise ValueError('Expected current chip study along the short X axis')
    extent=float(chip_config['axis_extent_um'])
    if not np.isfinite(extent) or extent<=0:raise ValueError('Invalid chip axis extent')
    shutil.copy2(args.chip/'configuration.json',args.out/'chip-configuration.json')
    for name in ('summary.csv','timeseries.csv','final_positions.csv','figure_main.png','figure_main.svg'):
        shutil.copy2(args.chip/name,args.out/('chip-'+name))
    shutil.copy2(args.center/'runs.csv',args.out/'center-runs.csv')
    with zipfile.ZipFile(args.out/'center-raw.zip','w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(args.center.glob('*.json')):archive.write(path,path.name)
    rows=[]
    for scale in ('small','medium'):
        for mechanism in ('a','b'):
            cases=[r for r in center if r['scale']==scale and r['mechanism']==mechanism]
            for metric in ('region_fraction','ever_arrived_fraction','mean_residence_s','degradation_per_initial_cell_molecules'):
                differences=[next(r for r in cases if r['seed']==seed and r['feedback'])['metrics'][metric]-next(r for r in cases if r['seed']==seed and not r['feedback'])['metrics'][metric] for seed in range(1,5)]
                mean,lower,upper=interval(differences)
                rows.append(dict(mechanism=mechanism,scale=scale,metric=metric,n_runs=4,paired_mean_difference=mean,ci95_lower=lower,ci95_upper=upper,statistical_unit='independent seed pair'))
    write_csv(args.out/'center-paired-effects.csv',rows)
    with (args.chip/'timeseries.csv').open(newline='') as stream:chip=list(csv.DictReader(stream))
    effects=[]
    for time in (120.,300.):
        cases=[r for r in chip if float(r['t_s'])==time]
        for key,scale,metric in (('frac_attractant_side',1.,'region_fraction'),('mean_x_over_length',extent,'mean_position_um')):
            differences=[scale*(float(next(r for r in cases if r['condition']=='gradient' and int(r['seed'])==seed)[key])-float(next(r for r in cases if r['condition']=='zero' and int(r['seed'])==seed)[key])) for seed in range(1,9)]
            mean,lower,upper=interval(differences)
            effects.append(dict(time_s=time,metric=metric,n_runs=8,paired_mean_difference=mean,ci95_lower=lower,ci95_upper=upper,statistical_unit='independent seed pair',observation_axis='x',axis_extent_um=extent))
    write_csv(args.out/'chip-paired-effects.csv',effects)
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    labels=[]
    for i,(scale,mechanism) in enumerate((s,m) for s in ('small','medium') for m in ('a','b')):
        cases=[r for r in center if r['scale']==scale and r['mechanism']==mechanism]
        labels.append(f'{mechanism.upper()} {scale}')
        for seed in range(1,5):
            y=[next(r for r in cases if r['seed']==seed and r['feedback']==feedback)['metrics']['degradation_per_initial_cell_molecules'] for feedback in (False,True)]
            axes[0].plot([i-.14,i+.14],y,color='#939ea4',alpha=.6,lw=.8)
            axes[0].scatter([i-.14,i+.14],y,c=['#687d91','#168672'],s=20)
        row=next(r for r in rows if r['scale']==scale and r['mechanism']==mechanism and r['metric']=='degradation_per_initial_cell_molecules')
        axes[1].errorbar(i,row['paired_mean_difference'],yerr=[[row['paired_mean_difference']-row['ci95_lower']],[row['ci95_upper']-row['paired_mean_difference']]],fmt='o',color='#168672',capsize=4)
    axes[0].set(ylabel='Conversion per initial cell [molecule/cell]',title='120 s: control / feedback, 4 paired seeds')
    axes[1].axhline(0,color='#939ea4',lw=.8)
    axes[1].set(ylabel='Feedback minus control [molecule/cell]',title='Paired mean and descriptive 95% t interval')
    for axis in axes:
        axis.set_xticks(range(4),labels);axis.spines[['top','right']].set_visible(False);axis.grid(axis='y',alpha=.15)
    fig.savefig(args.out/'center-comparison.svg');fig.savefig(args.out/'center-comparison.png',dpi=160);plt.close(fig)
    for path in args.out.glob('*.svg'):
        path.write_text('\n'.join(line.rstrip() for line in path.read_text(encoding='utf8').splitlines())+'\n',encoding='utf8')
    print(json.dumps({'center_cases':len(center),'center_effects':rows,'chip_effects':effects},ensure_ascii=False))


if __name__=='__main__':main()
