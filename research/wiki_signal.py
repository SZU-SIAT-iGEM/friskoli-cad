"""Generate Wiki A/B/C data using the CAD's PTS signal equations."""
import argparse
from dataclasses import asdict, fields, replace
import csv
import hashlib
import json
import math
from pathlib import Path
import platform

import numpy as np

from friskoli_cad.engine.presets import SIGNAL_CONSTANTS, pre_adapted_signal_state
from friskoli_cad.science import pts, pts_methylation as law, chemotaxis
from friskoli_cad.tasks.metadata import source_hashes


# These intervals are proposed uncertainty ranges, not fitted confidence bounds.
RANGES = {'tau_methylation_s': (2., 8.), 'tau_ei_s': (.5, 2.),
          'tau_chey_s': (.05, .2), 'half_saturation_uM': (.3, 3.),
          'ei_dephos_factor': (.5, 2.)}
DEFAULTS = {'functional_copies': 200., 'turnover_s': 2., 'half_saturation_uM': 1.,
            'reference_concentration_uM': .1, 'tau_methylation_s': 4.,
            'tau_ei_s': 1., 'tau_chey_s': .1, 'ei_dephos_factor': 1.}
CL_SOURCE = 'https://cluzel.fas.harvard.edu/sites/g/files/omnuum7156/files/cluzel/files/cluzel_science_00.pdf'


def model(overrides=None):
    settings = {**DEFAULTS, **(overrides or {})}
    if set(settings) != set(DEFAULTS) or any(not math.isfinite(v) or v <= 0 for v in settings.values()):
        raise ValueError('Use positive finite values for the declared signal settings')
    constants = {**SIGNAL_CONSTANTS, 'tau_methylation_s': settings['tau_methylation_s'],
                 'tau_chey_s': settings['tau_chey_s'], 'ei_rephos_s': 1. / settings['tau_ei_s']}
    derived = pre_adapted_signal_state(constants, settings['reference_concentration_uM'],
                                      settings['functional_copies'], settings['turnover_s'],
                                      settings['half_saturation_uM'])
    p = law.PTSMethylationParameters(**{f.name: derived[f.name.lower()][1] for f in fields(law.PTSMethylationParameters)})
    p = replace(p, ei_dephos_per_molecule=p.ei_dephos_per_molecule * settings['ei_dephos_factor'])
    return settings, p


def flux(concentration, settings):
    return pts.pts_request(np.asarray(concentration, dtype=float), settings['functional_copies'],
                           settings['turnover_s'], settings['half_saturation_uM'])


def equilibrium(concentration, settings, p, *, fixed_methylation=None):
    j = flux(concentration, settings)
    on = j * p.ei_dephos_per_molecule
    e = on / (on + p.ei_rephos_s)
    m = law.adapted_methylation(e, p) if fixed_methylation is None else np.full_like(e, fixed_methylation)
    a = law.complex_activity(e, m, p)
    y_on = a * p.chea_total_uM * p.chey_phos_per_uM_s
    y = p.chey_total_uM * y_on / (y_on + p.chey_dephos_s)
    b = chemotaxis.motor_bias(y, half_uM=p.motor_half_uM, hill=p.motor_hill)
    return j, e, m, a, y, b


def static_response(concentrations, settings, p):
    concentrations = np.asarray(concentrations, dtype=float)
    if concentrations.ndim != 1 or len(concentrations) < 3 or np.any(concentrations <= 0) or np.any(np.diff(concentrations) <= 0):
        raise ValueError('Use at least three increasing positive concentrations')
    anchor = float(equilibrium(np.array([settings['reference_concentration_uM']]), settings, p)[2][0])
    rows = []
    for adaptation in (True, False):
        par = p if adaptation else replace(p, adaptation_rate_s=0.)
        j, e, m, a, y, b = equilibrium(concentrations, settings, par,
                                       fixed_methylation=None if adaptation else anchor)
        advanced = law.advance(j, e, m, y, .1, par)
        scales = (1., p.methylation_max - p.methylation_min, 1., p.chey_total_uM, 1.)
        residual = np.maximum.reduce([np.abs(x - target) / scale for x, target, scale in zip(advanced, (e, m, a, y, b), scales)])
        slope = np.gradient(b, np.log(concentrations), edge_order=2)
        for i, c in enumerate(concentrations):
            rows.append({'adaptation': adaptation, 'concentration_uM': float(c), 'uptake_molecules_s': float(j[i]),
                         'ei_dephosphorylated_fraction': float(e[i]), 'methylation': float(m[i]),
                         'chea_activity': float(a[i]), 'chey_p_uM': float(y[i]), 'motor_bias': float(b[i]),
                         'd_bias_d_ln_C': float(slope[i]), 'equilibrium_step_residual': float(residual[i])})
    return rows


