"""Step-boundary cohort observations, independent of stored-frame frequency.

Residence is a left-endpoint rectangle integral at numerical step resolution;
arrival means seen in the closed region at a committed numerical boundary.
Descendants do not enlarge the initial-cohort denominator.
"""
from copy import deepcopy
import math


DEFINITIONS = [
    {"id": "initial_count", "label": "Initial cohort", "unit": "cell", "description": "Number of cells at initialization; descendants do not enter this denominator."},
    {"id": "live_count", "label": "Live cells", "unit": "cell", "description": "All living cells in the group at this committed boundary, including descendants."},
    {"id": "mean_position_um", "label": "Mean axial position", "unit": "um", "description": "Mean center position along the observation axis over all living cells. Null for an empty group."},
    {"id": "drift_um_s", "label": "10–120 s drift", "unit": "um/s", "description": "Least-squares slope of mean axial position at every committed numerical boundary in [10,120] s. Null until 120 s or with fewer than two valid boundaries."},
    {"id": "mean_displacement_um", "label": "Directional displacement", "unit": "um", "description": "Mean signed displacement along the declared axis among surviving initial cell IDs. Null when none remain."},
    {"id": "region_fraction", "label": "Region occupancy", "unit": "1", "description": "Fraction of all currently living cells whose centers are in the closed observation box. Null for an empty living group."},
    {"id": "ever_arrived_fraction", "label": "Cohort arrival fraction", "unit": "1", "description": "Fraction of initial cell IDs observed in the region at any committed step boundary, including t=0. Null for an initially empty group."},
    {"id": "mean_residence_s", "label": "Cohort residence time", "unit": "s", "description": "Initial-cohort mean of the left-endpoint region occupancy integral over numerical steps. Death stops accumulation. Null for an initially empty group."},
    {"id": "contact_fraction", "label": "Surface contact fraction", "unit": "1", "description": "Fraction of currently living cells whose capsule surface lies within contact_range_um of a material obstacle. Distance is measured capsule-to-box, the same quantity reaction.contact_degradation tests, not centre-to-box. Null unless the definition declares contact_range_um and for an empty living group."},
    {"id": "contact_cell_seconds", "label": "Cohort contact integral", "unit": "cell*s", "description": "Sum over the initial cohort of the left-endpoint contact occupancy integral. Death stops accumulation. Null unless the definition declares contact_range_um."},
]
DEFINITIONS.extend({'id': 'radial.' + key, 'label': label, 'unit': unit,
    'description': description + ' Optional system observation radial-spheres@1: declared center and strictly increasing radii; computed for every group. Definition: docs/science/n5-b-lifecycle.md.'}
    for key, label, unit, description in [
        ('mean_distance_um', 'Mean distance to center', 'um', 'Mean radius over living cells; null for no living cells.'),
        ('mean_inward_displacement_um', 'Founder inward displacement', 'um', 'Mean initial radius minus current radius over surviving initial IDs; null if none survive.'),
        ('live_founder_count', 'Surviving initial IDs', 'cell', 'Living initial cell IDs; excludes newly created daughter IDs.'),
        ('live_descendant_count', 'Living new daughter IDs', 'cell', 'Living IDs absent from the initial cohort.'),
        ('shells.live_count', 'Cumulative sphere occupancy', 'cell', 'All live centers inside each closed sphere. Spheres are cumulative, not disjoint shells.'),
        ('shells.live_fraction', 'Cumulative sphere fraction', '1', 'Occupancy divided by current live count; null for an empty group.'),
        ('shells.volume_enrichment', 'Volume-normalized enrichment', '1', 'Sphere live fraction divided by sphere/domain volume fraction; center and radii must keep spheres inside the domain.'),
        ('shells.founder_ever_arrived_fraction', 'Founder sphere arrival', '1', 'Ever observed inside at committed step boundaries, divided by initial count; includes time zero.'),
        ('shells.founder_mean_residence_s', 'Founder sphere residence', 's', 'Left-endpoint sphere occupancy integral, averaged over initial IDs; death stops accumulation.'),
        ('shells.founder_mean_first_arrival_s', 'Mean observed first arrival', 's', 'First committed boundary inside each sphere, averaged over arrived initial IDs only; null if none arrived.')])


