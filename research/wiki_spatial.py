"""Run matched spatial Wiki studies, numerical audits and parameter screens."""
import argparse
from copy import deepcopy
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
from unittest.mock import patch

import numpy as np

from friskoli_cad.engine.core import GridDomain
from friskoli_cad.engine.local_fields import FieldSpecies, make_local_field_state, propose_local_field_step
from friskoli_cad.engine.presets import build_center_project, pre_adapted_signal_state, SIGNAL_CONSTANTS
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import sha256
from friskoli_cad.science import pts_methylation
from friskoli_cad.science.pts import pts_request
from friskoli_cad.tasks.metadata import source_hashes

FACTORS = {'diffusion': (.5, 2.), 'uptake': (.5, 2.), 'release': (.5, 2.),
           'adaptation': (.5, 2.), 'ei_coupling': (.5, 2.)}
ENDPOINTS = ('contact_s_per_cell', 'converted_per_cell', 'arrival_fraction', 'region_fraction',
             'relative_gradient_per_um', 'chey_p_mean_uM')
ABSOLUTE_TOLERANCES = {'contact_s_per_cell': .02, 'converted_per_cell': 100., 'arrival_fraction': .02,
                       'region_fraction': .02, 'relative_gradient_per_um': .002, 'chey_p_mean_uM': .05}


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def gaussian_average(counts, spacing):
    """Exact cell averages of the same Gaussian, with the aligned material removed."""
    axes = []
    for count, dx, center in zip(counts, spacing, (32., 32., 16.)):
        edges = np.arange(count + 1) * dx
        primitive = np.array([math.erf((x - center) / (math.sqrt(2.) * 8.)) for x in edges])
        axes.append(np.diff(primitive) * 8. * math.sqrt(math.pi / 2.) / dx)
    values = axes[2][:, None, None] * axes[1][None, :, None] * axes[0][None, None, :]
    z, y, x = np.indices(values.shape)
    centers = np.stack(((x + .5) * spacing[0], (y + .5) * spacing[1], (z + .5) * spacing[2]), axis=-1)
    solid = np.all((centers >= [24., 24., 8.]) & (centers < [40., 40., 24.]), axis=-1)
    values[solid] = 0.
    return values


def positions(count=8, profile='approach'):
    if count not in (8, 16, 32) or profile not in ('approach', 'contact'):
        raise ValueError('Use 8, 16 or 32 cells and an approach/contact profile')
    offset = 18. if profile == 'approach' else 10.5
    points = []
    sites = [(0., -4.), (0., 4.), (-4., 0.), (4., 0.), (-4., -4.), (4., -4.), (-4., 4.), (4., 4.)]
    for tangent, z in sites:
        for x, y in ((-offset, tangent), (offset, tangent), (tangent, -offset), (tangent, offset)):
            points.append([32. + x, 32. + y, 16. + z])
    return points[:count]


def project_for(*, seed=1, feedback=True, mechanism='a', spacing=4., cells=8,
                profile='approach', zero_initial=False, factors=None):
    factors = factors or {}
    if set(factors) - (set(FACTORS) | {'speed', 'tumble'}):
        raise ValueError('Unknown perturbation')
    if any(not math.isfinite(v) or v < 0 for v in factors.values()):
        raise ValueError('Factors must be finite and nonnegative')
    if spacing not in (1., 2., 4.):
        raise ValueError('Use aligned 1, 2 or 4 µm grids')
    project = build_center_project(mechanism, 'assay', feedback=feedback, seed=seed, spacing_um=spacing, release='strong')
    group = project['groups']['cells']
    group['positions_um'] = positions(cells, profile)
    group['ids'] = [f'cell-{i:04d}' for i in range(cells)]
    group['initial_geometry'] = [deepcopy(group['initial_geometry'][0]) for _ in range(cells)]
    rng = np.random.default_rng(seed)
    orientations = rng.normal(size=(cells, 4))
    group['orientation_xyzw'] = (orientations / np.linalg.norm(orientations, axis=1, keepdims=True)).tolist()
    nodes = {n['id']: n for n in project['graph']['nodes']}
    values = gaussian_average(project['domain']['counts_xyz'], project['domain']['spacing_um_xyz'])
    if zero_initial:
        values *= 0.
    nodes['initial_product']['parameters']['values_um']['value'] = values.tolist()
    ambient = 0. if zero_initial else float(np.exp(-np.sum((np.array(group['positions_um']) - [32., 32., 16.])**2, axis=1) / 128.).mean())
    # Both capacity parameterizations yield 200 copies at the unperturbed gain.
    signal = pre_adapted_signal_state(SIGNAL_CONSTANTS, ambient, 200., 2., 1.)
    for key, (source, value) in signal.items():
        entry = nodes['pts_signal']['parameters'][key]
        entry.update(value=value, provenance={'kind': 'example', 'reference': 'Shared analytic ambient initialization; ' + source})
    targets = {'diffusion': ('sugar_field', 'diffusivity_um2_s'), 'uptake': ('uptake_request', 'turnover_s'),
               'release': ('contact_hydrolysis', 'kcat_s'), 'adaptation': ('pts_signal', 'adaptation_rate_s'),
               'ei_coupling': ('pts_signal', 'ei_dephos_per_molecule'), 'speed': ('motility', 'speed_um_s'),
               'tumble': ('motility', 'maximum_tumble_rate_s')}
    for key, factor in factors.items():
        nid, parameter = targets[key]
        entry = nodes[nid]['parameters'][parameter]
        entry['value'] *= factor
        entry['provenance'] = {'kind': 'example', 'reference': f'Assumed Wiki screen factor {key}={factor}; numerical perturbation'}
        if key == 'tumble':
            nodes['motility']['parameters']['minimum_tumble_rate_s']['value'] *= factor
    signal_parameters = nodes['pts_signal']['parameters']
    uptake_parameters = nodes['uptake_request']['parameters']
    initial_flux = float(pts_request(ambient, 200., uptake_parameters['turnover_s']['value'],
                                    uptake_parameters['half_saturation_um']['value']))
    on = initial_flux * signal_parameters['ei_dephos_per_molecule']['value']
    initial_ei = on / (on + signal_parameters['ei_rephos_s']['value'])
    signal_parameters['initial_ei_fraction']['value'] = initial_ei
    signal_parameters['initial_methylation']['value'] = (
        signal_parameters['methylation_min']['value'] + initial_ei *
        (signal_parameters['methylation_max']['value'] - signal_parameters['methylation_min']['value']))
    return project


