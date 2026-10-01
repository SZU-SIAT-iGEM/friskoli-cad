"""Full-graph, fixed-physics chemotaxis controls and dt/grid sensitivity.

Run with PYTHONPATH=src and --output pointing outside the repository. Results
include every requested seed, failed/partial records and paired differences;
no significance or positive drift is an acceptance condition. This is a small
constructed numerical study, not a fitted bacterial-chemotaxis experiment.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

import numpy as np

from friskoli_cad.engine.chemotaxis_modules import chemotaxis_registry
from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.project import simulation_from_project


MODELS = ('pts-a', 'pts-b', 'mcp')
MEASURES = ('drift_um_s', 'mean_displacement_um', 'region_fraction',
            'ever_arrived_fraction', 'mean_residence_s', 'tumble_duty_left',
            'step_chord_speed_um_s', 'blocked_duty_right', 'uptake_molecules')


def node(project, nid):
    return next(n for n in project['graph']['nodes'] if n['id'] == nid)


def set_value(project, nid, key, value):
    node(project, nid)['parameters'][key]['value'] = value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def code_digest():
    root = Path(__file__).resolve().parents[2] / 'src' / 'friskoli_cad'
    paths = sorted(root.glob('engine/*.py')) + sorted(root.glob('science/*.py')) + [root / 'project.py']
    return digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})


def make_case(case, registry):
    project = make_example('chemotaxis-' + case['model'], registry=registry)
    dx = case['dx_um']
    project['domain']['counts_xyz'] = [round(200 / dx), round(100 / dx), 1]
    project['domain']['spacing_um_xyz'] = [dx, dx, 2.]
    # Same physical box across meshes; an accessible target 5 um ahead of the
    # standard initial x=60 cohort, independent of voxel boundaries.
    project['observation'] = {'id': 'x_ge_65', 'label': 'x >= 65 um', 'axis': 0,
        'region_lower_um': [65., 0., 0.], 'region_upper_um': [200., 100., 2.]}
    for n in project['graph']['nodes']:
        if n['module_id'] == 'field.diffusive_local':
            n['parameters']['gradient_x_um_per_um']['value'] = .008 if case['gradient'] else 0.
    if 'mode' in case:
        set_value(project, 'motility', 'tumble_mode', case['mode'])
    if 'speed_um_s' in case:
        set_value(project, 'motility', 'speed_um_s', case['speed_um_s'])
    if case.get('signal_off'):
        # Keep sensing, field and accepted uptake running. Only disconnect the
        # motor input, using the initialized mean bias of this exact case.
        initial = simulation_from_project(project, registry)
        bias = float(np.mean(initial.outputs['motor_signal']['motor_bias']))
        manifest = next(m for m in registry.manifests if m['id'] == 'signal.constant_bias')
        project['graph']['nodes'].append({'id': 'frozen_control', 'module_id': 'signal.constant_bias',
            'module_version': manifest['version'], 'owner': {'kind': 'population', 'id': 'cells'},
            'parameters': {'bias': {'value': bias, 'unit': '1', 'provenance': {
                'kind': 'example', 'reference': 'Constructed control: initialized mean motor bias, sensing and uptake retained'}}}})
        for e in project['graph']['edges']:
            if e['to'] == {'node': 'motility', 'port': 'motor_bias'}:
                e['from'] = {'node': 'frozen_control', 'port': 'motor_bias'}
    return project


def field_inventory(sim):
    return {species: math.fsum(float(v) for v in values) * sim.world.grid.molecules_per_uM_voxel
            for species, values in sim.fields.concentrations_uM.items()}


def run_one(case, seed, duration_s, registry):
    started = time.monotonic()
    result = {'case': case['id'], 'seed': seed, 'status': 'partial', 'requested_time_s': duration_s,
              'completed_steps': 0, 'actual_time_s': 0.}
    sim = None
    try:
        project = make_case(case, registry)
        project['random_seed'] = seed
        result['project_sha256'] = digest(project)
        sim = simulation_from_project(project, registry, seed=seed)
        before = field_inventory(sim)
        source_initial = {s.id: s.remaining_molecules for s in sim.fields.sources}
        material_initial = {mid: m.remaining_molecules for mid, m in sim.materials.items()}
        n = sum(len(g.ids) for g in sim.world.groups.values())
        initial_ids = {cid for g in sim.world.groups.values() for cid in g.ids}
        dt = case['dt_s']
        count = round(duration_s / dt)
        if count <= 0 or not math.isclose(count * dt, duration_s, abs_tol=1e-12):
            raise ValueError('Requested duration must be an integer multiple of dt')
        tumble_time = travel = blocked_time = max_residual = max_residual_bound_ratio = 0.
        for _ in range(count):
            old = {cid: np.asarray(pos) for g in sim.world.groups.values() for cid, pos in zip(g.ids, g.positions_um)}
            tumble_time += sum(w.phase == 'tumble' for w in sim.walks.values()) * dt
            sim.step(dt)
            current_ids = {cid for g in sim.world.groups.values() for cid in g.ids}
            if current_ids != initial_ids:
                raise ValueError('Behavior study requires a fixed cohort; lifecycle belongs to a separate test')
            for group in sim.world.groups.values():
                travel += sum(float(np.linalg.norm(np.asarray(pos) - old[cid])) for cid, pos in zip(group.ids, group.positions_um))
            blocked_time += float(np.sum(sim.outputs['motility']['blocked'])) * dt
            for ledger in sim.ledger.values():
                residual = abs(ledger.conservation_residual_molecules)
                bound = ledger.conservation_bound_molecules
                max_residual = max(max_residual, residual)
                max_residual_bound_ratio = max(max_residual_bound_ratio, residual / bound if bound else (0. if residual == 0 else math.inf))
                if residual > bound:
                    raise ValueError('Per-step finite-field conservation bound exceeded')
            result['completed_steps'] += 1
        if not math.isclose(sim.time_s, duration_s, rel_tol=0, abs_tol=1e-10):
            raise ValueError('Runtime did not reach requested endpoint')
        metrics = sim.current.metrics['by_group']['cells']
        result.update(metrics)
        result.update(status='complete', actual_time_s=sim.time_s,
            drift_um_s=metrics['mean_displacement_um'] / duration_s,
            tumble_duty_left=tumble_time / (n * duration_s),
            step_chord_speed_um_s=travel / (n * duration_s),
            blocked_duty_right=blocked_time / (n * duration_s),
            uptake_molecules=math.fsum(sim.uptake_totals.values()),
            external_supply_molecules=dict(sim.supply_totals),
            initial_field_molecules=before, final_field_molecules=field_inventory(sim),
            initial_source_molecules=source_initial,
            final_source_molecules={s.id: s.remaining_molecules for s in sim.fields.sources},
            initial_material_molecules=material_initial,
            final_material_molecules={mid: m.remaining_molecules for mid, m in sim.materials.items()},
            max_step_residual_molecules=max_residual,
            max_step_residual_bound_ratio=max_residual_bound_ratio,
            final_observation_sha256=digest({'frame': sim.current.cell_frame,
                'fields': {s: list(v) for s, v in sim.fields.concentrations_uM.items()},
                'walks': {cid: w.to_dict() for cid, w in sim.walks.items()}}))
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error))
        if sim is not None:
            result['actual_time_s'] = sim.time_s
    result['wall_seconds'] = time.monotonic() - started
    return result


def summarize(values):
    n = len(values)
    if not n:
        return {'n': 0, 'mean': None, 'sd_between_seeds': None, 'se_between_seeds': None}
    sd = statistics.stdev(values) if n > 1 else None
    return {'n': n, 'mean': statistics.mean(values), 'sd_between_seeds': sd,
            'se_between_seeds': sd / math.sqrt(n) if sd is not None else None,
            'minimum': min(values), 'maximum': max(values)}


def run_worker(task):
    case, seed, duration = task
    return run_one(case, seed, duration, chemotaxis_registry())


def aggregate(cases, runs):
    result = {}
    by_case = {case['id']: {r['seed']: r for r in runs if r['case'] == case['id'] and r['status'] == 'complete'} for case in cases}
    for case in cases:
        rows = by_case[case['id']]
        entry = {'complete_seeds': sorted(rows), 'metrics': {key: summarize([r[key] for r in rows.values()]) for key in MEASURES}}
        if 'reference' in case:
            reference = by_case[case['reference']]
            common = sorted(rows.keys() & reference.keys())
            entry['paired_to'] = case['reference']
            entry['paired_seeds'] = common
            entry['paired_difference'] = {key: summarize([rows[s][key] - reference[s][key] for s in common]) for key in MEASURES}
        result[case['id']] = entry
    return result


def physical_inventory_checks(cases, runs):
    by_case = {case['id']: case for case in cases}
    references, checks = {}, []
    for run in runs:
        if run['status'] != 'complete':
            continue
        model = by_case[run['case']]['model']
        inventories = {key: run[key] for key in ('initial_field_molecules', 'initial_source_molecules', 'initial_material_molecules')}
        if model not in references:
            references[model] = inventories
        reference = references[model]
        matched = all(set(values) == set(reference[key]) and all(math.isclose(value, reference[key][name],
            rel_tol=1e-12, abs_tol=1e-8) for name, value in values.items()) for key, values in inventories.items())
        checks.append({'case': run['case'], 'seed': run['seed'], 'same_initial_inventory': matched})
    return checks


def cases_for(dt, dx, refinement=True):
    cases = []
    for model in MODELS:
        base = {'model': model, 'dt_s': dt, 'dx_um': dx, 'gradient': True}
        cases.append(dict(base, id=model + '-gradient'))
        cases.append(dict(base, id=model + '-zero', gradient=False, reference=model + '-gradient'))
        cases.append(dict(base, id=model + '-signal-off', signal_off=True, reference=model + '-gradient'))
        if refinement:
            for factor in (2, .5):
                cases.append(dict(base, id=f'{model}-dt-{factor:g}', dt_s=dt * factor, reference=model + '-gradient'))
            for factor in (.5, .25):
                cases.append(dict(base, id=f'{model}-dx-{factor:g}', dx_um=dx * factor, reference=model + '-gradient'))
    # Same MCP signal, direction kernel and hazard mapping. Only dwell or run
    # speed changes. Lower chord speed/greater residence alone is not taxis.
    base = {'model': 'mcp', 'dt_s': dt, 'dx_um': dx, 'gradient': True}
    cases.extend([dict(base, id='mcp-dwell', mode='dwell', reference='mcp-gradient'),
                  dict(base, id='mcp-slow', speed_um_s=2.5, reference='mcp-gradient'),
                  dict(base, id='mcp-dwell-off', mode='dwell', signal_off=True, reference='mcp-dwell'),
                  dict(base, id='mcp-slow-off', speed_um_s=2.5, signal_off=True, reference='mcp-slow')])
    # A separate finite-source/material graph checks that fixed physical source
    # support and inventory survive the grid comparison; it is not pooled into
    # the smooth-gradient behavioral statistics.
    for factor in ((1, .5, .25) if refinement else (1,)):
        cases.append({'id': f'materials-dx-{factor:g}', 'model': 'materials', 'dt_s': dt,
            'dx_um': dx * factor, 'gradient': True,
            **({'reference': 'materials-dx-1'} if factor != 1 else {})})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, default=8)
    parser.add_argument('--duration-s', type=float, default=10.)
    parser.add_argument('--dt-s', type=float, default=.1)
    parser.add_argument('--dx-um', type=float, default=10.)
    parser.add_argument('--workers', type=int, default=4, help='Independent process workers, 1 to 4')
    parser.add_argument('--smoke', action='store_true', help='Skip refinements; report is explicitly not an acceptance study')
    args = parser.parse_args()
    if args.seeds < 2 or args.duration_s <= 0 or args.dt_s <= 0 or args.dx_um <= 0 or not 1 <= args.workers <= 4:
        parser.error('At least two seeds and positive finite duration/dt/dx are required')
    registry = chemotaxis_registry()
    cases = cases_for(args.dt_s, args.dx_um, not args.smoke)
    code_before = code_digest()
    report = {'study_version': 'n3-chemotaxis-comparison-v1', 'execution_profile': 'chemotaxis-spatial-v1',
        'parameter_kind': 'constructed template values; no refitting during comparisons',
        'experimental_calibration': 'unknown', 'smoke_only': args.smoke,
        'code_sha256_at_start': code_before, 'catalog_sha256': digest(registry.catalog),
        'seeds': list(range(args.seeds)), 'duration_s': args.duration_s, 'cases': cases, 'runs': [],
        'definitions': {'drift_um_s': 'Signed X displacement of the fixed initial cohort / requested duration.',
            'region_fraction': 'Terminal live center fraction in x >= 65 um, physical closed box.',
            'ever_arrived_fraction': 'Fraction seen in target at a committed step boundary.',
            'mean_residence_s': 'Initial-cohort mean, left-endpoint numerical-step region integral.',
            'tumble_duty_left': 'Left-endpoint tumble indicator integral / total cell-time; dt-sensitive approximation.',
            'step_chord_speed_um_s': 'Sum of committed-step displacement norms / cell-time; underestimates curved/event paths.',
            'blocked_duty_right': 'Fraction of cell-steps flagged blocked, weighted by dt.',
            'paired_difference': 'Case minus stated reference for common complete seeds; SE treats seed, not cell, as replicate.'},
        'limits': ['Small constructed cohort and finite observation time; no positive drift is assumed.',
            'No-flux finite domain and geometric blocking; boundaries may affect residence.',
            'Nearest-voxel sampling changes as mesh changes. dx results are sensitivity, not a claimed convergence order.',
            'Initial linear field evolves by diffusion; gradient is not clamped.',
            'Frozen-motor controls match initialized mean bias, not time-varying mean tumble rate.',
            'MCP has an explicitly supplied nutrient reservoir; its finite ligand remains separate.',
            'Partial/failed runs never enter means. Code changes during a study invalidate acceptance completeness.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tasks = [(case, seed, args.duration_s) for case in cases for seed in report['seeds']]
    # Runs share no simulator, RNG, output file or mutable registry. Only the
    # main process writes the progress report; seed ordering is restored below.
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_worker, task) for task in tasks]
        for future in as_completed(futures):
            run = future.result()
            report['runs'].append(run)
            args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            print(json.dumps({key: run[key] for key in ('case', 'seed', 'status', 'actual_time_s')}, ensure_ascii=False), flush=True)
    order = {case['id']: i for i, case in enumerate(cases)}
    report['runs'].sort(key=lambda run: (order[run['case']], run['seed']))
    report['summary'] = aggregate(cases, report['runs'])
    report['physical_inventory_checks'] = physical_inventory_checks(cases, report['runs'])
    report['code_sha256_at_end'] = code_digest()
    report['complete'] = (not args.smoke and report['code_sha256_at_end'] == code_before
                          and all(r['status'] == 'complete' for r in report['runs'])
                          and all(c['same_initial_inventory'] for c in report['physical_inventory_checks']))
    report['failed_run_count'] = sum(r['status'] != 'complete' for r in report['runs'])
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'complete': report['complete'], 'failed_run_count': report['failed_run_count'], 'output': str(args.output)}, ensure_ascii=False))
    if report['failed_run_count']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