def observation_definition(project):
    extent = [a * b for a, b in zip(project['domain']['counts_xyz'], project['domain']['spacing_um_xyz'])]
    value = deepcopy(project.get('observation', {'id': 'whole_domain', 'label': 'Whole domain / +X',
        'axis': 0, 'region_lower_um': [0., 0., 0.], 'region_upper_um': extent}))
    base = {'id', 'label', 'axis', 'region_lower_um', 'region_upper_um'}
    radial = {'radial_center_um', 'radial_radii_um'}
    contact = {'contact_range_um'}
    if (type(value) is not dict or set(value) not in (base, base | radial, base | contact, base | radial | contact)
        or not isinstance(value['id'], str) or not value['id'] or not isinstance(value['label'], str)
        or not value['label'] or type(value['axis']) is not int or value['axis'] not in (0, 1, 2)):
        raise ValueError('Invalid observation definition')
    for name in ('region_lower_um', 'region_upper_um'):
        coords = value[name]
        if (not isinstance(coords, (list, tuple)) or len(coords) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in coords)):
            raise ValueError('Observation region must have three finite coordinates')
    if any(not 0 <= lo < hi <= size for lo, hi, size in zip(value['region_lower_um'], value['region_upper_um'], extent)):
        raise ValueError('Observation region must lie inside the physical domain and have positive extent')
    if 'radial_center_um' in value:
        center, radii = value['radial_center_um'], value['radial_radii_um']
        if (type(center) is not list or len(center) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in center)
            or type(radii) is not list or not radii or len(radii) > 32
            or any(type(r) not in (int, float) or not math.isfinite(r) or r <= 0 for r in radii)
            or any(a >= b for a, b in zip(radii, radii[1:]))
            or any(c - radii[-1] < 0 or c + radii[-1] > size for c, size in zip(center, extent))):
            raise ValueError('Radial observation requires finite center and increasing positive radii, with complete spheres inside the domain')
    if 'contact_range_um' in value:
        reach = value['contact_range_um']
        if type(reach) not in (int, float) or not math.isfinite(reach) or reach <= 0:
            raise ValueError('Contact observation requires a finite positive contact_range_um')
    return value


def _contact_hits(cells, obstacles, reach):
    """Capsule-surface distance to the nearest obstacle, matching the degradation rule."""
    from .collision import Capsule, capsule_box_gap
    from .motion import heading_from_orientation
    hits = {}
    for cell in cells:
        geometry = cell['geometry']
        heading = heading_from_orientation([cell['orientation_xyzw']])[0]
        capsule = Capsule(cell['id'], tuple(cell['position_um']), tuple(heading),
                          geometry['length_um'], geometry['diameter_um'])
        hits[cell['id']] = any(capsule_box_gap(capsule, box) <= reach for box in obstacles)
    return hits


def _inside(position, definition):
    return all(lo <= p <= hi for p, lo, hi in zip(position, definition['region_lower_um'], definition['region_upper_um']))