def steps_for(duration, dt):
    if not math.isfinite(duration) or not math.isfinite(dt) or duration <= 0 or dt <= 0:
        raise ValueError('Duration and time step must be positive and finite')
    count = round(duration / dt)
    if count < 1 or not math.isclose(count * dt, duration, abs_tol=1e-10, rel_tol=0):
        raise ValueError('Each duration must be a whole number of time steps')
    return count


def step_response(settings, p, *, dt=.05, onset=5., offset=35., duration=65., fold=10.):
    count = steps_for(duration, dt)
    if not 0 < onset < offset < duration or not math.isfinite(fold) or fold <= 1:
        raise ValueError('Use ordered interior step times and a fold increase greater than one')
    on_step, off_step = steps_for(onset, dt), steps_for(offset, dt)
    low = settings['reference_concentration_uM']
    rows = []
    for adaptation in (True, False):
        par = p if adaptation else replace(p, adaptation_rate_s=0.)
        _, e, m, _, y, _ = equilibrium(np.array([low]), settings, par)
        for i in range(count + 1):
            c = low * (fold if on_step <= i < off_step else 1.)
            a = law.complex_activity(e, m, par)
            b = chemotaxis.motor_bias(y, half_uM=par.motor_half_uM, hill=par.motor_hill)
            rows.append({'adaptation': adaptation, 'time_s': i * dt, 'concentration_uM': c,
                         'uptake_molecules_s': float(flux(np.array([c]), settings)[0]),
                         'ei_dephosphorylated_fraction': float(e[0]), 'methylation': float(m[0]),
                         'chea_activity': float(a[0]), 'chey_p_uM': float(y[0]), 'motor_bias': float(b[0])})
            if i < count:
                e, m, _, y, _ = law.advance(flux(np.array([c]), settings), e, m, y, dt, par)
    return rows


def response_summary(rows, *, onset=5., offset=35., duration=65., recovery_fraction=.05):
    result = []
    for adaptation in (True, False):
        group = [r for r in rows if r['adaptation'] == adaptation]
        for phase, start, end in [('up', onset, offset), ('down', offset, duration)]:
            window = [r for r in group if start - 1e-10 <= r['time_s'] <= end + 1e-10]
            baseline = window[0]['motor_bias']
            difference = np.array([r['motor_bias'] - baseline for r in window])
            index = int(np.argmax(np.abs(difference)))
            peak = float(difference[index])
            threshold = recovery_fraction * abs(peak)
            reached = np.flatnonzero(np.abs(difference) >= .5 * abs(peak)) if abs(peak) > 1e-12 else []
            returned = [i for i in range(index, len(window)) if np.all(np.abs(difference[i:]) <= threshold)] if abs(peak) > 1e-12 else []
            result.append({'adaptation': adaptation, 'phase': phase, 'baseline_motor_bias': baseline,
                           'signed_peak_bias_change': peak, 'peak_time_after_step_s': window[index]['time_s'] - start,
                           'half_peak_response_time_s': window[int(reached[0])]['time_s'] - start if len(reached) else None,
                           'adaptation_time_5pct_s': window[returned[0]]['time_s'] - start if returned else None,
                           'remaining_response_fraction': abs(float(difference[-1]) / peak) if abs(peak) > 1e-12 else None,
                           'recovery_fraction': 1. - abs(float(difference[-1]) / peak) if abs(peak) > 1e-12 else None})
    return result


def latin_hypercube(count, seed, ranges):
    if type(count) is not int or count < 2 or type(seed) is not int or seed < 0:
        raise ValueError('Use at least two parameter samples and a nonnegative integer seed')
    rng = np.random.default_rng(seed)
    if not ranges:
        raise ValueError('Declare at least one uncertainty range')
    result = [{} for _ in range(count)]
    for name, bounds in ranges.items():
        low, high = bounds
        if name not in RANGES or not 0 < low < high or not all(math.isfinite(v) for v in bounds):
            raise ValueError('Uncertainty ranges need declared names and ordered positive finite bounds')
        values = np.exp(np.log(low) + (rng.permutation(count) + rng.random(count)) / count * np.log(high / low))
        for row, value in zip(result, values):
            row[name] = float(value)
    return result


