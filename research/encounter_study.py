"""Encounter study: can PTS chemotaxis bring cells to the material more often?

The Wiki 2 s scenarios start cells one run away from the material, so first
contact is decided by the initial heading before the PTS sensor (EI time 1 s,
adaptation ~17 s) can act. This study separates the three processes the core
hypothesis rests on:

* encounter: first-contact times and contact entries of cells that start
  several run lengths away;
* retention: dwell time of each contact episode;
* function: cumulative contact time and converted equivalents.

The material is held at a steady surface concentration by a first-order
exchange shell, the far boundary is the same open sink as the presets, and the
field starts at the discrete steady state of that geometry, so cells meet a
maintained gradient rather than a decaying initial pulse. The only difference
between a matched pair is the motor-to-motion edge (PTS feedback vs a fixed
baseline bias), as in ``wiki_spatial.py``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import sys
import time
from pathlib import Path

import numpy as np

from friskoli_cad.engine.presets import (ENCOUNTER, build_encounter_project, encounter_geometry,
                                         encounter_steady_field)
from friskoli_cad.project import simulation_from_project
from friskoli_cad.tasks.metadata import source_hashes


def _plain(value):
    """JSON fallback for numpy scalars and arrays."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f'{type(value).__name__} is not JSON serializable')


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=_plain).encode()).hexdigest()


def project_for(*, seed, feedback, mechanism='a', cells=ENCOUNTER['cells'], start_gap_um=ENCOUNTER['start_gap_um'],
                surface_uM=ENCOUNTER['surface_uM'], field=None, min_tumble_scale=1.):
    """The shipped encounter preset, optionally with more cells, another start gap or a tumble screen."""
    project = build_encounter_project(mechanism, feedback=feedback, seed=seed, cells=cells,
                                      start_gap_um=start_gap_um, surface_uM=surface_uM, field=field)
    if min_tumble_scale != 1.:
        node = next(n for n in project['graph']['nodes'] if n['id'] == 'motility')
        entry = node['parameters']['minimum_tumble_rate_s']
        entry['value'] *= min_tumble_scale
        entry['provenance'] = {'kind': 'example', 'reference': f'Assumed screen: minimum tumble rate x{min_tumble_scale}'}
    signal = next(n for n in project['graph']['nodes'] if n['id'] == 'pts_signal')['parameters']
    return project, signal['initial_ei_fraction']['provenance']['reference']


def surface_gap(positions):
    """Distance from each cell centre to the cube surface (0 inside)."""
    _, _, lower, upper = encounter_geometry()
    outside = np.maximum(np.maximum(lower - positions, positions - upper), 0.)
    return np.linalg.norm(outside, axis=1)


