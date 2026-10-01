from copy import deepcopy
import json

import pytest

from friskoli_cad.design import DesignError, generate_design
from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol.task_validation import canonical_bytes, canonical_loads


def brief(**changes):
    result = {'brief_version': '0.1.0', 'id': 'test-design', 'name': 'Explore swimming speed',
        'goal': {'metric': 'mean_displacement_um', 'direction': 'maximize', 'group_id': 'cells'},
        'chassis': {'name': 'Constructed E. coli', 'provenance': 'User-selected exploratory chassis, not a calibrated strain'},
        'variables': [{'node_id': 'motility', 'parameter': 'speed_um_s', 'values': [5., 10.]}],
        'constraints': [], 'seeds': [0, 1, 2], 'max_runs': 32}
    result.update(changes)
    return result


def settings():
    return {'dt_s': .01, 'steps': 2}


def node(project, nid):
    return next(n for n in project['graph']['nodes'] if n['id'] == nid)


@pytest.mark.parametrize('example', ['pts-a', 'pts-b', 'mcp', 'control', 'materials', 'lifecycle'])
def test_all_six_templates_have_real_candidates_and_fixed_control(example):
    baseline = make_example('chemotaxis-' + example)
    before = deepcopy(baseline)
    result = generate_design(baseline, settings(), brief())
    assert baseline == before == result['baseline_project']
    assert result['budget']['total_runs'] == 9
    assert result['budget']['total_steps'] == 18
    assert result['budget']['recommendable']
    assert [c['kind'] for c in result['candidates']] == ['candidate', 'candidate', 'control']
    for candidate in result['candidates']:
        project = candidate['project']
        for key in ('groups', 'domain', 'species', 'observation'):
            assert project[key] == baseline[key]
        assert all(o['provenance']['kind'] == 'user' for o in candidate['overrides'])
        assert all('not experimentally calibrated' in o['provenance']['reference'] for o in candidate['overrides'])
        sim = simulation_from_project(canonical_loads(canonical_bytes(project)), seed=0)
        sim.step(.01)
        sim.step(.01)
        assert sim.frame_index == 2
    control = result['candidates'][-1]['project']
    assert node(control, 'motility') == node(baseline, 'motility')
    edges = [e for e in control['graph']['edges'] if e['to'] == {'node': 'motility', 'port': 'motor_bias'}]
    assert len(edges) == 1
    constant = node(control, edges[0]['from']['node'])
    assert constant['module_id'] == 'signal.constant_bias'
    assert constant['parameters']['bias']['value'] == .5
    result['baseline_project']['graph']['nodes'][0]['parameters'].clear()
    assert baseline == before
    assert result['candidates'][0]['project']['graph']['nodes'][0]['parameters']


def test_control_is_not_scanned_even_when_original_signal_bias_is_variable():
    project = make_example('chemotaxis-control')
    b = brief(variables=[{'node_id': 'motor_signal', 'parameter': 'bias', 'values': [.1, .9]}])
    result = generate_design(project, settings(), b)
    assert [node(c['project'], 'motor_signal')['parameters']['bias']['value'] for c in result['candidates'][:2]] == [.1, .9]
    assert node(result['candidates'][-1]['project'], 'motor_signal') == node(project, 'motor_signal')
    assert result['candidates'][-1]['overrides'][0]['value'] == .5


def constraint(kind, value, **updates):
    result = {'id': 'speed-limit', 'kind': kind, 'node_id': 'motility', 'parameter': 'speed_um_s', 'operator': '<=', 'value': value}
    result.update(updates)
    return result


def test_hard_constraints_soft_penalty_and_incomplete_design_are_explicit():
    project = make_example('chemotaxis-pts-a')
    result = generate_design(project, settings(), brief(constraints=[constraint('hard', 6)]))
    assert result['budget']['feasible_candidate_count'] == 1
    assert not result['budget']['recommendable']
    assert len(result['excluded']) == 1
    assert 'speed-limit' in result['excluded'][0]['reasons'][0]
    result = generate_design(project, settings(), brief(constraints=[constraint('soft', 6, weight=2)]))
    assert [c['soft_penalty'] for c in result['candidates']] == [0., 8., 0.]
    assert not result['excluded']


@pytest.mark.parametrize('cause', ['hard', 'range', 'coupled'])
def test_no_feasible_candidates_returns_empty_and_reasons(cause):
    project = make_example('chemotaxis-pts-a')
    b = brief()
    if cause == 'hard':
        b['constraints'] = [constraint('hard', -1)]
    elif cause == 'range':
        b['variables'][0]['values'] = [-1, -2]
    else:
        b['variables'] = [{'node_id': 'motility', 'parameter': 'minimum_tumble_rate_s', 'values': [4, 5]}]
    result = generate_design(project, settings(), b)
    assert result['candidates'] == []
    assert result['budget']['total_runs'] == 0
    assert not result['budget']['recommendable']
    assert len(result['excluded']) == 2
    assert all(item['reasons'] for item in result['excluded'])


