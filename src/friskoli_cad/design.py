"""Bounded, deterministic parameter designs over ordinary scientific projects."""
from copy import deepcopy
from functools import lru_cache
from importlib.resources import files
from itertools import product
import hashlib
import json
import math

from jsonschema import Draft202012Validator

from friskoli_cad.engine.profiles import CHEMOTAXIS_PROFILE, registry_for_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.engine.runtime import SimulationError
from friskoli_cad.protocol.task_validation import canonical_bytes, TaskValidationError

METRICS = frozenset(('mean_displacement_um', 'region_fraction', 'ever_arrived_fraction', 'mean_residence_s'))
MAX_CANDIDATES = 16
MAX_RUNS = 32


class DesignError(ValueError):
    def __init__(self, code, message, path=''):
        self.code, self.path = code, path
        super().__init__(message)


def _fail(message, path='', code='design.invalid'):
    raise DesignError(code, message, path)


def _object(value, required, optional=(), path=''):
    if type(value) is not dict or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        _fail('Missing or unknown object fields', path)


def _text(value, path, maximum=256):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        _fail(f'Expected nonempty text of at most {maximum} characters', path)


def _number(value, path):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail('Expected a finite number', path)
    return value


def _parameter(nodes, registry, node_id, parameter, path):
    if type(node_id) is not str or node_id not in nodes:
        _fail('Unknown graph node', path + '/node_id')
    node = nodes[node_id]
    manifest = registry.get(node['module_id'], node['module_version']).manifest
    if type(parameter) is not str or parameter not in manifest['parameters']:
        _fail('Unknown registered parameter', path + '/parameter')
    spec = manifest['parameters'][parameter]
    if spec.get('type') not in ('number', 'integer'):
        _fail('Only registered number/integer parameters can be explored or constrained', path + '/parameter')
    return spec


def _settings(settings):
    _object(settings, ('dt_s', 'steps'), ('frame_every_steps', 'include_fields', 'seed'), '/settings')
    result = deepcopy(settings)
    if _number(result['dt_s'], '/settings/dt_s') <= 0:
        _fail('dt_s must be positive', '/settings/dt_s')
    if type(result['steps']) is not int or not 1 <= result['steps'] <= 10000:
        _fail('steps must be an integer in [1, 10000]', '/settings/steps')
    if not math.isfinite(result['dt_s'] * result['steps']):
        _fail('Simulation duration must remain finite', '/settings')
    result.setdefault('frame_every_steps', 1)
    result.setdefault('include_fields', True)
    if type(result['frame_every_steps']) is not int or not 1 <= result['frame_every_steps'] <= 10000:
        _fail('frame_every_steps must be an integer in [1, 10000]', '/settings/frame_every_steps')
    if type(result['include_fields']) is not bool:
        _fail('include_fields must be boolean', '/settings/include_fields')
    if 'seed' in result and (type(result['seed']) is not int or not 0 <= result['seed'] <= 2**53 - 1):
        _fail('seed must be a nonnegative safe integer', '/settings/seed')
    return result


@lru_cache(maxsize=2)
def _brief_validator(version='0.1.0'):
    filename = {'0.1.0': 'design-brief-v0.1.schema.json', '0.2.0': 'design-brief-v0.2.schema.json'}.get(version)
    if filename is None:
        _fail('Unsupported design brief version', '/brief/brief_version')
    schema = json.loads(files('friskoli_cad.protocol').joinpath('schemas', filename).read_text(encoding='utf-8'))
    return Draft202012Validator(schema)


