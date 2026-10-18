"""Dark start: when can contact-driven release alone create a usable signal?

Cells in the contact layout start next to the material with no dissolved sugar.
Sugar appears only where cells touch the material, so the feedback loop
contact -> release -> gradient -> chemotaxis -> contact must start itself. This
extends M04 with the default release (factor 1), stronger releases and a longer
window, and runs each case with PTS feedback and with the fixed-bias control so
that the signal threshold and its consequence for contact are read together.

The probe and gradient criterion are those of ``wiki_spatial.chemical_metrics``
(2-8 um from the surface, C >= 0.01 uM and |grad C|/C >= 0.02 /um in at least
10% of probe voxels). The PTS response window is reported separately as the
probe-mean concentration against the 0.05-5 uM band where the signal study's
median transient gain exceeds a third of its peak.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_spatial import Study, write_csv  # noqa: E402
from friskoli_cad.tasks.metadata import source_hashes  # noqa: E402

RELEASES = (.5, 1., 2., 4., 8., 16.)
WINDOW_UM = (.05, 5.)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3, 4])
    parser.add_argument('--duration', type=float, default=10.)
    parser.add_argument('--dt', type=float, default=.02)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f'{args.out} exists; choose a new output directory')
    study = Study(args.out, duration=args.duration, dt=args.dt)
    rows, series = [], []
    for release in RELEASES:
        for feedback in (True, False):
            for seed in args.seeds:
                result = study.run(seed=seed, feedback=feedback, profile='contact', zero_initial=True,
                                   factors={'release': release})
                m = result['metrics']
                first = next((s['time_s'] for s in result['series'] if s['qualified_probe_fraction'] >= .1), None)
                in_window = sum(args.dt for s in result['series'][1:]
                                if WINDOW_UM[0] <= s['mean_probe_concentration_uM'] <= WINDOW_UM[1])
                rows.append({'release_factor': release, 'feedback': feedback, 'seed': seed, 'case_sha256': result['case_sha256'],
                             'first_gradient_criterion_s': first, 'gradient_maintained_s': m['gradient_maintained_s'],
                             'time_probe_mean_in_window_s': in_window,
                             'final_probe_concentration_uM': m['mean_probe_concentration_uM'],
                             'final_relative_gradient_per_um': m['relative_gradient_per_um'],
                             'contact_s_per_cell': m['contact_s_per_cell'], 'contact_entries': m['contact_entries'],
                             'converted_per_cell': m['converted_per_cell'], 'audit_passed': result['audit_passed']})
                series.extend({'release_factor': release, 'feedback': feedback, 'seed': seed, 'time_s': s['time_s'],
                               'mean_probe_concentration_uM': s['mean_probe_concentration_uM'],
                               'qualified_probe_fraction': s['qualified_probe_fraction'],
                               'contact_s_per_cell': s['contact_s_per_cell']} for s in result['series'])
    write_csv(args.out / 'dark-start-runs.csv', rows)
    write_csv(args.out / 'dark-start-series.csv', series)
    summary = []
    for release in RELEASES:
        on = [r for r in rows if r['release_factor'] == release and r['feedback']]
        off = {r['seed']: r for r in rows if r['release_factor'] == release and not r['feedback']}
        diffs = [r['contact_s_per_cell'] - off[r['seed']]['contact_s_per_cell'] for r in on]
        summary.append({'release_factor': release,
                        'mean_final_probe_uM_feedback': float(np.mean([r['final_probe_concentration_uM'] for r in on])),
                        'mean_gradient_maintained_s_feedback': float(np.mean([r['gradient_maintained_s'] for r in on])),
                        'mean_time_in_window_s_feedback': float(np.mean([r['time_probe_mean_in_window_s'] for r in on])),
                        'contact_s_per_cell_feedback': float(np.mean([r['contact_s_per_cell'] for r in on])),
                        'contact_s_per_cell_control': float(np.mean([r['contact_s_per_cell'] for r in off.values()])),
                        'paired_contact_difference_mean': float(np.mean(diffs)),
                        'pairs_positive': sum(d > 0 for d in diffs), 'pairs_zero': sum(d == 0 for d in diffs),
                        'pairs_negative': sum(d < 0 for d in diffs)})
    write_csv(args.out / 'dark-start-summary.csv', summary)
    (args.out / 'manifest.json').write_text(json.dumps({
        'kind': 'friskoli-dark-start', 'arguments': {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        'releases': RELEASES, 'window_uM': WINDOW_UM, 'environment': {'python': platform.python_version(), 'numpy': np.__version__},
        'source_hashes': source_hashes(), 'notes': ['Contact layout of wiki_spatial.py; release factor scales kcat.',
                                                    'Per-case inputs, series and audits are in cases/.']}, indent=2), encoding='utf-8')
    if not args.no_plots:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.4))
        times = sorted({s['time_s'] for s in series})
        palette = plt.cm.viridis(np.linspace(0, .9, len(RELEASES)))
        for colour, release in zip(palette, RELEASES):
            values = np.array([[s['mean_probe_concentration_uM'] for s in series
                                if s['release_factor'] == release and s['feedback'] and s['seed'] == seed] for seed in args.seeds])
            axes[0].plot(times, values.mean(axis=0), color=colour, label=f'release ×{release:g}')
        axes[0].axhspan(*WINDOW_UM, color='#2f7d4f', alpha=.08, label='PTS response band')
        axes[0].set(yscale='log', xlabel='Time (s)', ylabel='Probe-mean sugar, 2–8 µm from surface (µM)',
                    title='Self-generated signal (feedback runs)')
        axes[0].legend(frameon=False, fontsize=8)
        x = np.arange(len(RELEASES))
        axes[1].bar(x, [s['mean_gradient_maintained_s_feedback'] for s in summary], color='#56707e')
        axes[1].set(xticks=x, xticklabels=[f'×{r:g}' for r in RELEASES], xlabel='Release factor',
                    ylabel=f'Time meeting gradient criterion (s of {args.duration:g})', title='M04 criterion, longer window')
        axes[2].bar(x - .2, [s['contact_s_per_cell_feedback'] for s in summary], .4, color='#2f7d4f', label='PTS feedback')
        axes[2].bar(x + .2, [s['contact_s_per_cell_control'] for s in summary], .4, color='#56707e', label='Fixed bias')
        axes[2].set(xticks=x, xticklabels=[f'×{r:g}' for r in RELEASES], xlabel='Release factor',
                    ylabel='Contact time per cell (s)', title=f'Contact, mean of {len(args.seeds)} seeds')
        axes[2].legend(frameon=False, fontsize=8)
        for a in axes:
            a.spines[['top', 'right']].set_visible(False)
        fig.tight_layout()
        for suffix in ('png', 'svg'):
            fig.savefig(args.out / f'dark-start.{suffix}', dpi=150)
        plt.close(fig)
    print(f'Dark-start study written to {args.out}')


if __name__ == '__main__':
    sys.exit(main())
