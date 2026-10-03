"""Bounded, read-only input profiling of a real spatial task outside the service.

Run with PYTHONPATH=src. Output must be a new directory outside the checkout.
The original task, checkpoint, numerical configuration and service are untouched.
Initialization, ordinary steps, instrumented steps and publication are separate.
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import math
from pathlib import Path
import platform
import pstats
import statistics
import time
from contextlib import contextmanager
from functools import wraps

import numpy as np
import psutil

from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.diffusion import explicit_no_flux_limit
from friskoli_cad.engine.field_backend import backend_environment
from friskoli_cad.engine.task_checkpoint import load_task_checkpoint, save_task_checkpoint
from friskoli_cad.protocol.task_validation import canonical_bytes
from friskoli_cad.tasks.metadata import source_hashes
from friskoli_cad.tasks.worker import concentration_preview


def timed(call):
    start = time.perf_counter()
    value = call()
    return value, time.perf_counter() - start


def positive(value):
    result = int(value)
    if not 1 <= result <= 100:
        raise argparse.ArgumentTypeError('Use 1 to 100 steps per phase.')
    return result


@contextmanager
def stage_timers():
    """Time coarse boundaries separately from cProfile; never patch arithmetic."""
    from friskoli_cad.engine import chemotaxis_runtime as chem, local_fields as fields
    from friskoli_cad.engine import field_backend, spatial_runtime
    from friskoli_cad.protocol import FrameSequenceValidator
    targets = [
        (chem.ChemotaxisSimulation, 'step', 'total'),
        (chem.ChemotaxisSimulation, '_prepare', 'prepare'),
        (chem, 'propose_local_field_step', 'field_transaction'),
        (fields, '_mass', 'inventory_reductions'),
        (fields.LocalFieldState, '__post_init__', 'field_state_validation'),
        (fields, 'copy_concentrations', 'writable_field_copy'),
        (fields, '_stored_values', 'immutable_field_storage'),
        (field_backend, 'diffuse_cuda', 'cuda_diffusion_and_transfer'),
        (chem.ChemotaxisSimulation, '_move', 'motion'),
        (chem.ChemotaxisSimulation, '_physiology', 'physiology'),
        (chem.ChemotaxisSimulation, '_validate_arrays', 'output_validation'),
        (chem, '_freeze', 'freeze_outputs'),
        (spatial_runtime.SpatialSimulation, '_snapshot', 'frame_construction'),
        (FrameSequenceValidator, 'accept', 'frame_validation'),
    ]
    stack, result, originals = [], {}, []
    def wrap(original, label):
        @wraps(original)
        def measured(*args, **kwargs):
            frame = [time.perf_counter(), 0.]
            stack.append(frame)
            try:
                return original(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - frame[0]
                stack.pop()
                if stack:
                    stack[-1][1] += elapsed
                row = result.setdefault(label, {'calls': 0, 'inclusive_s': 0., 'exclusive_s': 0.})
                row['calls'] += 1
                row['inclusive_s'] += elapsed
                row['exclusive_s'] += elapsed - frame[1]
        return measured
    try:
        for owner, attribute, label in targets:
            original = getattr(owner, attribute)
            originals.append((owner, attribute, original))
            setattr(owner, attribute, wrap(original, label))
        yield result
    finally:
        for owner, attribute, original in reversed(originals):
            setattr(owner, attribute, original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-input', required=True, type=Path)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--steps', type=positive, default=3)
    parser.add_argument('--stages', action='store_true', help='Add a separately timed coarse-stage phase (chemotaxis only).')
    parser.add_argument('--publication', action='store_true', help='Also time preview and a new checkpoint.')
    args = parser.parse_args()
    checkout = Path(__file__).resolve().parents[2]
    out = args.out.resolve()
    if out.is_relative_to(checkout):
        parser.error('Keep profiles and checkpoint outputs outside the checkout.')
    body = json.loads(args.task_input.read_text(encoding='utf-8-sig'))
    execution = body['execution']
    if execution['semantics'] not in ('chemotaxis-spatial-v1', 'spatial-unbiased-v1', 'modular-spatial-v1'):
        parser.error('This utility requires a spatial execution profile.')
    if args.stages and execution['semantics'] != 'chemotaxis-spatial-v1':
        parser.error('--stages currently measures chemotaxis-spatial-v1.')
    out.mkdir(parents=True, exist_ok=False)
    report = {
        'python': platform.python_version(), 'platform': platform.platform(),
        'numpy': np.__version__, 'execution': execution,
        'output_plan': body['output_plan'],
        'input_sha256': hashlib.sha256(canonical_bytes(body)).hexdigest(),
        'source_sha256': source_hashes(),
        'memory_available_before': psutil.virtual_memory().available,
        'checkpoint_input': str(args.checkpoint) if args.checkpoint else None,
        'note': 'Shared host; short samples. Profiled and unprofiled steps cover consecutive states.',
    }
    def initialize():
        if args.checkpoint:
            return load_task_checkpoint(args.checkpoint, maximum=2 * 1024**3)
        return simulation_from_project(body['project'], seed=execution['seed'], field_backend=execution['backend'])
    sim, report['initialize_s'] = timed(initialize)
    if (canonical_bytes(sim.project) != canonical_bytes(body['project'])
            or sim.seed != execution['seed'] or sim.field_backend != execution['backend']):
        raise ValueError('Checkpoint project, seed and backend must match the submitted task input.')
    report['backend_environment'] = backend_environment(execution['backend'])
    report['start_step'] = sim.frame_index
    report['cells'] = sum(len(g.ids) for g in sim.world.groups.values())
    grid = sim.world.grid
    report['grid'] = {'shape_zyx': list(grid.shape), 'spacing_xyz_um': [grid.dx_um, grid.dy_um, grid.dz_um]}
    if hasattr(sim, 'fields') and hasattr(sim.fields, 'diffusivities_um2_s'):
        report['diffusivities_um2_s'] = dict(sim.fields.diffusivities_um2_s)
        limit = min((explicit_no_flux_limit(grid, d) for d in sim.fields.diffusivities_um2_s.values()), default=math.inf) * .9
        report['diffusion_substeps_per_outer_step'] = max(1, math.ceil(execution['dt_s'] / limit))
    print(json.dumps({'phase': 'initialized', 'seconds': report['initialize_s'], 'step': sim.frame_index}), flush=True)
    _, report['warmup_step_s'] = timed(lambda: sim.step(execution['dt_s']))
    report['unprofiled_step_s'] = []
    for _ in range(args.steps):
        _, elapsed = timed(lambda: sim.step(execution['dt_s']))
        report['unprofiled_step_s'].append(elapsed)
        print(json.dumps({'phase': 'ordinary', 'step': sim.frame_index, 'seconds': elapsed}), flush=True)
    report['unprofiled_median_s'] = statistics.median(report['unprofiled_step_s'])
    if args.stages:
        with stage_timers() as measurements:
            for _ in range(args.steps):
                sim.step(execution['dt_s'])
        report['stage_timings'] = measurements
        print(json.dumps({'phase': 'stages', 'step': sim.frame_index, 'measurements': measurements}), flush=True)
    profiler = cProfile.Profile()
    def profile_steps():
        for _ in range(args.steps):
            sim.step(execution['dt_s'])
    _, report['profiled_steps_total_s'] = timed(lambda: profiler.runcall(profile_steps))
    profiler.dump_stats(str(out / 'steps.prof'))
    stats = pstats.Stats(profiler)
    rows = []
    for (filename, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
        rows.append({'file': filename, 'line': line, 'function': name, 'calls': calls,
                     'primitive_calls': primitive, 'self_s': own, 'cumulative_s': cumulative})
    report['profile'] = sorted(rows, key=lambda row: row['cumulative_s'], reverse=True)
    with (out / 'steps.txt').open('w', encoding='utf-8') as stream:
        stats.stream = stream
        stats.sort_stats('cumulative').print_stats(60)
        stats.sort_stats('tottime').print_stats(40)
    report['end_step'] = sim.frame_index
    report['end_time_s'] = sim.time_s
    report['metrics'] = dict(sim.current.metrics)
    report['fields_sha256'] = {name: hashlib.sha256(memoryview(np.ascontiguousarray(field)).cast('B')).hexdigest()
                               for name, field in sim.current.concentration_fields.items()}
    report['frame_sha256'] = hashlib.sha256(canonical_bytes(sim.current.cell_frame)).hexdigest()
    if args.publication:
        def preview():
            return {name: concentration_preview(field, grid, body['output_plan'].get('field_stride_xyz', [1, 1, 1]), binary=True)
                    for name, field in sim.current.concentration_fields.items()}
        _, report['preview_s'] = timed(preview)
        artifact, report['checkpoint_save_s'] = timed(lambda: save_task_checkpoint(
            sim, out / 'profile-checkpoint.zip', maximum=2 * 1024**3))
        report['checkpoint_artifact'] = artifact
    report['process_memory'] = psutil.Process().memory_info()._asdict()
    report['source_unchanged'] = report['source_sha256'] == source_hashes()
    (out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key in (
        'initialize_s', 'warmup_step_s', 'unprofiled_step_s', 'profiled_steps_total_s',
        'start_step', 'end_step', 'preview_s', 'checkpoint_save_s', 'source_unchanged')}, indent=2), flush=True)
    print('REPORT', out / 'report.json', flush=True)


if __name__ == '__main__':
    main()