def initial_observation(project, frame, obstacles=()):
    definition = observation_definition(project)
    result = {'observation_state_version': '1.0.0', 'definition': definition,
        'time_s': frame['time_s'], 'groups': sorted(project['groups']),
        'regression': {gid: {'n': 0, 'sum_t': 0., 'sum_y': 0., 'sum_tt': 0., 'sum_ty': 0.} for gid in project['groups']},
        'cohort': {cell['id']: {'group_id': cell['group_id'], 'initial_position_um': list(cell['position_um']),
            'last_position_um': list(cell['position_um']), 'alive': True,
            'arrived': _inside(cell['position_um'], definition), 'residence_s': 0.}
            for cell in frame['cells']}}
    if 'contact_range_um' in definition:
        hits = _contact_hits(frame['cells'], obstacles, definition['contact_range_um'])
        for cid, item in result['cohort'].items():
            item['last_contact'] = bool(hits.get(cid, False))
            item['contact_ever'] = bool(hits.get(cid, False))
            item['contact_residence_s'] = 0.
    if 'radial_center_um' in definition:
        result['radial_domain_volume_um3'] = math.prod(a * b for a, b in zip(project['domain']['counts_xyz'], project['domain']['spacing_um_xyz']))
        for item in result['cohort'].values():
            radius = math.dist(item['initial_position_um'], definition['radial_center_um'])
            item['radial_first_arrival_s'] = [frame['time_s'] if radius <= r else None for r in definition['radial_radii_um']]
            item['radial_residence_s'] = [0. for _ in definition['radial_radii_um']]
    return result


def advance_observation(state, frame, obstacles=()):
    next_state = deepcopy(state)
    dt = frame['time_s'] - state['time_s']
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError('Observation time must increase')
    current = {cell['id']: cell for cell in frame['cells']}
    reach = state['definition'].get('contact_range_um')
    hits = _contact_hits(frame['cells'], obstacles, reach) if reach is not None else {}
    for cid, item in next_state['cohort'].items():
        if item['alive'] and _inside(item['last_position_um'], state['definition']):
            item['residence_s'] += dt
        if reach is not None and item['alive'] and item.get('last_contact'):
            item['contact_residence_s'] += dt      # left endpoint, as residence above
        cell = current.get(cid)
        if 'radial_center_um' in state['definition']:
            center = state['definition']['radial_center_um']
            previous_radius = math.dist(item['last_position_um'], center)
            next_radius = math.dist(cell['position_um'], center) if cell else math.inf
            for i, radius in enumerate(state['definition']['radial_radii_um']):
                if item['alive'] and previous_radius <= radius:
                    item['radial_residence_s'][i] += dt
                if next_radius <= radius and item['radial_first_arrival_s'][i] is None:
                    item['radial_first_arrival_s'][i] = frame['time_s']
        if cell is not None:
            if not item['alive'] or cell['group_id'] != item['group_id']:
                raise ValueError('A cohort ID cannot reappear or change group')
            item['last_position_um'] = list(cell['position_um'])
            item['arrived'] = item['arrived'] or _inside(cell['position_um'], state['definition'])
            if reach is not None:
                item['last_contact'] = bool(hits.get(cid, False))
                item['contact_ever'] = item.get('contact_ever', False) or item['last_contact']
        elif reach is not None:
            item['last_contact'] = False
        item['alive'] = cell is not None
    next_state['time_s'] = frame['time_s']
    if 10. - 1e-10 <= frame['time_s'] <= 120. + 1e-10:
        for gid in state['groups']:
            positions = [c['position_um'][state['definition']['axis']] for c in frame['cells'] if c['group_id'] == gid]
            if not positions:
                continue
            t, y = frame['time_s'], math.fsum(positions) / len(positions)
            r = next_state['regression'][gid]
            r['n'] += 1; r['sum_t'] += t; r['sum_y'] += y
            r['sum_tt'] += t * t; r['sum_ty'] += t * y
    return next_state