def control_signature(project):
    """Hash everything except identity strings and the explicitly toggled motor edge."""
    document = deepcopy(project)
    document.pop('id')
    document['graph'].pop('id')
    document['run'].pop('run_id')
    document['run'].pop('graph_id')
    document['graph']['nodes'] = [n for n in document['graph']['nodes'] if n['id'] != 'constant_motor']
    for edge in document['graph']['edges']:
        edge.pop('id')
        if edge['to'] == {'node': 'motility', 'port': 'motor_bias'}:
            edge['from'] = {'node': 'matched_motor_toggle', 'port': 'motor_bias'}
    return sha256(document)


def chemical_metrics(simulation, *, concentration_floor=.01, gradient_threshold=.02):
    grid = simulation.world.grid
    value = np.asarray(simulation.fields.concentrations_uM['sugar']).reshape(grid.shape)
    blocked = np.asarray(simulation.fields.blocked).reshape(grid.shape)
    gradients = np.gradient(value, grid.dz_um, grid.dy_um, grid.dx_um)
    eligible = ~blocked.copy()
    for axis in range(3):
        eligible &= ~np.roll(blocked, 1, axis) & ~np.roll(blocked, -1, axis)
        for side in (0, -1):
            selection = [slice(None)] * 3
            selection[axis] = side
            eligible[tuple(selection)] = False
    z, y, x = np.indices(value.shape)
    centers = np.stack(((x + .5) * grid.dx_um, (y + .5) * grid.dy_um, (z + .5) * grid.dz_um), axis=-1)
    distance = np.linalg.norm(np.maximum(np.abs(centers - [32., 32., 16.]) - 8., 0.), axis=-1)
    probe = eligible & (distance >= 2.) & (distance <= 8.)
    valid = probe & (value >= concentration_floor)
    norm = np.sqrt(sum(g**2 for g in gradients))
    relative = np.divide(norm, value, out=np.zeros_like(value), where=valid)
    fraction = float(np.count_nonzero(valid & (relative >= gradient_threshold)) / np.count_nonzero(probe))
    return {'relative_gradient_per_um': float(np.median(relative[valid])) if np.any(valid) else 0.,
            'qualified_probe_fraction': fraction, 'mean_probe_concentration_uM': float(value[probe].mean())}


def audit(simulation):
    rows = []
    for species, values in simulation.fields.concentrations_uM.items():
        ledger = simulation.ledger[species]
        measured = math.fsum(values) * simulation.world.grid.molecules_per_uM_voxel
        expected = math.fsum([simulation.initial_amounts[species], ledger.get('external_net', 0.),
                              ledger.get('internal_net', 0.), ledger.get('reaction_net', 0.), -ledger.get('consumed', 0.)])
        error = measured - expected
        allowance = 1e-6 + 1e-10 * max(abs(measured), abs(expected))
        accepted = math.fsum(simulation.outputs['accepted_uptake']['cumulative_uptake'])
        uptake_error = accepted - ledger.get('consumed', 0.)
        stock = math.fsum(simulation.state['reserve']['intracellular_molecules'])
        initial_stock = len(simulation.project['groups']['cells']['ids']) * 1e6
        reserve_error = stock - (initial_stock + accepted - ledger.get('maintenance', 0.) - ledger.get('growth', 0.))
        reserve_allowance = 1e-6 + 1e-10 * max(stock, initial_stock + accepted)
        rows.append({'species': species, 'field_molecules': measured, 'expected_field_molecules': expected,
                     'field_balance_error': error, 'field_balance_allowance': allowance,
                     'uptake_consumption_error': uptake_error, 'reserve_error': reserve_error,
                     'passed': abs(error) <= allowance and abs(uptake_error) <= 1e-6 and abs(reserve_error) <= reserve_allowance})
    return rows