def small_step(concentrations, settings, p, *, relative_change=.1, duration=20., dt=.05):
    if not math.isfinite(relative_change) or relative_change <= 0:
        raise ValueError('Relative change must be positive and finite')
    count = steps_for(duration, dt)
    concentrations = np.asarray(concentrations, dtype=float)
    if concentrations.ndim != 1 or not np.isfinite(concentrations).all() or np.any(concentrations <= 0):
        raise ValueError('Background concentrations must be positive finite values')
    _, e, m, _, y, baseline = equilibrium(concentrations, settings, p)
    peak = np.zeros_like(baseline)
    peak_time = np.zeros_like(baseline)
    j = flux(concentrations * (1 + relative_change), settings)
    for i in range(1, count + 1):
        e, m, _, y, bias = law.advance(j, e, m, y, dt, p)
        response = baseline - bias
        improved = response > peak
        peak_time[improved] = i * dt
        peak = np.maximum(peak, response)
    return [{'background_uM': float(c), 'relative_change': relative_change,
             'peak_bias_decrease': float(peak[i]), 'peak_time_s': float(peak_time[i]),
             'transient_gain_per_ln_C': float(peak[i] / math.log1p(relative_change))}
            for i, c in enumerate(concentrations)]


def write_csv(path, rows):
    if not rows:
        raise ValueError('Cannot write an empty result table')
    with Path(path).open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def draw(destination, dose, dynamic, uncertainty):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    colors = {True: '#398454', False: '#537781'}
    dose_axes = [('uptake_molecules_s', 'Uptake (molecules/s)'), ('ei_dephosphorylated_fraction', 'Dephosphorylated EI fraction'),
                 ('chea_activity', 'CheA activity'), ('chey_p_uM', 'CheY-P (µM)'), ('motor_bias', 'Motor CW bias'),
                 ('d_bias_d_ln_C', 'Steady-state d(bias)/d ln C')]
    dynamic_axes = [('concentration_uM', 'Sugar (µM)'), ('ei_dephosphorylated_fraction', 'Dephosphorylated EI fraction'),
                    ('methylation', 'Methylation state'), ('chea_activity', 'CheA activity'), ('chey_p_uM', 'CheY-P (µM)'),
                    ('motor_bias', 'Motor CW bias')]
    with plt.rc_context({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False}):
        for name, rows, xkey, xlabel, panels in [('A-dose-response', dose, 'concentration_uM', 'Sugar (µM)', dose_axes),
                                                ('B-step-response', dynamic, 'time_s', 'Time (s)', dynamic_axes)]:
            fig, axes = plt.subplots(2, 3, figsize=(10, 6), layout='constrained')
            for adaptation in (True, False):
                group = [r for r in rows if r['adaptation'] == adaptation]
                for axis, (key, ylabel) in zip(axes.flat, panels):
                    axis.plot([r[xkey] for r in group], [r[key] for r in group], color=colors[adaptation],
                              linestyle='-' if adaptation else '--', label='Adaptation on' if adaptation else 'Adaptation off')
                    axis.set(xlabel=xlabel, ylabel=ylabel)
                    if xkey == 'concentration_uM':
                        axis.set_xscale('log')
            axes.flat[0].legend(fontsize=8)
            fig.suptitle('A · Maintained-concentration steady states' if name.startswith('A') else 'B · Identical concentration steps, downstream adaptation toggle')
            for suffix in ('png', 'svg'):
                fig.savefig(destination / f'{name}.{suffix}', dpi=180)
            plt.close(fig)
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.8), layout='constrained')
        for axis, metric, label in zip(axes, ('peak_bias_decrease', 'transient_gain_per_ln_C'),
                                       ('Peak bias decrease', 'Transient Δbias / Δln C')):
            axis.fill_between([r['background_uM'] for r in uncertainty], [r[metric + '_q05'] for r in uncertainty],
                              [r[metric + '_q95'] for r in uncertainty], color='#398454', alpha=.22, label='Parameter 5–95% quantiles')
            axis.plot([r['background_uM'] for r in uncertainty], [r[metric + '_q50'] for r in uncertainty],
                      color='#398454', label='Parameter median')
            axis.set(xscale='log', xlabel='Background sugar (µM)', ylabel=label)
        axes[0].legend(fontsize=8)
        fig.suptitle('C · Small relative steps across assumed parameter ranges')
        for suffix in ('png', 'svg'):
            fig.savefig(destination / f'C-window-uncertainty.{suffix}', dpi=180)
        plt.close(fig)