def run_case(project, *, duration, dt):
    simulation = simulation_from_project(project)
    ids = list(simulation.observation_state['cohort'])
    n = len(ids)
    steps = int(round(duration / dt))
    in_contact = np.array([bool(simulation.observation_state['cohort'][c]['last_contact']) for c in ids])
    first_contact = np.full(n, np.nan)
    entries = np.zeros(n, int)
    episode_start = np.where(in_contact, 0., np.nan)
    episodes = []
    series = []
    for step in range(1, steps + 1):
        simulation.step(dt)
        t = step * dt
        cohort = simulation.observation_state['cohort']
        hit = np.array([bool(cohort[c]['last_contact']) for c in ids])
        started = hit & ~in_contact
        ended = in_contact & ~hit
        for i in np.flatnonzero(started):
            entries[i] += 1
            episode_start[i] = t
            if math.isnan(first_contact[i]):
                first_contact[i] = t
        for i in np.flatnonzero(ended):
            episodes.append({'cell': int(i), 'start_s': float(episode_start[i]), 'dwell_s': float(t - episode_start[i]), 'complete': True})
            episode_start[i] = np.nan
        in_contact = hit
        if step % max(1, int(round(.5 / dt))) == 0 or step == steps:
            positions = simulation.world.groups['cells'].positions_um
            gap = surface_gap(positions)
            metrics = simulation.current.metrics['by_group']['cells']
            bias = np.asarray(simulation.outputs['pts_signal']['motor_bias'])
            series.append({'time_s': round(t, 9), 'ever_contacted_fraction': float(np.mean(~np.isnan(first_contact))),
                           'contact_fraction': float(np.mean(hit)), 'mean_gap_um': float(gap.mean()),
                           'within_10um_fraction': float(np.mean(gap <= 10.)),
                           'contact_s_per_cell': metrics['contact_cell_seconds'] / n,
                           'converted_per_cell': metrics['cumulative_degradation_molecules'] / n,
                           'entries_per_cell': float(entries.mean()), 'signal_bias_mean': float(bias.mean()),
                           'signal_bias_in_contact': float(bias[hit].mean()) if hit.any() else None})
    for i in np.flatnonzero(in_contact):
        episodes.append({'cell': int(i), 'start_s': float(episode_start[i]), 'dwell_s': float(duration - episode_start[i]), 'complete': False})
    final = series[-1]
    complete = [e['dwell_s'] for e in episodes if e['complete']]
    contacted = first_contact[~np.isnan(first_contact)]
    summary = {
        'ever_contacted_fraction': final['ever_contacted_fraction'],
        'median_first_contact_s': float(np.median(contacted)) if contacted.size else None,
        'entries_per_cell': float(entries.mean()),
        'entry_rate_per_cell_s': float(entries.mean() / duration),
        'mean_complete_dwell_s': float(np.mean(complete)) if complete else None,
        'complete_episodes': len(complete),
        'contact_s_per_cell': final['contact_s_per_cell'],
        'converted_per_cell': final['converted_per_cell'],
        'final_mean_gap_um': final['mean_gap_um'],
        'final_within_10um_fraction': final['within_10um_fraction'],
    }
    return {'summary': summary, 'series': series, 'episodes': episodes,
            'first_contact_s': [None if math.isnan(v) else float(v) for v in first_contact]}


def write_csv(path, rows):
    rows = list(rows)
    keys = list(dict.fromkeys(k for row in rows for k in row))
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, keys)
        writer.writeheader()
        writer.writerows(rows)


