"""G scan: how PTS expression level and the membrane limit shape the chemotactic signal.

``g_requested`` scales the transporter copies a cell tries to express. The two
capacity rules cap the functional copies differently (A: footprint on the
membrane, B: reference-area scaling), so A and B only separate once the request
exceeds a capacity. For every G and background concentration the script records
uptake and the transient motor-bias response to a 10% concentration rise, using
the CAD's own PTS signal equations (``wiki_signal.small_step``).

Two couplings between uptake and EI are compared:

* ``fixed`` (the model): EI dephosphorylation per transported molecule is a
  property of the phosphoryl relay, fixed once at the reference strain
  (``presets.REFERENCE_STRAIN``: G = 1, 100 copies). More transporters then mean
  more EI dephosphorylation at the same concentration.
* ``rederived`` (the convention used before 2026-10-17, kept for comparison): the
  constant is re-derived from each cell's functional copies so that EI is half
  dephosphorylated at its own half-saturated uptake. This cancels the copy number
  out of EI(C), so G cannot change sensing.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_signal as ws  # noqa: E402
from friskoli_cad.science import pts, pts_methylation as law  # noqa: E402
from friskoli_cad.tasks.metadata import source_hashes  # noqa: E402

AREA_UM2 = float(pts.capsule_area_um2(2., .8))   # preset capsule: 2 um long, 0.8 um wide
REFERENCE_G = 1.     # reference strain, presets.REFERENCE_STRAIN
PRESET_G = 2.        # engineered strain in the shipped PTS presets
CAPACITY = {
    'a': pts.RebuiltCapacityParameters(100., .5, .4, .001),
    'b': pts.SimplifiedCapacityParameters(100., 3., 5.),
}
ENVIRONMENT_UM = {'start ring (0.15 µM)': .15, 'material surface (1 µM)': 1.}


def copies(mechanism, g):
    rule = pts.rebuilt_capacity if mechanism == 'a' else pts.simplified_capacity
    readout = rule(AREA_UM2, g, CAPACITY[mechanism])
    return float(np.ravel(readout.target_copies)[0]), float(np.ravel(readout.capacity_copies)[0]), \
        float(np.ravel(readout.functional_copies)[0])


def scan(gains, backgrounds, *, duration=20., dt=.05):
    rows = []
    for mechanism in ('a', 'b'):
        for g in gains:
            target, capacity, functional = copies(mechanism, g)
            for coupling in ('fixed', 'rederived'):
                settings, p = ws.model({'functional_copies': functional})   # model coupling: reference strain
                if coupling == 'rederived':
                    p = replace(p, ei_dephos_per_molecule=float(np.ravel(law.ei_dephos_per_molecule(
                        p.ei_rephos_s, functional, settings['turnover_s']))[0]))
                uptake = ws.flux(backgrounds, settings)
                e = ws.equilibrium(backgrounds, settings, p)[1]
                for row, j, ei in zip(ws.small_step(backgrounds, settings, p, duration=duration, dt=dt), uptake, e):
                    rows.append({'mechanism': mechanism, 'coupling': coupling, 'g_requested': float(g),
                                 'target_copies': target, 'capacity_copies': capacity, 'functional_copies': functional,
                                 'capacity_limited': target > capacity, 'ei_dephos_per_molecule': p.ei_dephos_per_molecule,
                                 'uptake_molecules_s': float(j), 'adapted_ei_fraction': float(ei), **row})
    return rows


def summarise(rows, gains, backgrounds):
    out = []
    for mechanism in ('a', 'b'):
        for coupling in ('fixed', 'rederived'):
            for g in gains:
                group = [r for r in rows if r['mechanism'] == mechanism and r['coupling'] == coupling and r['g_requested'] == g]
                gain = np.array([r['transient_gain_per_ln_C'] for r in group])
                best = int(np.argmax(gain))
                entry = {'mechanism': mechanism, 'coupling': coupling, 'g_requested': g,
                         'functional_copies': group[0]['functional_copies'], 'capacity_limited': group[0]['capacity_limited'],
                         'peak_transient_gain': float(gain[best]), 'background_at_peak_uM': group[best]['background_uM']}
                for label, c in ENVIRONMENT_UM.items():
                    entry[f'gain_at_{c:g}_uM'] = float(np.interp(math.log(c), np.log(backgrounds), gain))
                    entry[f'uptake_at_{c:g}_uM'] = float(np.interp(math.log(c), np.log(backgrounds),
                                                                   [r['uptake_molecules_s'] for r in group]))
                out.append(entry)
    return out


def draw(destination, rows, summary, gains, backgrounds):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colours = {'a': '#2f7d4f', 'b': '#b5651d'}
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 4.8))
    ax = axes[0]
    for m in ('a', 'b'):
        s = [r for r in summary if r['mechanism'] == m and r['coupling'] == 'fixed']
        ax.plot(gains, [r['functional_copies'] for r in s], color=colours[m], label=f'PTS {m.upper()} functional copies')
    ax.plot(gains, [copies('a', g)[0] for g in gains], color='0.5', ls=':', label='Requested copies (G × 100)')
    ax.axvline(REFERENCE_G, color='0.75', lw=.8)
    ax.axvline(PRESET_G, color='0.75', lw=.8, ls='--')
    ax.set(xscale='log', yscale='log', xlabel='Requested gain G', ylabel='Transporter copies per cell',
           title='Membrane capacity limits the functional copies')
    ax.legend(frameon=False, fontsize=8)
    ax = axes[1]
    grid = np.array([[r['transient_gain_per_ln_C'] for r in rows
                      if r['mechanism'] == 'a' and r['coupling'] == 'fixed' and r['g_requested'] == g] for g in gains])
    mesh = ax.pcolormesh(backgrounds, gains, grid, shading='auto', cmap='YlGnBu')
    fig.colorbar(mesh, ax=ax, label='Transient Δbias / Δln C')
    for label, c in ENVIRONMENT_UM.items():
        ax.axvline(c, color='white', lw=.8, ls='--')
    ax.axhline(PRESET_G, color='white', lw=.8)
    ax.set(xscale='log', yscale='log', xlabel='Adapted background sugar (µM)', ylabel='Requested gain G',
           title='PTS A, reference-strain coupling')
    ax = axes[2]
    for m in ('a', 'b'):
        for coupling, ls in (('fixed', '-'), ('rederived', ':')):
            s = [r for r in summary if r['mechanism'] == m and r['coupling'] == coupling]
            ax.plot(gains, [r['gain_at_0.15_uM'] for r in s], color=colours[m], ls=ls,
                    label=f'PTS {m.upper()}, ' + ('reference-strain coupling (model)' if coupling == 'fixed'
                                                   else 'per-cell re-derived (previous)'))
    ax.axvline(REFERENCE_G, color='0.75', lw=.8)
    ax.axvline(PRESET_G, color='0.75', lw=.8, ls='--')
    ax.set(xscale='log', xlabel='Requested gain G', ylabel='Transient gain at 0.15 µM background',
           title='Sensing gain at the encounter-study start ring')
    ax.legend(frameon=False, fontsize=8)
    for a in axes:
        a.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    for suffix in ('png', 'svg'):
        fig.savefig(destination / f'G-scan.{suffix}', dpi=150)
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    gains = np.round(np.logspace(math.log10(.5), math.log10(50.), 25), 6)
    backgrounds = np.logspace(-3, 3, 31)
    rows = scan(gains, backgrounds)
    summary = summarise(rows, gains, backgrounds)
    ws.write_csv(args.out / 'G-scan-all.csv', rows)
    ws.write_csv(args.out / 'G-scan-summary.csv', summary)
    thresholds = {m: next((float(g) for g in gains if copies(m, g)[0] > copies(m, g)[1]), None) for m in ('a', 'b')}
    manifest = {'kind': 'friskoli-g-scan', 'area_um2': AREA_UM2, 'reference_g': REFERENCE_G, 'preset_g': PRESET_G,
                'capacity_parameters': {m: vars(p) for m, p in CAPACITY.items()},
                'first_capacity_limited_g': thresholds, 'gains': gains.tolist(), 'backgrounds_uM': backgrounds.tolist(),
                'signal_defaults': ws.DEFAULTS, 'environment': {'python': platform.python_version(), 'numpy': np.__version__},
                'source_hashes': source_hashes(),
                'notes': ['Signal-level study: maintained concentrations, one adapted cell per condition.',
                          'Capacity rules and parameters are those of the center presets.']}
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=float), encoding='utf-8')
    if not args.no_plots:
        draw(args.out, rows, summary, gains, backgrounds)
    print(json.dumps(thresholds))
    print(f'G scan written to {args.out}')


if __name__ == '__main__':
    sys.exit(main())