def validate_design_brief(project, brief, registry=None):
    """Validate syntax and registered targets; numeric range failures are exclusions."""
    registry = registry_for_project(project) if registry is None else registry
    try:
        canonical_bytes(brief)
    except TaskValidationError as error:
        _fail(str(error), '/brief')
    version = brief.get('brief_version') if isinstance(brief, dict) else None
    error = next(_brief_validator(version).iter_errors(brief), None)
    if error is not None:
        _fail(error.message, '/brief/' + '/'.join(str(p) for p in error.absolute_path))
    required = ('brief_version', 'id', 'name', 'goal', 'chassis', 'variables', 'constraints', 'seeds', 'max_runs')
    if version == '0.2.0':
        required += ('result_constraints', 'selection_policy')
    _object(brief, required, path='/brief')
    for key in ('id', 'name'):
        _text(brief[key], '/brief/' + key)
    goal = brief['goal']
    _object(goal, ('metric', 'direction', 'group_id'), path='/brief/goal')
    if goal['metric'] not in METRICS or goal['direction'] not in ('maximize', 'minimize'):
        _fail('Unsupported metric or optimization direction', '/brief/goal')
    if type(goal['group_id']) is not str or goal['group_id'] not in project['groups']:
        _fail('Goal population does not exist', '/brief/goal/group_id')
    if not project['groups'][goal['group_id']]['ids']:
        _fail('Goal population must contain initial cells', '/brief/goal/group_id')
    chassis = brief['chassis']
    _object(chassis, ('name', 'provenance'), path='/brief/chassis')
    _text(chassis['name'], '/brief/chassis/name')
    _text(chassis['provenance'], '/brief/chassis/provenance', 4096)
    seeds = brief['seeds']
    if type(seeds) is not list or not seeds or any(type(s) is not int or not 0 <= s <= 2**53 - 1 for s in seeds):
        _fail('seeds must contain nonnegative safe integers', '/brief/seeds')
    if len(set(seeds)) != len(seeds):
        _fail('Duplicate seeds are not independent repeats', '/brief/seeds')
    if type(brief['max_runs']) is not int or not 1 <= brief['max_runs'] <= MAX_RUNS:
        _fail('max_runs must be an integer in [1, 32]', '/brief/max_runs')
    if version == '0.2.0':
        policy = brief['selection_policy']
        _object(policy, ('min_repeats', 'min_control_improvement'), path='/brief/selection_policy')
        if type(policy['min_repeats']) is not int or not 2 <= policy['min_repeats'] <= 8:
            _fail('min_repeats must be an integer in [2, 8]', '/brief/selection_policy/min_repeats')
        if _number(policy['min_control_improvement'], '/brief/selection_policy/min_control_improvement') < 0:
            _fail('min_control_improvement must be nonnegative', '/brief/selection_policy/min_control_improvement')
        result_ids = set()
        for i, constraint in enumerate(brief['result_constraints']):
            path = f'/brief/result_constraints/{i}'
            _object(constraint, ('id', 'kind', 'metric', 'group_id', 'operator', 'value'), path=path)
            if constraint['id'] in result_ids:
                _fail('Duplicate result constraint id', path + '/id')
            result_ids.add(constraint['id'])
            if constraint['metric'] not in METRICS or constraint['kind'] not in ('hard', 'soft') or constraint['operator'] not in ('<=', '>='):
                _fail('Unsupported result constraint', path)
            if constraint['group_id'] not in project['groups'] or not project['groups'][constraint['group_id']]['ids']:
                _fail('Result constraint requires a populated initial group', path + '/group_id')
            _number(constraint['value'], path + '/value')
    nodes = {node['id']: node for node in project['graph']['nodes']}
    if type(brief['variables']) is not list or not brief['variables']:
        _fail('At least one exploration variable is required', '/brief/variables')
    seen, count = set(), 1
    for i, variable in enumerate(brief['variables']):
        path = f'/brief/variables/{i}'
        _object(variable, ('node_id', 'parameter', 'values'), path=path)
        _parameter(nodes, registry, variable['node_id'], variable['parameter'], path)
        key = (variable['node_id'], variable['parameter'])
        if key in seen:
            _fail('Repeated exploration parameter', path)
        seen.add(key)
        values = variable['values']
        if type(values) is not list:
            _fail('values must be a list', path + '/values')
        for j, value in enumerate(values):
            _number(value, f'{path}/values/{j}')
        if len(set(values)) != len(values):
            _fail('Duplicate exploration values', path + '/values')
        count *= len(values)
        if count > MAX_CANDIDATES:
            _fail('Cartesian exploration exceeds 16 candidates', path, 'design.budget')
    if type(brief['constraints']) is not list:
        _fail('constraints must be a list', '/brief/constraints')
    ids = set()
    for i, constraint in enumerate(brief['constraints']):
        path = f'/brief/constraints/{i}'
        _object(constraint, ('id', 'kind', 'node_id', 'parameter', 'operator', 'value'), ('weight',), path)
        _text(constraint['id'], path + '/id')
        if constraint['id'] in ids:
            _fail('Duplicate constraint id', path + '/id')
        ids.add(constraint['id'])
        _parameter(nodes, registry, constraint['node_id'], constraint['parameter'], path)
        if constraint['kind'] not in ('hard', 'soft') or constraint['operator'] not in ('<=', '>='):
            _fail('Unsupported constraint kind or operator', path)
        _number(constraint['value'], path + '/value')
        if 'weight' in constraint and _number(constraint['weight'], path + '/weight') < 0:
            _fail('Constraint weight must be nonnegative', path + '/weight')
    return deepcopy(brief)


