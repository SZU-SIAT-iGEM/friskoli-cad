"""Separate startup and gradient direction using unchanged biological rates.

This companion preserves the 28-case study and adds a predeclared diagnostic:
opposite gradients, locally adapted B initial EI/CheY/memory, and longer time.
Only initial state, gradient sign and observation time change; no rate fitting.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path

import numpy as np

import compare_chemotaxis as comparison
from friskoli_cad.engine.pts_runtime import parameters
from friskoli_cad.science import pts


ORIGINAL_MAKE_CASE = comparison.make_case


def diagnostic_project(case, registry):
    project = ORIGINAL_MAKE_CASE(case, registry)
    for node in project['graph']['nodes']:
        if node['module_id'] == 'field.diffusive_local':
            node['parameters']['gradient_x_um_per_um']['value'] = case['gradient_value']
    if case.get('adapted_initial'):
        initial = comparison.simulation_from_project(project, registry)
        flux = initial.outputs['uptake_request']['requested_flux']
        if not np.allclose(flux, flux[0], rtol=0, atol=1e-12):
            raise ValueError('This diagnostic requires uniform initial local influx across its cohort')
        pars = parameters(initial.plan.by_id['pts_signal'], pts.SignalParameters)
        steady = pts.steady_state_signal(flux, pars)
        comparison.set_value(project, 'pts_signal', 'initial_ei_fraction', float(steady.ei_fraction[0]))
        comparison.set_value(project, 'pts_signal', 'initial_chey_p_um', float(steady.chey_p_uM[0]))
        comparison.set_value(project, 'motor_signal', 'initial_memory_um', float(steady.chey_p_uM[0]))
        # Nutrient is finite. Verify the requested first step can be fully
        # supplied; the steady signal must not use an unrealizable flux.
        initial.step(case['dt_s'])
        if not np.allclose(initial.outputs['accepted_uptake']['accepted_flux'], flux, rtol=1e-13, atol=1e-12):
            raise ValueError('Initial requested influx is not fully available')
    return project


def worker(task):
    comparison.make_case = diagnostic_project
    case, seed, duration = task
    return comparison.run_one(case, seed, duration, comparison.chemotaxis_registry())


def cases():
    base = {'model': 'pts-b', 'dt_s': .1, 'dx_um': 10., 'gradient': True}
    result = []
    for initial in ('original', 'adapted'):
        for sign in ('positive', 'negative'):
            result.append(dict(base, id=f'b-{initial}-{sign}', adapted_initial=initial == 'adapted',
                gradient_value=.008 if sign == 'positive' else -.008,
                **({'reference': f'b-{initial}-positive'} if sign == 'negative' else {})))
    result.extend([dict(base, id='b-adapted-zero', gradient_value=0., adapted_initial=True, reference='b-adapted-positive'),
        dict(base, id='b-adapted-frozen', gradient_value=.008, adapted_initial=True, signal_off=True, reference='b-adapted-positive')])
    for sign in ('positive', 'negative', 'zero'):
        result.append({'id': 'mcp-' + sign, 'model': 'mcp', 'dt_s': .1, 'dx_um': 10., 'gradient': True,
            'gradient_value': .008 if sign == 'positive' else -.008 if sign == 'negative' else 0.,
            **({'reference': 'mcp-positive'} if sign != 'positive' else {})})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, default=16)
    parser.add_argument('--duration-s', type=float, default=30.)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.seeds < 2 or not 1 <= args.workers <= 4 or args.duration_s <= 0:
        parser.error('Require >=2 seeds, 1..4 workers and positive duration')
    designs = cases()
    start_hash = comparison.code_digest()
    report = {'study_version': 'n3-transient-diagnostic-v1', 'cases': designs,
        'seeds': list(range(args.seeds)), 'duration_s': args.duration_s,
        'code_sha256_at_start': start_hash,
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'parent_script_sha256': hashlib.sha256(Path(comparison.__file__).read_bytes()).hexdigest(),
        'changes': ['Gradient sign +/- .008 uM/um or zero at fixed mean concentration and total initial inventory.',
            'B adapted initial EI/CheY at fully accepted initial flux, memory equals that CheY.',
            'Original B initial values retained as separate paired cases.',
            'Longer physical observation time; all biological rates, speed, dwell, turn kernel and geometry unchanged.'],
        'limits': ['Opposite-gradient paired contrasts reduce direction-independent startup/boundary effects; they do not eliminate all geometry effects.',
            'B retains its 3 s memory and dwell; MCP retains reduced MWC/linear feedback.',
            'Adapted initialization is a constructed experimental preparation, not a parameter fit.',
            'Finite cohort; report seed SD/SE without interpreting a positive point estimate as proof.'], 'runs': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = [pool.submit(worker, (case, seed, args.duration_s)) for case in designs for seed in report['seeds']]
        for future in as_completed(pending):
            run = future.result()
            report['runs'].append(run)
            args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            print(json.dumps({k: run[k] for k in ('case', 'seed', 'status', 'actual_time_s')}), flush=True)
    order = {case['id']: index for index, case in enumerate(designs)}
    report['runs'].sort(key=lambda r: (order[r['case']], r['seed']))
    report['summary'] = comparison.aggregate(designs, report['runs'])
    report['physical_inventory_checks'] = comparison.physical_inventory_checks(designs, report['runs'])
    contrasts = {'b-original-direction': ('b-original-positive', 'b-original-negative', .5),
        'b-adapted-direction': ('b-adapted-positive', 'b-adapted-negative', .5),
        'b-initialization-positive': ('b-adapted-positive', 'b-original-positive', 1.),
        'b-initialization-negative': ('b-adapted-negative', 'b-original-negative', 1.),
        'mcp-direction': ('mcp-positive', 'mcp-negative', .5)}
    index = {(r['case'], r['seed']): r for r in report['runs'] if r['status'] == 'complete'}
    report['contrasts'] = {}
    for name, (a, b, factor) in contrasts.items():
        common = [s for s in report['seeds'] if (a, s) in index and (b, s) in index]
        report['contrasts'][name] = {'definition': f'{factor} * ({a} - {b})',
            'drift_um_s': comparison.summarize([factor * (index[(a, s)]['drift_um_s'] - index[(b, s)]['drift_um_s']) for s in common])}
    report['code_sha256_at_end'] = comparison.code_digest()
    report['failed_run_count'] = sum(r['status'] != 'complete' for r in report['runs'])
    report['complete'] = (not report['failed_run_count'] and start_hash == report['code_sha256_at_end']
        and all(c['same_initial_inventory'] for c in report['physical_inventory_checks']))
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'complete': report['complete'], 'failed_run_count': report['failed_run_count']}))
    if report['failed_run_count']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
