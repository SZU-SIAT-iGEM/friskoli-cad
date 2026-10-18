"""Reproduce paired PTS contact assays, numerical checks and Wiki figures."""
import argparse
from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import platform
import time

import numpy as np

from friskoli_cad.engine.presets import build_center_project
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import sha256
from friskoli_cad.tasks.metadata import source_hashes

METRICS = ('live_count', 'region_fraction', 'ever_arrived_fraction', 'mean_residence_s',
           'contact_fraction', 'contact_cell_seconds', 'cumulative_degradation_molecules',
           'degradation_per_initial_cell_molecules', 'mean_displacement_um')


def parameter_rows(path):
    if path is None:
        return []
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ['node_id', 'parameter', 'value', 'unit', 'source', 'condition']:
            raise ValueError('Parameter CSV needs node_id,parameter,value,unit,source,condition in that order')
        rows, seen = [], set()
        for row in reader:
            key = row['node_id'], row['parameter']
            if key in seen or not row['source'].strip() or not row['condition'].strip():
                raise ValueError('Parameter rows need unique targets, a source and measurement conditions')
            seen.add(key)
            row['value'] = float(row['value'])
            if not math.isfinite(row['value']):
                raise ValueError('Parameter values must be finite')
            rows.append(row)
    return rows


def apply_parameters(project, rows):
    nodes = {node['id']: node for node in project['graph']['nodes']}
    for row in rows:
        target = nodes.get(row['node_id'], {}).get('parameters', {}).get(row['parameter'])
        if target is None or type(target['value']) not in (int, float):
            raise ValueError(f'Unknown numeric parameter: {row["node_id"]}.{row["parameter"]}')
        if row['unit'] != target.get('unit', '1'):
            raise ValueError(f'Unit differs for {row["node_id"]}.{row["parameter"]}')
        target['value'] = int(row['value']) if type(target['value']) is int and row['value'].is_integer() else row['value']
        target['provenance'] = {'kind': 'user', 'reference': f'{row["source"]}; conditions: {row["condition"]}'}


def configurations(mechanisms, seeds, dt, spacing):
    cases = []
    for mechanism in mechanisms:
        for seed in seeds:
            for feedback in (False, True):
                cases.append(dict(scenario='paired', mechanism=mechanism, seed=seed,
                                  feedback=feedback, dt_s=dt, spacing_um=spacing, kcat_factor=1.))
    for changes in ({'dt_s': dt / 2}, {'spacing_um': spacing / 2}, {'kcat_factor': .5}, {'kcat_factor': 2.}):
        cases.append(dict(scenario='sensitivity', mechanism=mechanisms[0], seed=seeds[0],
                          feedback=True, **{**{'dt_s': dt, 'spacing_um': spacing, 'kcat_factor': 1.}, **changes}))
    return cases


def run_case(case, duration, rows):
    steps = round(duration / case['dt_s'])
    if steps < 1 or not math.isclose(steps * case['dt_s'], duration, rel_tol=0, abs_tol=1e-10):
        raise ValueError('Duration must be a positive whole number of every selected time step')
    project = build_center_project(case['mechanism'], 'assay', feedback=case['feedback'],
                                   seed=case['seed'], spacing_um=case['spacing_um'], release='strong')
    apply_parameters(project, rows)
    if case['kcat_factor'] != 1.:
        entry = next(node for node in project['graph']['nodes'] if node['id'] == 'contact_hydrolysis')['parameters']['kcat_s']
        entry['value'] *= case['kcat_factor']
        entry['provenance'] = {'kind': 'user', 'reference': f'PTS assay sensitivity factor {case["kcat_factor"]}; base: {entry["provenance"]["reference"]}'}
    simulation = simulation_from_project(project)
    series = [{'time_s': 0., **simulation.current.metrics['by_group']['cells']}]
    start = time.perf_counter()
    for _ in range(steps):
        simulation.step(case['dt_s'])
        series.append({'time_s': simulation.time_s, **simulation.current.metrics['by_group']['cells']})
    settings = {**case, 'duration_s': duration, 'steps': steps}
    return {'configuration': settings, 'configuration_sha256': sha256({'project': project, 'settings': settings}),
            'project': project, 'project_sha256': sha256(project), 'wall_s': time.perf_counter() - start,
            'series': series, 'metrics': simulation.current.metrics['by_group']['cells'], 'ledger': simulation.ledger}