def _prepare(project, settings):
    # Local import avoids a dependency cycle when the HTTP service imports designs.
    from friskoli_cad.replay_service import prepare_project
    return prepare_project(project, dt_s=settings['dt_s'], steps=settings['steps'], validation_only=True)


def _resource_estimate(project, settings, registry):
    from friskoli_cad.tasks.metadata import estimate
    from friskoli_cad.tasks.service import TaskLimits
    limits = TaskLimits()
    budget = estimate({'project': project, 'execution': {'steps': settings['steps']},
        'output_plan': {'observables': list(project['run']['channels']),
            'frame_every_steps': settings['frame_every_steps'], 'include_fields': settings['include_fields']}}, registry)
    reasons = []
    for field, limit in (('cells', 'cells'), ('voxels', 'voxels'), ('steps', 'steps'),
                         ('memory_bytes', 'estimated_memory_bytes'), ('output_bytes', 'output_bytes')):
        if budget[field] > getattr(limits, limit):
            reasons.append(f'Estimated {field} {budget[field]} exceeds TaskLimits {getattr(limits, limit)}')
    frames = 1 + settings['steps'] // settings['frame_every_steps'] + int(settings['steps'] % settings['frame_every_steps'] != 0)
    if budget['output_bytes'] // frames > limits.chunk_bytes:
        reasons.append(f'Estimated complete frame exceeds TaskLimits chunk_bytes {limits.chunk_bytes}')
    return budget, reasons


def _provenance(brief, control=False):
    return {'kind': 'user', 'reference': f"Design brief {brief['id']}: " +
            ('fixed constant-bias comparison; not experimentally calibrated' if control else
             'user exploration value; not experimentally calibrated')}


def _identifier(brief, suffix):
    prefix = brief['id']
    if len(prefix) + len(suffix) + 1 > 256:
        prefix = prefix[:220] + '-' + hashlib.sha256(prefix.encode()).hexdigest()[:12]
    return prefix + ':' + suffix


def _control(baseline, brief):
    project = deepcopy(baseline)
    graph = project['graph']
    used = {node['id'] for node in graph['nodes']}
    edge_ids = {edge['id'] for edge in graph['edges']}
    overrides = []
    for motion in list(graph['nodes']):
        if motion['module_id'] != 'motion.hazard_run_tumble':
            continue
        nid = 'design_constant_bias_' + motion['id']
        while nid in used:
            nid += '_'
        used.add(nid)
        provenance = _provenance(brief, True)
        graph['nodes'].append({'id': nid, 'module_id': 'signal.constant_bias', 'module_version': '1.0.0',
            'owner': deepcopy(motion['owner']), 'parameters': {'bias': {'value': .5, 'unit': '1', 'provenance': provenance}}})
        graph['edges'] = [edge for edge in graph['edges'] if not (edge['to']['node'] == motion['id'] and edge['to']['port'] == 'motor_bias')]
        eid = nid + '_to_motor'
        while eid in edge_ids:
            eid += '_'
        edge_ids.add(eid)
        graph['edges'].append({'id': eid, 'from': {'node': nid, 'port': 'motor_bias'},
            'to': {'node': motion['id'], 'port': 'motor_bias'}, 'timing': 'previous_step'})
        overrides.append({'node_id': nid, 'parameter': 'bias', 'value': .5, 'unit': '1', 'provenance': deepcopy(provenance)})
    if not overrides:
        _fail('A fixed motor-bias control requires hazard_run_tumble motion', '/project/graph')
    project['id'] = _identifier(brief, 'control')
    return {'id': project['id'], 'name': 'Constant motor bias', 'kind': 'control',
        'project': project, 'overrides': overrides, 'soft_penalty': 0.,
        'explanation': 'Unscanned baseline with fixed motor bias 0.5, matching the N3 control template. Fields, populations, observations and motility parameters are unchanged; exploration constraints apply only to candidates.'}