def observation_metrics(state, frame, obstacles=()):
    by_group, axis = {}, state['definition']['axis']
    for gid in state['groups']:
        cohort = [v for v in state['cohort'].values() if v['group_id'] == gid]
        alive_cohort = [v for v in cohort if v['alive']]
        live = [cell for cell in frame['cells'] if cell['group_id'] == gid]
        r = state['regression'][gid]
        denominator = r['sum_tt'] - r['sum_t'] ** 2 / r['n'] if r['n'] else 0.
        drift = (r['sum_ty'] - r['sum_t'] * r['sum_y'] / r['n']) / denominator if state['time_s'] >= 120. - 1e-10 and r['n'] >= 2 and denominator > 0 else None
        by_group[gid] = {'initial_count': len(cohort), 'live_count': len(live),
            'mean_position_um': math.fsum(c['position_um'][axis] for c in live) / len(live) if live else None,
            'drift_um_s': drift,
            'mean_displacement_um': math.fsum(v['last_position_um'][axis] - v['initial_position_um'][axis]
                for v in alive_cohort) / len(alive_cohort) if alive_cohort else None,
            'region_fraction': sum(_inside(cell['position_um'], state['definition']) for cell in live) / len(live) if live else None,
            'ever_arrived_fraction': sum(v['arrived'] for v in cohort) / len(cohort) if cohort else None,
            'mean_residence_s': math.fsum(v['residence_s'] for v in cohort) / len(cohort) if cohort else None}
        if 'radial_center_um' in state['definition']:
            center = state['definition']['radial_center_um']
            distances = [math.dist(c['position_um'], center) for c in live]
            radial = {'center_um': list(center), 'mean_distance_um': math.fsum(distances) / len(live) if live else None,
                'mean_inward_displacement_um': math.fsum(math.dist(v['initial_position_um'], center) - math.dist(v['last_position_um'], center)
                    for v in alive_cohort) / len(alive_cohort) if alive_cohort else None,
                'live_founder_count': len(alive_cohort), 'live_descendant_count': len(live) - len(alive_cohort), 'shells': []}
            for i, radius in enumerate(state['definition']['radial_radii_um']):
                count = sum(d <= radius for d in distances)
                fraction = count / len(live) if live else None
                arrivals = [v['radial_first_arrival_s'][i] for v in cohort if v['radial_first_arrival_s'][i] is not None]
                radial['shells'].append({'radius_um': radius, 'live_count': count, 'live_fraction': fraction,
                    'volume_enrichment': fraction / (4 * math.pi * radius**3 / (3 * state['radial_domain_volume_um3'])) if fraction is not None else None,
                    'founder_ever_arrived_fraction': len(arrivals) / len(cohort) if cohort else None,
                    'founder_mean_residence_s': math.fsum(v['radial_residence_s'][i] for v in cohort) / len(cohort) if cohort else None,
                    'founder_mean_first_arrival_s': math.fsum(arrivals) / len(arrivals) if arrivals else None})
            by_group[gid]['radial'] = radial
        if 'contact_range_um' in state['definition']:
            hits = _contact_hits(live, obstacles, state['definition']['contact_range_um'])
            by_group[gid]['contact_fraction'] = sum(hits.values()) / len(live) if live else None
            by_group[gid]['contact_cell_seconds'] = math.fsum(v['contact_residence_s'] for v in cohort) if cohort else None
    return {'metric_version': '0.1.0', 'observation_id': state['definition']['id'], 'by_group': by_group}