class Study:
    def __init__(self, destination, *, duration=2., dt=.02):
        self.destination = Path(destination)
        self.destination.mkdir(parents=True, exist_ok=True)
        (self.destination / 'cases').mkdir(exist_ok=True)
        self.duration, self.dt, self.cache = duration, dt, {}
        if not math.isfinite(duration) or duration <= 0 or not math.isfinite(dt) or dt <= 0:
            raise ValueError('Use positive finite duration and time step')

    def run(self, *, dt=None, **configuration):
        dt = self.dt if dt is None else dt
        steps = round(self.duration / dt)
        if steps < 1 or not math.isclose(steps * dt, self.duration, abs_tol=1e-10, rel_tol=0):
            raise ValueError('Duration must be an integer number of steps')
        project = project_for(**configuration)
        key = sha256({'project': project, 'duration_s': self.duration, 'dt_s': dt})
        if key in self.cache:
            return self.cache[key]
        simulation = simulation_from_project(project)
        initial = np.array(project['groups']['cells']['positions_um'])
        previous = initial.copy()
        path = np.zeros(len(initial))
        state = simulation.observation_state['cohort']
        contacts = {cid: int(row['last_contact']) for cid, row in state.items()}
        previous_hits = {cid: row['last_contact'] for cid, row in state.items()}
        first_arrivals = {cid: 0. if row['arrived'] else None for cid, row in state.items()}
        series, trajectories, audits = [], [], []
        gradient_maintained_s = 0.
        call_count = 0
        advance = pts_methylation.advance
        def counted(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return advance(*args, **kwargs)
        def record():
            nonlocal gradient_maintained_s
            chemical = chemical_metrics(simulation)
            metrics = simulation.current.metrics['by_group']['cells']
            if simulation.frame_index and chemical['qualified_probe_fraction'] >= .1:
                gradient_maintained_s += dt
            arrival = [value for value in first_arrivals.values() if value is not None]
            row = {'time_s': simulation.time_s, **chemical, 'chey_p_mean_uM': float(np.mean(simulation.outputs['pts_signal']['chey_p'])),
                   'motor_bias_mean': float(np.mean(simulation.outputs['pts_signal']['motor_bias'])),
                   'arrival_fraction': metrics['ever_arrived_fraction'], 'region_fraction': metrics['region_fraction'],
                   'enrichment_vs_uniform_fluid': metrics['region_fraction'] / ((24.**3 - 16.**3) / (64. * 64. * 32. - 16.**3)),
                   'mean_arrival_s_among_arrivals': float(np.mean(arrival)) if arrival else None,
                   'contact_s_per_cell': metrics['contact_cell_seconds'] / len(initial),
                   'contact_cell_seconds': metrics['contact_cell_seconds'], 'contact_fraction': metrics['contact_fraction'],
                   'contact_entries': sum(contacts.values()), 'converted_per_cell': metrics['cumulative_degradation_molecules'] / len(initial),
                   'converted_molecules': metrics['cumulative_degradation_molecules']}
            series.append(row)
            trajectories.append({'time_s': simulation.time_s, 'positions_xyz_um': simulation.world.groups['cells'].positions_um.tolist()})
        record()
        with patch.object(pts_methylation, 'advance', counted):
            for _ in range(steps):
                simulation.step(dt)
                current = simulation.world.groups['cells'].positions_um
                path += np.linalg.norm(current - previous, axis=1)
                previous = current.copy()
                for cid, row in simulation.observation_state['cohort'].items():
                    contacts[cid] += int(row['last_contact'] and not previous_hits[cid])
                    previous_hits[cid] = row['last_contact']
                    if row['arrived'] and first_arrivals[cid] is None:
                        first_arrivals[cid] = simulation.time_s
                audits.extend({'time_s': simulation.time_s, **r} for r in audit(simulation))
                record()
        final = dict(series[-1])
        displacement = previous - initial
        progress = np.linalg.norm(initial - [32., 32., 16.], axis=1) - np.linalg.norm(previous - [32., 32., 16.], axis=1)
        radial_index = np.divide(progress, path, out=np.full_like(path, np.nan), where=path > 0)
        initial_field = gaussian_average(project['domain']['counts_xyz'], project['domain']['spacing_um_xyz'])
        if configuration.get('zero_initial'):
            initial_field *= 0.
        final.update(msd_um2=float(np.mean(np.sum(displacement**2, axis=1))),
                     finite_window_D_msd_um2_s=float(np.mean(np.sum(displacement**2, axis=1)) / (6 * self.duration)),
                     drift_x_um_s=float(displacement[:, 0].mean() / self.duration),
                     radial_chemotaxis_index=float(np.nanmean(radial_index)) if np.isfinite(radial_index).any() else None,
                     gradient_maintained_s=gradient_maintained_s,
                     initial_dissolved_molecules=float(initial_field.sum() * simulation.world.grid.molecules_per_uM_voxel))
        field = np.asarray(simulation.fields.concentrations_uM['sugar']).reshape(simulation.world.grid.shape)
        passed = all(r['passed'] for r in audits) and call_count == steps
        result = {'case_sha256': key, 'configuration': configuration, 'dt_s': dt, 'duration_s': self.duration,
                  'project': project, 'control_signature': control_signature(project), 'metrics': final,
                  'series': series, 'trajectories': trajectories, 'audits': audits,
                  'signal_advance_calls': call_count, 'expected_signal_calls': steps, 'audit_passed': passed,
                  'ledger': simulation.ledger, 'final_field_xy_uM': field[field.shape[0] // 2].tolist()}
        (self.destination / 'cases' / f'{key}.json').write_text(json.dumps(result, allow_nan=False) + '\n', encoding='utf-8')
        self.cache[key] = result
        print(f'{len(self.cache):04d} {key[:10]} {configuration} audit={passed}', flush=True)
        if not passed:
            raise ValueError(f'Spatial audit failed in {key}; inspect saved per-step balances')
        return result


def bootstrap(values, seed=9201):
    values = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = values[rng.integers(len(values), size=(2000, len(values)))].mean(axis=1)
    lo, hi = np.quantile(means, [.025, .975])
    return float(values.mean()), float(lo), float(hi)


def comparison(reference, changed, *, metric, relative_tolerance=.05):
    if len(reference) != len(changed) or len(reference) < 2:
        raise ValueError('Resolution comparisons need matched independent seed lists')
    differences = np.asarray(changed, float) - np.asarray(reference, float)
    mean, lo, hi = bootstrap(differences)
    allowance = max(ABSOLUTE_TOLERANCES[metric], relative_tolerance * abs(float(np.mean(reference))))
    status = 'within_threshold' if lo >= -allowance and hi <= allowance else (
        'exceeds_threshold' if lo > allowance or hi < -allowance else 'unresolved_at_this_seed_count')
    return {'metric': metric, 'n_seeds': len(reference), 'reference_mean': float(np.mean(reference)),
            'changed_mean': float(np.mean(changed)), 'paired_difference': mean, 'difference_ci_low': lo,
            'difference_ci_high': hi, 'absolute_allowance': allowance, 'status': status}


def credibility(study, seeds, mechanisms):
    comparisons, precision, populations = [], [], []
    for mechanism in mechanisms:
        for feedback in (False, True):
            common = {'mechanism': mechanism, 'feedback': feedback}
            base = [study.run(seed=s, **common) for s in seeds]
            for label, change in [('half_dt', {'dt': study.dt / 2}), ('half_grid', {'spacing': 2.})]:
                changed = [study.run(seed=s, **common, **change) for s in seeds]
                for metric in ENDPOINTS:
                    comparisons.append({'mechanism': mechanism, 'feedback': feedback, 'change': label,
                                        **comparison([r['metrics'][metric] for r in base], [r['metrics'][metric] for r in changed], metric=metric)})
            for n in sorted({max(2, len(seeds) // 2), len(seeds)}):
                for metric in ENDPOINTS:
                    values = [r['metrics'][metric] for r in base[:n]]
                    mean, lo, hi = bootstrap(values)
                    allowance = max(ABSOLUTE_TOLERANCES[metric], .05 * abs(mean))
                    precision.append({'mechanism': mechanism, 'feedback': feedback, 'metric': metric, 'n_seeds': n,
                                      'seed_ids': ' '.join(map(str, seeds[:n])), 'cells_per_run': 8,
                                      'mean': mean, 'ci_low': lo, 'ci_high': hi, 'absolute_allowance': allowance,
                                      'precision_within_threshold': max(hi - mean, mean - lo) <= allowance})
            changed = [study.run(seed=s, cells=16, **common) for s in seeds]
            for metric in ENDPOINTS:
                populations.append({'mechanism': mechanism, 'feedback': feedback, 'cells_reference': 8, 'cells_changed': 16,
                                    'interpretation': 'Population-size/density scan in a fixed domain',
                                    **comparison([r['metrics'][metric] for r in base], [r['metrics'][metric] for r in changed], metric=metric)})
    write_csv(study.destination / 'resolution-check.csv', comparisons)
    write_csv(study.destination / 'seed-precision.csv', precision)
    write_csv(study.destination / 'cell-count-scan.csv', populations)


def controls(study, seeds, mechanisms):
    pairs = []
    for mechanism in mechanisms:
        for seed in seeds:
            off = study.run(seed=seed, mechanism=mechanism, feedback=False)
            on = study.run(seed=seed, mechanism=mechanism, feedback=True)
            if on['control_signature'] != off['control_signature']:
                raise ValueError('A control changed inputs other than the declared motor feedback toggle')
            pairs.append({'mechanism': mechanism, 'seed': seed, 'control_signature': on['control_signature'],
                          **{m + '_difference': on['metrics'][m] - off['metrics'][m] for m in ENDPOINTS},
                          'contact_entries_control': off['metrics']['contact_entries'],
                          'contact_entries_feedback': on['metrics']['contact_entries'],
                          'region_fraction_control': off['metrics']['region_fraction'],
                          'region_fraction_feedback': on['metrics']['region_fraction']})
    write_csv(study.destination / 'chemotaxis-off-control.csv', pairs)


def gradient(study, seeds):
    rows = []
    for release in (0., .5, 2.):
        for diffusion in (.5, 1., 2.):
            for uptake in (0., .5, 2.):
                for seed in seeds:
                    result = study.run(seed=seed, profile='contact', zero_initial=True,
                                       factors={'release': release, 'diffusion': diffusion, 'uptake': uptake})
                    rows.append({'release_factor': release, 'diffusion_factor': diffusion, 'uptake_factor': uptake,
                                 'seed': seed, 'case_sha256': result['case_sha256'],
                                 'qualified_probe_fraction': result['metrics']['qualified_probe_fraction'],
                                 'relative_gradient_per_um': result['metrics']['relative_gradient_per_um'],
                                 'gradient_maintained_s': result['metrics']['gradient_maintained_s'],
                                 'converted_molecules': result['metrics']['converted_molecules']})
    write_csv(study.destination / 'M04-gradient-phase.csv', rows)


def sensitivity(study, seeds, *, trajectories=8):
    baseline = [study.run(seed=s) for s in seeds]
    mean_base = np.mean([r['metrics']['converted_per_cell'] for r in baseline])
    oat = []
    for parameter in FACTORS:
        for factor in (.8, 1.2):
            values = [study.run(seed=s, factors={parameter: factor})['metrics']['converted_per_cell'] for s in seeds]
            average = float(np.mean(values))
            oat.append({'parameter': parameter, 'factor': factor, 'n_seeds': len(seeds), 'baseline': mean_base,
                        'mean_converted_per_cell': average,
                        'normalized_sensitivity': (average - mean_base) / mean_base / (factor - 1.) if mean_base else None})
    write_csv(study.destination / 'M12-OAT.csv', oat)
    heat = []
    for adaptation in (.5, 1., 2.):
        for coupling in (.5, 1., 2.):
            for seed in seeds:
                result = study.run(seed=seed, factors={'adaptation': adaptation, 'ei_coupling': coupling})
                heat.append({'adaptation_factor': adaptation, 'ei_coupling_factor': coupling, 'seed': seed,
                             'case_sha256': result['case_sha256'], **{m: result['metrics'][m] for m in ENDPOINTS}})
    write_csv(study.destination / 'M13-parameter-plane.csv', heat)
    rng = np.random.default_rng(20416)
    effects, paths = [], []
    names = list(FACTORS)
    delta = 2. / 3.
    for trajectory in range(trajectories):
        signs = rng.choice((-1., 1.), size=len(names))
        point = rng.integers(0, 2, size=len(names)) / 3. + np.where(signs < 0, delta, 0.)
        order = rng.permutation(len(names))
        def evaluate(position, step):
            factors = {name: float(np.exp(np.log(.5) + coordinate * np.log(4.))) for name, coordinate in zip(names, position)}
            results = [study.run(seed=s, factors=factors) for s in seeds]
            value = float(np.mean([r['metrics']['converted_per_cell'] for r in results]))
            paths.append({'trajectory': trajectory, 'step': step, **{f'u_{name}': float(v) for name, v in zip(names, position)},
                          **{f'factor_{name}': v for name, v in factors.items()}, 'n_seeds': len(seeds),
                          'mean_converted_per_cell': value})
            return value
        before = evaluate(point, 0)
        for step, index in enumerate(order, 1):
            point = point.copy()
            point[index] += signs[index] * delta
            after = evaluate(point, step)
            effects.append({'trajectory': trajectory, 'parameter': names[index],
                            'elementary_effect': (after - before) / (signs[index] * delta)})
            before = after
    summary = []
    for name in names:
        values = [r['elementary_effect'] for r in effects if r['parameter'] == name]
        summary.append({'parameter': name, 'n_trajectories': trajectories, 'n_motility_seeds': len(seeds),
                        'mu': float(np.mean(values)), 'mu_star': float(np.mean(np.abs(values))),
                        'sigma': float(np.std(values, ddof=1))})
    write_csv(study.destination / 'M14-Morris-paths.csv', paths)
    write_csv(study.destination / 'M14-elementary-effects.csv', effects)
    write_csv(study.destination / 'M14-Morris-summary.csv', summary)


def layers(study, seeds, mechanisms):
    rows = []
    for mechanism in mechanisms:
        for feedback in (False, True):
            for seed in seeds:
                result = study.run(seed=seed, mechanism=mechanism, feedback=feedback)
                rows.extend({'mechanism': mechanism, 'feedback': feedback, 'seed': seed,
                             'case_sha256': result['case_sha256'], **row} for row in result['series'])
    write_csv(study.destination / 'M16-layered-timeseries.csv', rows)


def motion(study, seeds):
    rows = []
    for speed in (.5, 1., 1.5):
        for tumble in (.5, 1., 2.):
            for seed in seeds:
                result = study.run(seed=seed, feedback=False, factors={'speed': speed, 'tumble': tumble})
                rows.append({'speed_factor': speed, 'tumble_factor': tumble, 'seed': seed,
                             **{m: result['metrics'][m] for m in ('msd_um2', 'finite_window_D_msd_um2_s', 'drift_x_um_s', 'radial_chemotaxis_index')}})
    write_csv(study.destination / 'M02-motion-scan.csv', rows)


def diffusion_reference(destination, *, duration=2., dt=.02):
    rows = []
    for dx, step in ((4., dt), (4., dt / 2), (2., dt), (2., dt / 2)):
        count = round(64. / dx)
        grid = GridDomain.volume(count, 2, 2, dx, 4., 4.)
        x = (np.arange(count) + .5) * dx
        average_cosine = np.cos(np.pi * x / 64.) * np.sinc(dx / 128.)
        initial = np.broadcast_to(1. + .5 * average_cosine, grid.shape).copy()
        state = make_local_field_state(grid, [FieldSpecies('S', initial, 10.)])
        steps = round(duration / step)
        if not math.isclose(steps * step, duration, abs_tol=1e-10, rel_tol=0):
            raise ValueError('Diffusion duration must divide by both time steps')
        for _ in range(steps):
            state = propose_local_field_step(state, step).after
        actual = np.asarray(state.concentrations_uM['S']).reshape(grid.shape)
        expected = 1. + .5 * math.exp(-10. * (math.pi / 64.)**2 * duration) * average_cosine
        rows.append({'spacing_x_um': dx, 'dt_s': step, 'duration_s': duration, 'diffusivity_um2_s': 10.,
                     'relative_L2_error': float(np.linalg.norm(actual - expected) / np.linalg.norm(np.broadcast_to(expected, grid.shape))),
                     'mass_change_molecules': float((actual.sum() - initial.sum()) * grid.molecules_per_uM_voxel),
                     'reference': 'Closed-domain cosine eigenmode; exact cell-average initial values'})
    write_csv(Path(destination) / 'diffusion-analytic-reference.csv', rows)
    return rows


def draw(study):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    def save(fig, name):
        for suffix in ('png', 'svg'):
            fig.savefig(study.destination / f'{name}.{suffix}', dpi=180)
        plt.close(fig)
    def read(name):
        path = study.destination / name
        return list(csv.DictReader(path.open(encoding='utf-8', newline=''))) if path.exists() else []
    with plt.rc_context({'font.size': 9, 'axes.spines.top': False, 'axes.spines.right': False}):
        rows = read('M16-layered-timeseries.csv')
        if rows:
            fig, axes = plt.subplots(2, 3, figsize=(10, 6), layout='constrained')
            panels = [('relative_gradient_per_um', 'Relative gradient (1/µm)'), ('chey_p_mean_uM', 'CheY-P (µM)'),
                      ('arrival_fraction', 'Arrived fraction'), ('contact_s_per_cell', 'Contact time per founder (s)'),
                      ('converted_per_cell', 'Converted equivalents per founder'), ('region_fraction', 'Region occupancy')]
            for mechanism in sorted({r['mechanism'] for r in rows}):
                for feedback in ('True', 'False'):
                    group = [r for r in rows if r['mechanism'] == mechanism and r['feedback'] == feedback]
                    times = sorted({float(r['time_s']) for r in group})
                    for axis, (metric, label) in zip(axes.flat, panels):
                        values = np.array([[float(r[metric]) for r in group if float(r['time_s']) == t] for t in times])
                        mean, sd = values.mean(axis=1), values.std(axis=1, ddof=1)
                        color = '#398454' if feedback == 'True' else '#537781'
                        axis.plot(times, mean, color=color, linestyle='-' if mechanism == 'a' else '--',
                                  label=f'PTS {mechanism.upper()}, ' + ('feedback' if feedback == 'True' else 'fixed bias'))
                        axis.fill_between(times, mean - sd, mean + sd, color=color, alpha=.1)
                        axis.set(xlabel='Time (s)', ylabel=label)
            axes.flat[0].legend(fontsize=7)
            fig.suptitle('M16 · Matched runs, separate evaluation layers · mean and sample SD')
            save(fig, 'M16-layered-evaluation')
            fig, axis = plt.subplots(figsize=(5, 5), layout='constrained')
            for feedback, color in ((True, '#398454'), (False, '#537781')):
                result = study.run(seed=1, feedback=feedback)
                positions_array = np.array([r['positions_xyz_um'] for r in result['trajectories']])
                for index in range(positions_array.shape[1]):
                    axis.plot(positions_array[:, index, 0], positions_array[:, index, 1], color=color, alpha=.7,
                              label=('Feedback' if feedback else 'Fixed bias') if index == 0 else None)
            axis.add_patch(plt.Rectangle((24, 24), 16, 16, facecolor='#d9c58d', edgecolor='#5b5544'))
            axis.set(xlabel='X (µm)', ylabel='Y (µm)', xlim=(0, 64), ylim=(0, 64), aspect='equal',
                     title='M01 · XY trajectory projection · preselected seed 1')
            axis.legend(fontsize=8)
            save(fig, 'M01-matched-trajectories')
        rows = read('M04-gradient-phase.csv')
        if rows:
            fig, axes = plt.subplots(1, 3, figsize=(10, 3.6), layout='constrained')
            for axis, uptake in zip(axes, (0., .5, 2.)):
                values = [[np.mean([float(r['gradient_maintained_s']) for r in rows if float(r['release_factor']) == release
                                     and float(r['diffusion_factor']) == diffusion and float(r['uptake_factor']) == uptake])
                           for diffusion in (.5, 1., 2.)] for release in (0., .5, 2.)]
                im = axis.imshow(values, origin='lower', vmin=0, vmax=study.duration, cmap='YlGnBu', aspect='auto')
                axis.set(xticks=range(3), xticklabels=['0.5', '1', '2'], yticks=range(3), yticklabels=['0', '0.5', '2'],
                         xlabel='Diffusion factor', ylabel='Release factor', title=f'Uptake factor {uptake:g}')
            fig.colorbar(im, ax=axes, label='Time above gradient criterion (s)', shrink=.8)
            fig.suptitle('M04 · Zero initial dissolved sugar · mean over motility seeds')
            save(fig, 'M04-gradient-phase')
            snapshots = [study.run(seed=1, profile='contact', zero_initial=True, factors={'release': factor}) for factor in (0., .5, 2.)]
            fig, axes = plt.subplots(1, 3, figsize=(10, 3.4), layout='constrained')
            maximum = max(np.max(r['final_field_xy_uM']) for r in snapshots)
            for axis, factor, result in zip(axes, (0., .5, 2.), snapshots):
                im = axis.imshow(result['final_field_xy_uM'], origin='lower', extent=(0, 64, 0, 64), vmin=0, vmax=maximum, cmap='viridis')
                axis.set(xlabel='X (µm)', ylabel='Y (µm)', title=f'Release ×{factor:g}')
            fig.colorbar(im, ax=axes, label='Sugar (µM)', shrink=.8)
            fig.suptitle(f'M03 · Same seed and physical section, t={study.duration:g} s')
            save(fig, 'M03-field-snapshots')
            fig, axis = plt.subplots(figsize=(6, 3.6), layout='constrained')
            for factor, result in zip((0., .5, 2.), snapshots):
                image = np.asarray(result['final_field_xy_uM'])
                line_y = int(44. / 4.)
                axis.plot((np.arange(image.shape[1]) + .5) * 4., image[line_y], label=f'Release ×{factor:g}')
            axis.set(xlabel='X (µm)', ylabel='Sugar (µM)', title='M03 · Cell-center section near y = 46 µm, z = 18 µm')
            axis.legend(fontsize=8)
            save(fig, 'M03-field-profiles')
        rows = read('M12-OAT.csv')
        if rows:
            names = sorted(FACTORS, key=lambda name: max(abs(float(r['normalized_sensitivity'] or 0)) for r in rows if r['parameter'] == name))
            fig, axis = plt.subplots(figsize=(7, 3.6), layout='constrained')
            for factor, offset, color in ((.8, -.15, '#537781'), (1.2, .15, '#398454')):
                vals = [next(r['normalized_sensitivity'] for r in rows if r['parameter'] == name and float(r['factor']) == factor) for name in names]
                axis.barh(np.arange(len(names)) + offset, [float(v) if v else 0 for v in vals], height=.28, color=color, label=f'Factor {factor:g}')
            axis.set(yticks=range(len(names)), yticklabels=names, xlabel='Normalized one-at-a-time response')
            axis.legend(fontsize=8)
            save(fig, 'M12-OAT')
        rows = read('M13-parameter-plane.csv')
        if rows:
            fig, axis = plt.subplots(figsize=(5, 4), layout='constrained')
            values = [[np.mean([float(r['converted_per_cell']) for r in rows if float(r['adaptation_factor']) == adaptation
                                 and float(r['ei_coupling_factor']) == coupling]) for coupling in (.5, 1., 2.)]
                      for adaptation in (.5, 1., 2.)]
            im = axis.imshow(values, origin='lower', cmap='YlGnBu')
            axis.set(xticks=range(3), xticklabels=['0.5', '1', '2'], yticks=range(3), yticklabels=['0.5', '1', '2'],
                     xlabel='EI dephosphorylation factor', ylabel='Adaptation rate factor', title='M13 · Mean converted equivalents per founder')
            fig.colorbar(im, ax=axis)
            save(fig, 'M13-parameter-plane')
        rows = read('M14-Morris-summary.csv')
        if rows:
            fig, axis = plt.subplots(figsize=(5.5, 4), layout='constrained')
            for row in rows:
                x, y = float(row['mu_star']), float(row['sigma'])
                axis.scatter(x, y, color='#398454')
                axis.annotate(row['parameter'], (x, y), xytext=(4, 4), textcoords='offset points', fontsize=8)
            axis.set(xlabel='Morris µ* (converted equivalents per founder)', ylabel='Morris σ', title='M14 · Screening on log-scaled parameter factors')
            save(fig, 'M14-Morris')
        rows = read('M02-motion-scan.csv')
        if rows:
            fig, axes = plt.subplots(2, 2, figsize=(9, 6), layout='constrained')
            panels = [('msd_um2', 'MSD (µm²)'), ('finite_window_D_msd_um2_s', 'Finite-window MSD/(6T) (µm²/s)'),
                      ('drift_x_um_s', 'Mean axial velocity (µm/s)'), ('radial_chemotaxis_index', 'Radial progress / path length')]
            for axis, (metric, label) in zip(axes.flat, panels):
                for tumble in (.5, 1., 2.):
                    values = [[float(r[metric]) for r in rows if float(r['speed_factor']) == speed
                               and float(r['tumble_factor']) == tumble] for speed in (.5, 1., 1.5)]
                    axis.errorbar([10., 20., 30.], np.mean(values, axis=1), yerr=np.std(values, axis=1, ddof=1),
                                  marker='o', markersize=3, capsize=3, label=f'Tumble rates ×{tumble:g}')
                axis.set(xlabel='Swimming speed (µm/s)', ylabel=label)
            axes.flat[0].legend(fontsize=7)
            fig.suptitle(f'M02 · Fixed motor bias, mean and sample SD · T={study.duration:g} s')
            save(fig, 'M02-motion-scan')


def run_study(destination, *, groups=('credibility', 'controls', 'gradient', 'sensitivity', 'layers', 'motion'),
              seeds=tuple(range(1, 9)), mechanisms=('a', 'b'), duration=2., dt=.02, morris_trajectories=8, plot=True):
    if len(seeds) < 2 or len(set(seeds)) != len(seeds) or any(type(s) is not int or s < 0 for s in seeds):
        raise ValueError('Use at least two distinct nonnegative motility seeds')
    if set(groups) - {'credibility', 'controls', 'gradient', 'sensitivity', 'layers', 'motion'}:
        raise ValueError('Unknown study group')
    if not mechanisms or set(mechanisms) - {'a', 'b'} or morris_trajectories < 2:
        raise ValueError('Declare mechanisms a/b and at least two Morris trajectories')
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Choose an empty output directory to preserve previous studies')
    study = Study(destination, duration=duration, dt=dt)
    manifest = {'kind': 'wiki-spatial-studies', 'source_hashes': source_hashes(),
                'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'python': platform.python_version(), 'numpy': np.__version__, 'duration_s': duration, 'dt_s': dt,
                'groups': list(groups), 'motility_seeds': list(seeds), 'mechanisms': list(mechanisms),
                'parameter_source': 'assumption', 'cell_count': 8, 'cell_count_scan': [8, 16],
                'initial_field': 'Exact volume averages of one Gaussian; zero dissolved sugar for M04',
                'bootstrap': {'samples': 2000, 'seed': 9201, 'interval': [.025, .975]},
                'resolution_relative_tolerance': .05, 'absolute_tolerances': ABSOLUTE_TOLERANCES,
                'gradient_criterion': {'concentration_floor_uM': .01, 'relative_gradient_min_per_um': .02,
                                       'min_qualified_probe_fraction': .1, 'probe_surface_distance_um': [2., 8.]},
                'contact_entries': 'Initial contact plus observed false-to-true contact transitions at committed boundaries',
                'cell_count_interpretation': 'Fixed-domain population-size/density scan, separate from seed precision',
                'source_strength': 'Potential release is enzyme copies × kcat; actual release remains contact dependent',
                'Morris': {'trajectories': morris_trajectories, 'levels': 4, 'delta_normalized': 2/3,
                           'parameter_seed': 20416, 'parameter_factors': FACTORS,
                           'indices': 'Elementary-effect µ, µ*, σ; these are not Sobol indices'}}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if 'credibility' in groups:
        diffusion_reference(destination, duration=duration, dt=dt)
        credibility(study, list(seeds), mechanisms)
    if 'controls' in groups:
        controls(study, seeds, mechanisms)
    subset = tuple(seeds[:min(4, len(seeds))])
    if 'gradient' in groups:
        gradient(study, subset)
    if 'sensitivity' in groups:
        sensitivity(study, subset, trajectories=morris_trajectories)
    if 'layers' in groups:
        layers(study, seeds, mechanisms)
    if 'motion' in groups:
        motion(study, subset)
    if plot:
        draw(study)
    write_csv(destination / 'all-endpoints.csv', [{'case_sha256': r['case_sha256'], 'n_cells': len(r['project']['groups']['cells']['ids']),
                                                 **r['metrics']} for r in study.cache.values()])
    write_csv(destination / 'conservation-and-call-audit.csv',
              [{'case_sha256': r['case_sha256'], 'audit_passed': r['audit_passed'],
                'max_field_balance_error': max(abs(a['field_balance_error']) for a in r['audits']),
                'max_uptake_consumption_error': max(abs(a['uptake_consumption_error']) for a in r['audits']),
                'max_reserve_error': max(abs(a['reserve_error']) for a in r['audits']),
                'signal_advance_calls': r['signal_advance_calls'], 'expected_signal_calls': r['expected_signal_calls']}
               for r in study.cache.values()])
    peclet = [{'flow_speed_um_s': u, 'length_um': length, 'diffusivity_um2_s': 10., 'Pe': u * length / 10.,
               'source_kind': 'assumption', 'flow_velocity_role': 'Residual fluid flow, not cell swimming'}
              for length in (16., 64.) for u in (0., .01, .1, 1.)]
    write_csv(destination / 'peclet-scenarios.csv', peclet)
    print(f'Completed {len(study.cache)} unique spatial cases', flush=True)
    return study


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--groups', nargs='+', default=['credibility', 'controls', 'gradient', 'sensitivity', 'layers', 'motion'])
    parser.add_argument('--seeds', type=int, nargs='+', default=list(range(1, 9)))
    parser.add_argument('--mechanisms', choices=['a', 'b'], nargs='+', default=['a', 'b'])
    parser.add_argument('--duration', type=float, default=2.)
    parser.add_argument('--dt', type=float, default=.02)
    parser.add_argument('--morris-trajectories', type=int, default=8)
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    run_study(args.out, groups=args.groups, seeds=args.seeds, mechanisms=args.mechanisms,
              duration=args.duration, dt=args.dt, morris_trajectories=args.morris_trajectories, plot=not args.no_plots)