def generate_design(project, settings, brief):
    """Enumerate and preflight candidates without advancing the numerical simulation."""
    if type(project) is not dict or project.get('project_version') != '0.5.0' or project.get('execution_profile') != CHEMOTAXIS_PROFILE:
        _fail('Design requires a scientific Project 0.5', '/project')
    settings = _settings(settings)
    baseline = deepcopy(project)
    try:
        _prepare(baseline, settings)
    except (ProtocolError, SimulationError, ValueError, TypeError, KeyError) as error:
        _fail('Baseline project cannot be prepared: ' + str(error), '/project')
    registry = registry_for_project(baseline)
    brief = validate_design_brief(baseline, brief, registry)
    nodes = {node['id']: node for node in baseline['graph']['nodes']}
    candidates, excluded, resources = [], [], []
    for index, values in enumerate(product(*(v['values'] for v in brief['variables'])), 1):
        cid, name = _identifier(brief, f'candidate-{index:03d}'), f'Candidate {index:03d}'
        candidate, overrides, reasons = deepcopy(baseline), [], []
        candidate['id'] = cid
        targets = {node['id']: node for node in candidate['graph']['nodes']}
        for variable, value in zip(brief['variables'], values, strict=True):
            nid, key = variable['node_id'], variable['parameter']
            spec = _parameter(nodes, registry, nid, key, '/brief/variables')
            error = next(Draft202012Validator(spec).iter_errors(value), None)
            if error is not None:
                reasons.append(f'{nid}.{key}: {error.message}')
            provenance = _provenance(brief)
            targets[nid]['parameters'][key] = {**targets[nid]['parameters'][key], 'value': value, 'provenance': provenance}
            overrides.append({'node_id': nid, 'parameter': key, 'value': value,
                'unit': spec.get('unit', '1'), 'provenance': deepcopy(provenance)})
        penalty = 0.
        for constraint in brief['constraints']:
            value = targets[constraint['node_id']]['parameters'][constraint['parameter']]['value']
            excess = value - constraint['value'] if constraint['operator'] == '<=' else constraint['value'] - value
            violation = max(0., excess)
            if constraint['kind'] == 'hard' and violation:
                reasons.append(f"{constraint['id']}: {constraint['node_id']}.{constraint['parameter']} {constraint['operator']} {constraint['value']} is not satisfied")
            elif constraint['kind'] == 'soft':
                penalty += constraint.get('weight', 1.) * violation
        if not math.isfinite(penalty):
            reasons.append('Soft constraint penalty exceeds finite numeric range')
        resource, resource_reasons = _resource_estimate(candidate, settings, registry)
        reasons.extend(resource_reasons)
        if not reasons:
            try:
                _prepare(candidate, settings)
            except (ProtocolError, SimulationError, ValueError, TypeError, KeyError) as error:
                reasons.append('Project preparation failed: ' + str(error))
        if reasons:
            excluded.append({'id': cid, 'name': name, 'reasons': reasons})
        else:
            resources.append(resource)
            candidates.append({'id': cid, 'name': name, 'kind': 'candidate', 'project': candidate,
                'overrides': overrides, 'soft_penalty': penalty,
                'explanation': 'User exploration of ' + ', '.join(f"{v['node_id']}.{v['parameter']}={v['value']} {v['unit']}" for v in overrides) + '; exploratory values, not experimental calibration.'})
    feasible = len(candidates)
    if feasible:
        control = _control(baseline, brief)
        resource, reasons = _resource_estimate(control['project'], settings, registry)
        if reasons:
            _fail('Fixed control exceeds task resources: ' + '; '.join(reasons), '/control', 'design.budget')
        try:
            _prepare(control['project'], settings)
        except (ProtocolError, SimulationError, ValueError, TypeError, KeyError) as error:
            _fail('Control project cannot be prepared: ' + str(error), '/control')
        candidates.append(control)
        resources.append(resource)
    elif not excluded:
        excluded.append({'id': _identifier(brief, 'empty'), 'name': 'Empty parameter set', 'reasons': ['At least one variable has no values; no Cartesian candidates exist.']})
    count, repeats = len(candidates), len(brief['seeds'])
    if count > MAX_CANDIDATES or count * repeats > brief['max_runs']:
        _fail('Feasible candidates plus the fixed control exceed the candidate/run budget; reduce values or seeds', '/brief', 'design.budget')
    return {'design_version': brief['brief_version'], 'id': brief['id'], 'brief': brief, 'baseline_project': baseline,
        'settings': settings, 'candidates': candidates, 'excluded': excluded,
        'budget': {'candidate_count': count, 'feasible_candidate_count': feasible, 'control_count': int(bool(feasible)),
            'repeats': repeats, 'total_runs': count * repeats, 'total_steps': count * repeats * settings['steps'],
            'max_runs': brief['max_runs'], 'max_candidates': MAX_CANDIDATES, 'recommendable': feasible >= 2,
            'max_memory_bytes': max((r['memory_bytes'] for r in resources), default=0),
            'total_output_bytes': sum(r['output_bytes'] for r in resources) * repeats,
            'resource_estimates': [{'candidate_id': c['id'], **r} for c, r in zip(candidates, resources, strict=True)]}}
