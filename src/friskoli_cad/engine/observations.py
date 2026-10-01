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
    {"id": "mean_displacement_um", "label": "Directional displacement", "unit": "um", "description": "Mean signed displacement along the declared axis among surviving initial cell IDs. Null when none remain."},
    {"id": "region_fraction", "label": "Region occupancy", "unit": "1", "description": "Fraction of all currently living cells whose centers are in the closed observation box. Null for an empty living group."},
    {"id": "ever_arrived_fraction", "label": "Cohort arrival fraction", "unit": "1", "description": "Fraction of initial cell IDs observed in the region at any committed step boundary, including t=0. Null for an initially empty group."},
    {"id": "mean_residence_s", "label": "Cohort residence time", "unit": "s", "description": "Initial-cohort mean of the left-endpoint region occupancy integral over numerical steps. Death stops accumulation. Null for an initially empty group."},
]


def observation_definition(project):
    extent = [a * b for a, b in zip(project['domain']['counts_xyz'], project['domain']['spacing_um_xyz'])]
    value = deepcopy(project.get('observation', {'id': 'whole_domain', 'label': 'Whole domain / +X',
        'axis': 0, 'region_lower_um': [0., 0., 0.], 'region_upper_um': extent}))
    if (type(value) is not dict or set(value) != {'id', 'label', 'axis', 'region_lower_um', 'region_upper_um'}
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
    return value


def _inside(position, definition):
    return all(lo <= p <= hi for p, lo, hi in zip(position, definition['region_lower_um'], definition['region_upper_um']))


def initial_observation(project, frame):
    definition = observation_definition(project)
    return {'observation_state_version': '0.1.0', 'definition': definition,
        'time_s': frame['time_s'], 'groups': sorted(project['groups']),
        'cohort': {cell['id']: {'group_id': cell['group_id'], 'initial_position_um': list(cell['position_um']),
            'last_position_um': list(cell['position_um']), 'alive': True,
            'arrived': _inside(cell['position_um'], definition), 'residence_s': 0.}
            for cell in frame['cells']}}


def advance_observation(state, frame):
    next_state = deepcopy(state)
    dt = frame['time_s'] - state['time_s']
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError('Observation time must increase')
    current = {cell['id']: cell for cell in frame['cells']}
    for cid, item in next_state['cohort'].items():
        if item['alive'] and _inside(item['last_position_um'], state['definition']):
            item['residence_s'] += dt
        cell = current.get(cid)
        if cell is not None:
            if not item['alive'] or cell['group_id'] != item['group_id']:
                raise ValueError('A cohort ID cannot reappear or change group')
            item['last_position_um'] = list(cell['position_um'])
            item['arrived'] = item['arrived'] or _inside(cell['position_um'], state['definition'])
        item['alive'] = cell is not None
    next_state['time_s'] = frame['time_s']
    return next_state


def observation_metrics(state, frame):
    by_group, axis = {}, state['definition']['axis']
    for gid in state['groups']:
        cohort = [v for v in state['cohort'].values() if v['group_id'] == gid]
        alive_cohort = [v for v in cohort if v['alive']]
        live = [cell for cell in frame['cells'] if cell['group_id'] == gid]
        by_group[gid] = {'initial_count': len(cohort), 'live_count': len(live),
            'mean_displacement_um': math.fsum(v['last_position_um'][axis] - v['initial_position_um'][axis]
                for v in alive_cohort) / len(alive_cohort) if alive_cohort else None,
            'region_fraction': sum(_inside(cell['position_um'], state['definition']) for cell in live) / len(live) if live else None,
            'ever_arrived_fraction': sum(v['arrived'] for v in cohort) / len(cohort) if cohort else None,
            'mean_residence_s': math.fsum(v['residence_s'] for v in cohort) / len(cohort) if cohort else None}
    return {'metric_version': '0.1.0', 'observation_id': state['definition']['id'], 'by_group': by_group}


def validate_observation_state(state, project, frame):
    """Strict checkpoint state validation; no history is invented during restore."""
    if type(state) is not dict or set(state) != {'observation_state_version', 'definition', 'time_s', 'groups', 'cohort'}:
        raise ValueError('Malformed observation state')
    expected = {cid: (gid, list(pos)) for gid, group in project['groups'].items()
                for cid, pos in zip(group['ids'], group['positions_um'])}
    if (state['observation_state_version'] != '0.1.0' or state['definition'] != observation_definition(project)
        or type(state['time_s']) not in (int, float) or not math.isfinite(state['time_s'])
        or state['time_s'] != frame['time_s'] or state['time_s'] < 0
        or state['groups'] != sorted(project['groups']) or type(state['cohort']) is not dict
        or set(state['cohort']) != set(expected)):
        raise ValueError('Observation state differs from frozen project or frame')
    current = {c['id']: c for c in frame['cells']}
    for cid, (gid, position) in expected.items():
        value = state['cohort'][cid]
        if type(value) is not dict or set(value) != {'group_id', 'initial_position_um', 'last_position_um', 'alive', 'arrived', 'residence_s'}:
            raise ValueError('Malformed cohort record')
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
    return deepcopy(state)