def write_csv(path, fields, rows):
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def figures(records, destination):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    colors = {False: '#537781', True: '#398454'}
    with plt.rc_context({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False}):
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.5), layout='constrained')
        for mechanism in sorted({r['configuration']['mechanism'] for r in records}):
            for feedback in (False, True):
                group = [r for r in records if r['configuration']['scenario'] == 'paired'
                         and r['configuration']['mechanism'] == mechanism and r['configuration']['feedback'] == feedback]
                for axis, metric, label in zip(axes, ('contact_cell_seconds', 'cumulative_degradation_molecules'),
                                               ('Cohort contact integral (cell·s)', 'Converted equivalents (molecules)')):
                    values = np.array([[point[metric] for point in row['series']] for row in group], dtype=float)
                    mean = values.mean(axis=0)
                    sd = values.std(axis=0, ddof=1) if len(group) > 1 else np.zeros_like(mean)
                    times = [point['time_s'] for point in group[0]['series']]
                    axis.plot(times, mean, color=colors[feedback], linestyle='-' if mechanism == 'a' else '--',
                              label=f'PTS {mechanism.upper()}, '+('feedback' if feedback else 'no feedback'))
                    if len(group) > 1:
                        axis.fill_between(times, mean-sd, mean+sd, color=colors[feedback], alpha=.1)
                    axis.set(xlabel='Simulated time (s)', ylabel=label)
        axes[0].legend(fontsize=7)
        fig.suptitle('Constructed PTS contact assay · run means and sample SD')
        for suffix in ('svg', 'png'):
            fig.savefig(destination / f'contact-and-conversion.{suffix}', dpi=180)
        plt.close(fig)
        reference = next(r for r in records if r['configuration']['scenario'] == 'paired' and r['configuration']['feedback'])
        checks = [reference] + [r for r in records if r['configuration']['scenario'] == 'sensitivity']
        labels = ['Reference'] + [f'dt={r["configuration"]["dt_s"]:g}\ndx={r["configuration"]["spacing_um"]:g}\nkcat×{r["configuration"]["kcat_factor"]:g}' for r in checks[1:]]
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), layout='constrained')
        for axis, metric, label in zip(axes, ('contact_cell_seconds', 'cumulative_degradation_molecules'),
                                       ('Contact integral (cell·s)', 'Converted equivalents (molecules)')):
            axis.bar(labels, [r['metrics'][metric] for r in checks], color='#398454')
            axis.set(ylabel=label)
        fig.suptitle('Numerical and parameter checks · same seed, same physical duration')
        for suffix in ('svg', 'png'):
            fig.savefig(destination / f'sensitivity.{suffix}', dpi=180)
        plt.close(fig)


def run_study(destination, *, duration=.4, dt=.02, spacing=4., seeds=(1, 2), mechanisms=('a', 'b'), parameters=None, plot=True):
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(dt) or dt <= 0:
        raise ValueError('Duration and time step must be positive and finite')
    if not seeds or len(set(seeds)) != len(seeds) or any(type(seed) is not int or seed < 0 for seed in seeds):
        raise ValueError('Use distinct nonnegative integer seeds')
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Choose an empty output directory to preserve previous studies')
    destination.mkdir(parents=True, exist_ok=True)
    rows = parameter_rows(parameters)
    environment = {'python': platform.python_version(), 'numpy': np.__version__, 'platform': platform.platform(),
                   'source_sha256': source_hashes(), 'parameter_inputs': deepcopy(rows)}
    records = []
    for index, case in enumerate(configurations(mechanisms, seeds, dt, spacing)):
        record = run_case(case, duration, rows)
        record['environment'] = environment
        name = f'{index+1:03d}-{record["configuration_sha256"][:12]}.json'
        (destination / name).write_text(json.dumps(record, allow_nan=False, indent=1) + '\n', encoding='utf-8')
        records.append(record)
        print(f'{name}: contact={record["metrics"]["contact_cell_seconds"]:g}, converted={record["metrics"]["cumulative_degradation_molecules"]:g}', flush=True)
    fields = ['scenario', 'mechanism', 'seed', 'feedback', 'dt_s', 'spacing_um', 'kcat_factor', 'duration_s', 'steps',
              'project_sha256', 'configuration_sha256', *METRICS]
    write_csv(destination / 'runs.csv', fields, [{**r['configuration'], **{k: r[k] for k in ('project_sha256', 'configuration_sha256')},
                                               **{k: r['metrics'][k] for k in METRICS}} for r in records])
    paired = []
    for mechanism in mechanisms:
        for seed in seeds:
            group = {r['configuration']['feedback']: r for r in records if r['configuration']['scenario'] == 'paired'
                     and r['configuration']['mechanism'] == mechanism and r['configuration']['seed'] == seed}
            paired.append({'mechanism': mechanism, 'seed': seed,
                           **{key: group[True]['metrics'][key] - group[False]['metrics'][key]
                              if group[True]['metrics'][key] is not None and group[False]['metrics'][key] is not None else None for key in METRICS}})
    write_csv(destination / 'paired-effects.csv', ['mechanism', 'seed', *METRICS], paired)
    manifest = {'kind': 'constructed-pts-contact-assay', 'duration_s': duration, 'environment': environment,
                'runs': [r['configuration_sha256'] for r in records], 'paired_effect_definition': 'feedback minus matched no-feedback control',
                'scope': 'Short contact/release and sensitivity checks. Evaluate longer durations and calibrated inputs for a biological study.'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if plot:
        figures(records, destination)
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--duration', type=float, default=.4)
    parser.add_argument('--dt', type=float, default=.02)
    parser.add_argument('--spacing', type=float, default=4.)
    parser.add_argument('--seeds', type=int, nargs='+', default=[1, 2])
    parser.add_argument('--mechanisms', choices=('a', 'b'), nargs='+', default=['a', 'b'])
    parser.add_argument('--parameters', type=Path)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    run_study(args.out, duration=args.duration, dt=args.dt, spacing=args.spacing, seeds=tuple(args.seeds),
              mechanisms=tuple(args.mechanisms), parameters=args.parameters, plot=not args.no_plots)