def run_study(destination, *, samples=64, seed=20261016, dt=.05, backgrounds=None, ranges=None, plot=True):
    # Validate the sampling specification before making an output directory.
    samples_table = latin_hypercube(samples, seed, RANGES if ranges is None else ranges)
    steps_for(5., dt)
    steps_for(20., dt)
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Choose an empty output directory to preserve previous results')
    destination.mkdir(parents=True, exist_ok=True)
    settings, p = model()
    dose = static_response(np.geomspace(1e-4, 1e4, 81), settings, p)
    dynamic = step_response(settings, p, dt=dt)
    summaries = response_summary(dynamic)
    refined = response_summary(step_response(settings, p, dt=dt / 2))
    convergence = []
    for coarse, fine in zip(summaries, refined):
        for metric in ('signed_peak_bias_change', 'half_peak_response_time_s', 'adaptation_time_5pct_s'):
            a, b = coarse[metric], fine[metric]
            allowance = max(.001 if metric == 'signed_peak_bias_change' else dt, .05 * abs(b)) if b is not None else None
            convergence.append({'adaptation': coarse['adaptation'], 'phase': coarse['phase'], 'metric': metric,
                                'dt_s': dt, 'refined_dt_s': dt / 2, 'coarse': a, 'refined': b,
                                'absolute_allowance': allowance,
                                'within_threshold': abs(a - b) <= allowance if a is not None and b is not None else None})
    backgrounds = np.geomspace(1e-3, 1e3, 31) if backgrounds is None else np.asarray(backgrounds)
    sampled = []
    for index, overrides in enumerate(samples_table):
        sampled_settings, par = model(overrides)
        sampled.extend({'sample_id': index, **row} for row in small_step(backgrounds, sampled_settings, par, dt=dt))
        if (index + 1) % 8 == 0:
            print(f'C: {index + 1}/{samples} parameter samples completed', flush=True)
    uncertainty = []
    for background in backgrounds:
        group = [r for r in sampled if r['background_uM'] == float(background)]
        row = {'background_uM': float(background), 'n_parameter_samples': samples}
        for metric in ('peak_bias_decrease', 'transient_gain_per_ln_C'):
            for name, value in zip(('q05', 'q50', 'q95'), np.quantile([r[metric] for r in group], [.05, .5, .95])):
                row[f'{metric}_{name}'] = float(value)
        uncertainty.append(row)
    parameter_rows = [{'parameter': key, 'value': value, 'source_kind': 'assumption',
                       'source': 'Constructed maintained-reservoir signal study'} for key, value in settings.items()]
    parameter_rows.extend({'parameter': key, 'value': value,
                           'source_kind': 'literature' if key == 'motor_hill' else 'assumption',
                           'source': CL_SOURCE if key == 'motor_hill' else 'Constructed or derived CAD signal parameter'}
                          for key, value in asdict(p).items())
    for name, rows in [('A-dose-response.csv', dose), ('B-step-response.csv', dynamic), ('B-response-summary.csv', summaries),
                       ('B-time-step-check.csv', convergence),
                       ('C-parameter-samples.csv', [{'sample_id': i, **row} for i, row in enumerate(samples_table)]),
                       ('C-all-responses.csv', sampled), ('C-uncertainty.csv', uncertainty), ('parameters.csv', parameter_rows)]:
        write_csv(destination / name, rows)
    manifest = {'kind': 'wiki-pts-signal-ABC', 'environment': {'python': platform.python_version(), 'numpy': np.__version__},
                'source_hashes': source_hashes(), 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'settings': settings, 'signal_parameters': asdict(p), 'dt_s': dt,
                'A': {'method': 'Solve algebraic steady states; verify with the CAD advance function',
                      'equilibrium_residual_max': max(r['equilibrium_step_residual'] for r in dose)},
                'B': {'step_on_s': 5., 'step_off_s': 35., 'duration_s': 65., 'fold': 10.,
                      'adaptation_definition': 'Return within 5% of peak deviation from the pre-step bias and stay there'},
                'C': {'n_parameter_samples': samples, 'algorithm': 'Latin hypercube, uniform in log parameters',
                      'parameter_seed': seed, 'ranges': RANGES if ranges is None else ranges, 'relative_step': .1,
                      'response_window_s': 20., 'band': 'Parameter-response 5th/50th/95th percentiles; not an experimental CI'},
                'boundary': 'Concentration maintained by an unlimited, well-mixed reservoir; accepted flux equals requested uptake',
                'spatial_geometry': None, 'n_representative_cells': 1, 'n_motility_seeds': 0,
                'control': 'Same signal parameters, initial adapted state and concentration input; only adaptation_rate_s is zeroed',
                'parameter_source_categories': ['literature', 'measured', 'assumption'], 'measured_inputs': [],
                'numerical_tolerance': '5% of refined value, with absolute floors 0.001 bias or one coarse time step'}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if plot:
        draw(destination, dose, dynamic, uncertainty)
    print(f'A/B/C tables written to {destination}', flush=True)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=64)
    parser.add_argument('--seed', type=int, default=20261016)
    parser.add_argument('--dt', type=float, default=.05)
    parser.add_argument('--ranges', type=Path, help='JSON mapping declared parameter names to positive interval pairs')
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    interval_spec = json.loads(args.ranges.read_text(encoding='utf-8')) if args.ranges else None
    run_study(args.out, samples=args.samples, seed=args.seed, dt=args.dt, ranges=interval_spec, plot=not args.no_plots)