def bootstrap(values, seed=9201, draws=2000):
    values = np.asarray([v for v in values if v is not None], float)
    if values.size < 2:
        return None, None
    rng = np.random.default_rng(seed)
    means = rng.choice(values, size=(draws, values.size)).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def draw(out, runs):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    panels = [('ever_contacted_fraction', 'Ever contacted fraction'), ('entries_per_cell', 'Contact entries per cell'),
              ('contact_s_per_cell', 'Contact time per cell (s)'), ('mean_gap_um', 'Mean distance to surface (µm)'),
              ('within_10um_fraction', 'Fraction within 10 µm'), ('signal_bias_mean', 'Signal CW bias (mean)')]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8.5))
    colours = {True: '#2f7d4f', False: '#56707e'}
    for ax, (key, label) in zip(axes.flat, panels):
        for feedback in (True, False):
            group = [r for r in runs if r['feedback'] == feedback]
            if not group:
                continue
            t = np.array([row['time_s'] for row in group[0]['series']])
            values = np.array([[row[key] for row in r['series']] for r in group], float)
            mean, sd = values.mean(axis=0), values.std(axis=0, ddof=1) if len(group) > 1 else 0.
            ax.plot(t, mean, color=colours[feedback], label='PTS feedback' if feedback else 'Fixed baseline bias')
            ax.fill_between(t, mean - sd, mean + sd, color=colours[feedback], alpha=.18, linewidth=0)
        ax.set_xlabel('Time (s)')
        ax.set_ylabel(label)
        ax.spines[['top', 'right']].set_visible(False)
    axes[0, 0].legend(frameon=False)
    seeds = len({r['seed'] for r in runs})
    cells = runs[0]['cells']
    fig.suptitle(f'Encounter study · maintained surface gradient · mean ± SD over {seeds} seeds × {cells} cells')
    fig.tight_layout()
    for suffix in ('png', 'svg'):
        fig.savefig(out / f'encounter-layers.{suffix}', dpi=150)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=list(range(1, 9)))
    parser.add_argument('--mechanism', choices=('a', 'b'), default='a')
    parser.add_argument('--cells', type=int, default=ENCOUNTER['cells'])
    parser.add_argument('--start-gap', type=float, default=ENCOUNTER['start_gap_um'], help='initial gap to the nearest face, µm')
    parser.add_argument('--surface-uM', type=float, default=ENCOUNTER['surface_uM'])
    parser.add_argument('--duration', type=float, default=60.)
    parser.add_argument('--dt', type=float, default=.05)
    parser.add_argument('--min-tumble-scale', type=float, default=1.)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    field, solve = encounter_steady_field(args.surface_uM)
    runs, started = [], time.time()
    for seed in args.seeds:
        for feedback in (True, False):
            project, ambient_note = project_for(seed=seed, feedback=feedback, mechanism=args.mechanism, cells=args.cells,
                                           start_gap_um=args.start_gap, surface_uM=args.surface_uM, field=field,
                                           min_tumble_scale=args.min_tumble_scale)
            clock = time.time()
            result = run_case(project, duration=args.duration, dt=args.dt)
            record = {'seed': seed, 'feedback': feedback, 'mechanism': args.mechanism, 'cells': args.cells,
                      'project_sha256': _sha(project), 'adaptation': ambient_note, 'wall_s': time.time() - clock, **result}
            # The preset id names the condition, not the seed: one record file per (condition, seed).
            (args.out / f"{project['id']}-s{seed}.json").write_text(json.dumps({**record, 'project': project}, default=_plain),
                                                                    encoding='utf-8')
            runs.append(record)
            print(f"{project['id']} seed {seed}: {json.dumps(result['summary'])} ({record['wall_s']:.0f} s)", flush=True)
    write_csv(args.out / 'runs.csv', ({'seed': r['seed'], 'feedback': r['feedback'], 'mechanism': r['mechanism'],
                                       'project_sha256': r['project_sha256'], **r['summary']} for r in runs))
    paired = []
    by = {(r['seed'], r['feedback']): r['summary'] for r in runs}
    for seed in args.seeds:
        on, off = by[(seed, True)], by[(seed, False)]
        paired.append({'seed': seed, **{k: (None if on[k] is None or off[k] is None else on[k] - off[k]) for k in on}})
    write_csv(args.out / 'paired-effects.csv', paired)
    stats = []
    for key in runs[0]['summary']:
        diffs = [row[key] for row in paired]
        low, high = bootstrap(diffs)
        valid = [d for d in diffs if d is not None]
        stats.append({'metric': key, 'n_pairs': len(valid),
                      'feedback_mean': float(np.mean([by[(s, True)][key] for s in args.seeds if by[(s, True)][key] is not None])) if valid else None,
                      'control_mean': float(np.mean([by[(s, False)][key] for s in args.seeds if by[(s, False)][key] is not None])) if valid else None,
                      'paired_mean_difference': float(np.mean(valid)) if valid else None,
                      'bootstrap_95_low': low, 'bootstrap_95_high': high,
                      'pairs_positive': sum(d > 0 for d in valid), 'pairs_zero': sum(d == 0 for d in valid),
                      'pairs_negative': sum(d < 0 for d in valid)})
    write_csv(args.out / 'paired-statistics.csv', stats)
    manifest = {'kind': 'friskoli-encounter-study', 'arguments': {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                'geometry': ENCOUNTER,
                'steady_field_solve': solve, 'environment': {'python': platform.python_version(), 'numpy': np.__version__,
                'platform': platform.platform()}, 'source_hashes': source_hashes(), 'wall_s': time.time() - started,
                'notes': ['Constructed parameters; only the motor-to-motion edge differs within a pair.',
                          'Contact episodes still open at the end are reported as incomplete and excluded from mean dwell.']}
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=_plain), encoding='utf-8')
    if not args.no_plots:
        draw(args.out, runs)
    print(f'Encounter study written to {args.out}')


if __name__ == '__main__':
    sys.exit(main())