def test_generate_never_advances_simulation(monkeypatch):
    from friskoli_cad.engine.chemotaxis_runtime import ChemotaxisSimulation
    monkeypatch.setattr(ChemotaxisSimulation, 'step', lambda *args: pytest.fail('Design generation advanced simulation'))
    assert generate_design(make_example(), settings(), brief())['budget']['candidate_count'] == 3


@pytest.mark.parametrize('problem', ['unknown_node', 'unknown_parameter', 'string_parameter', 'nan', 'inf', 'bool',
    'duplicate_seed', 'negative_seed', 'empty_seed', 'duplicate_variable', 'duplicate_value', 'unknown_metric',
    'unknown_group', 'empty_variables', 'too_many_values', 'run_budget', 'negative_weight', 'unknown_key'])
def test_invalid_or_oversized_briefs_fail_without_mutation(problem):
    project, b = make_example(), brief()
    if problem == 'unknown_node': b['variables'][0]['node_id'] = 'missing'
    elif problem == 'unknown_parameter': b['variables'][0]['parameter'] = 'missing'
    elif problem == 'string_parameter': b['variables'][0]['parameter'] = 'turn_kernel'
    elif problem in ('nan', 'inf', 'bool'): b['variables'][0]['values'] = [{'nan': float('nan'), 'inf': float('inf'), 'bool': True}[problem]]
    elif problem == 'duplicate_seed': b['seeds'] = [0, 0]
    elif problem == 'negative_seed': b['seeds'] = [-1]
    elif problem == 'empty_seed': b['seeds'] = []
    elif problem == 'duplicate_variable': b['variables'] *= 2
    elif problem == 'duplicate_value': b['variables'][0]['values'] = [1, 1.]
    elif problem == 'unknown_metric': b['goal']['metric'] = 'speed'
    elif problem == 'unknown_group': b['goal']['group_id'] = 'missing'
    elif problem == 'empty_variables': b['variables'] = []
    elif problem == 'too_many_values': b['variables'][0]['values'] = list(range(17))
    elif problem == 'run_budget': b['max_runs'] = 8
    elif problem == 'negative_weight': b['constraints'] = [constraint('soft', 1, weight=-1)]
    else: b['evidence'] = {'calibrated': True}
    before = deepcopy(project)
    with pytest.raises(DesignError):
        generate_design(project, settings(), b)
    assert project == before


@pytest.mark.parametrize('changes', [{'dt_s': 0}, {'dt_s': float('nan')}, {'steps': 10001}, {'steps': True}, {'frame_every_steps': 0}, {'include_fields': 1}])
def test_invalid_settings_are_rejected(changes):
    with pytest.raises(DesignError):
        generate_design(make_example(), {**settings(), **changes}, brief())


def test_cartesian_enumeration_is_deterministic_and_inputs_are_detached():
    b = brief(variables=[{'node_id': 'motility', 'parameter': 'speed_um_s', 'values': [5, 10]},
        {'node_id': 'motility', 'parameter': 'maximum_tumble_rate_s', 'values': [2, 3]}])
    original = deepcopy(b)
    first = generate_design(make_example(), settings(), b)
    assert first == generate_design(make_example(), settings(), b)
    assert [tuple(o['value'] for o in c['overrides']) for c in first['candidates'][:-1]] == [(5, 2), (5, 3), (10, 2), (10, 3)]
    first['brief']['variables'][0]['values'].append(100)
    assert b == original


def test_invalid_baseline_and_unreachable_parameter_bindings_are_rejected():
    project = make_example()
    project['graph']['edges'] = [e for e in project['graph']['edges'] if e['to']['node'] != 'motility']
    with pytest.raises(DesignError, match='Baseline'):
        generate_design(project, settings(), brief())


def test_resource_estimate_matches_task_admission_and_excludes_oversize_output():
    project = make_example('chemotaxis-lifecycle')
    normal = generate_design(project, settings(), brief())
    assert normal['budget']['max_memory_bytes'] >= 64 * 1024 * 1024
    assert normal['budget']['total_output_bytes'] == sum(e['output_bytes'] for e in normal['budget']['resource_estimates']) * 3
    oversized = generate_design(project, {'dt_s': .01, 'steps': 10000}, brief())
    assert not oversized['candidates']
    assert all('TaskLimits' in '; '.join(e['reasons']) for e in oversized['excluded'])
    assert oversized['budget']['total_runs'] == 0


def test_returned_design_matches_registered_schemas_with_long_id():
    from importlib.resources import files
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    docs = [json.loads(p.read_text(encoding='utf-8')) for p in files('friskoli_cad.protocol').joinpath('schemas').iterdir() if p.name.endswith('.schema.json')]
    registry = Registry().with_resources((d['$id'], Resource.from_contents(d)) for d in docs)
    validator = Draft202012Validator({'$ref': 'urn:friskoli:design:0.1.0'}, registry=registry)
    result = generate_design(make_example(), {**settings(), 'frame_every_steps': 5}, brief(id='x' * 256))
    validator.validate(result)
    assert len({c['project']['id'] for c in result['candidates']}) == 3
    assert all(len(c['id']) <= 256 for c in result['candidates'])