def validate_observation_state(state, project, frame):
    """Strict checkpoint state validation; no history is invented during restore."""
    radial = 'radial_center_um' in project.get('observation', {})
    contact = 'contact_range_um' in project.get('observation', {})
    extra = {'radial_domain_volume_um3'} if radial else set()
    if type(state) is not dict or set(state) != {'observation_state_version', 'definition', 'time_s', 'groups', 'cohort', 'regression'} | extra:
        raise ValueError('Malformed observation state')
    expected = {cid: (gid, list(pos)) for gid, group in project['groups'].items()
                for cid, pos in zip(group['ids'], group['positions_um'])}
    if (state['observation_state_version'] != '1.0.0' or state['definition'] != observation_definition(project)
        or type(state['time_s']) not in (int, float) or not math.isfinite(state['time_s'])
        or state['time_s'] != frame['time_s'] or state['time_s'] < 0
        or state['groups'] != sorted(project['groups']) or type(state['cohort']) is not dict
        or set(state['cohort']) != set(expected)):
        raise ValueError('Observation state differs from frozen project or frame')
    if type(state['regression']) is not dict or set(state['regression']) != set(project['groups']):
        raise ValueError('Regression population history differs')
    for r in state['regression'].values():
        if (type(r) is not dict or set(r) != {'n', 'sum_t', 'sum_y', 'sum_tt', 'sum_ty'}
            or type(r['n']) is not int or not 0 <= r['n'] <= frame['frame_index']
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in r.values())
            or r['n'] == 0 and any(r[k] != 0 for k in r if k != 'n')):
            raise ValueError('Invalid axial regression history')
    if radial and state['radial_domain_volume_um3'] != math.prod(a * b for a, b in zip(project['domain']['counts_xyz'], project['domain']['spacing_um_xyz'])):
        raise ValueError('Radial observation volume differs from domain')
    current = {c['id']: c for c in frame['cells']}
    for cid, (gid, position) in expected.items():
        value = state['cohort'][cid]
        extra = ({'radial_first_arrival_s', 'radial_residence_s'} if radial else set())
        extra |= {'last_contact', 'contact_ever', 'contact_residence_s'} if contact else set()
        if type(value) is not dict or set(value) != {'group_id', 'initial_position_um', 'last_position_um', 'alive', 'arrived', 'residence_s'} | extra:
            raise ValueError('Malformed cohort record')
        if contact and (type(value['last_contact']) is not bool or type(value['contact_ever']) is not bool
                        or type(value['contact_residence_s']) not in (int, float)
                        or not math.isfinite(value['contact_residence_s'])
                        or not 0 <= value['contact_residence_s'] <= state['time_s']):
            raise ValueError('Invalid cohort contact accounting')
        if (value['group_id'] != gid or value['initial_position_um'] != position
            or type(value['alive']) is not bool or value['alive'] != (cid in current)
            or type(value['arrived']) is not bool or type(value['residence_s']) not in (int, float)
            or not math.isfinite(value['residence_s']) or not 0 <= value['residence_s'] <= state['time_s']):
            raise ValueError('Invalid cohort accounting')
        pos = value['last_position_um']
        if type(pos) is not list or len(pos) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in pos):
            raise ValueError('Invalid cohort position')
        if cid in current and (current[cid]['position_um'] != pos or current[cid]['group_id'] != gid):
            raise ValueError('Cohort position differs from committed frame')
        if (cid in current and _inside(pos, state['definition']) or _inside(position, state['definition']) or value['residence_s'] > 0) and not value['arrived']:
            raise ValueError('Observed occupancy requires an arrival')
        if radial:
            radii, center = state['definition']['radial_radii_um'], state['definition']['radial_center_um']
            arrivals, residence = value['radial_first_arrival_s'], value['radial_residence_s']
            if type(arrivals) is not list or type(residence) is not list or len(arrivals) != len(radii) or len(residence) != len(radii):
                raise ValueError('Radial cohort shape differs from declared radii')
            for i, radius in enumerate(radii):
                arrival, spent = arrivals[i], residence[i]
                if (type(spent) not in (int, float) or not math.isfinite(spent) or not 0 <= spent <= state['time_s']
                    or arrival is not None and (type(arrival) not in (int, float) or not math.isfinite(arrival) or not 0 <= arrival <= state['time_s'])
                    or (spent > 0 or cid in current and math.dist(pos, center) <= radius) and arrival is None
                    or math.dist(position, center) <= radius and arrival != 0
                    or arrival is not None and spent > state['time_s'] - arrival + 1e-12
                    or i > 0 and spent < residence[i - 1]
                    or i > 0 and arrivals[i - 1] is not None and (arrival is None or arrival > arrivals[i - 1])):
                    raise ValueError('Invalid radial arrival/residence history')
    return deepcopy(state)
