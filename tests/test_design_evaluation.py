"""Recommendation requires complete, immutable and paired exploratory evidence."""
from copy import deepcopy
import csv
import io

import pytest

from friskoli_cad.design import generate_design, DesignError
from friskoli_cad.design_delivery import (export_design_package, import_design_package,
    design_report_csv, design_report_html, DesignPackageError)
from friskoli_cad.design_evaluation import evaluate_design
from friskoli_cad.engine.presets import make_example


@pytest.fixture(scope='module')
def prototype():
    project = make_example('center-pts-a-small-strong')
    brief = {'brief_version': '0.2.0', 'id': 'evaluated', 'name': 'Exploratory design',
             'goal': {'metric': 'mean_displacement_um', 'direction': 'maximize', 'group_id': 'cells'},
             'chassis': {'name': 'Example', 'provenance': 'Constructed; not calibrated'},
             'variables': [{'node_id': 'capacity', 'parameter': 'g_requested', 'values': [1., 2.]}],
             'constraints': [], 'result_constraints': [
                 {'id': 'arrival', 'kind': 'hard', 'metric': 'ever_arrived_fraction', 'group_id': 'cells', 'operator': '>=', 'value': .5},
                 {'id': 'residence', 'kind': 'soft', 'metric': 'mean_residence_s', 'group_id': 'cells', 'operator': '<=', 'value': .05}],
             'selection_policy': {'min_repeats': 2, 'min_control_improvement': 0.}, 'seeds': [0, 1], 'max_runs': 32}
    design = generate_design(project, {'dt_s': .05, 'steps': 2}, brief)
    modules = {(n['module_id'], n['module_version']) for c in design['candidates'] for n in c['project']['graph']['nodes']}
    lock = {'registry_sha256': 'a' * 64, 'implementations': [{'id': i, 'version': v, 'sha256': 'b' * 64} for i, v in sorted(modules)]}
    runs = []
    for index, candidate in enumerate(design['candidates']):
        for seed in brief['seeds']:
            frozen = deepcopy(candidate['project'])
            frozen['random_seed'] = seed
            execution = {'dt_s': .05, 'steps': 2, 'seed': seed, 'backend': 'numpy-cpu', 'semantics': 'modular-spatial-v1'}
            output = {'frame_every_steps': 1, 'include_fields': True, 'observables': list(frozen['run']['channels'])}
            runs.append({'id': f'{index}-{seed}', 'status': 'completed', 'completeness': 'complete',
                         'design_ref': {'design_id': design['id'], 'candidate_id': candidate['id']},
                         'project': frozen, 'settings': {**execution, **output},
                         'submission': {'project': deepcopy(frozen), 'execution': execution, 'output_plan': output,
                                        'version_lock': deepcopy(lock), 'task_contract_version': '0.4.0'},
                         'replay': {'snapshots': [{'frame': {'frame_index': 2, 'time_s': .1}, 'metrics': {
                             'metric_version': '0.1.0', 'observation_id': frozen.get('observation', {}).get('id', 'whole_domain'),
                             'by_group': {'cells': {'mean_displacement_um': [5., 4., 1.][index] + seed,
                                                    'ever_arrived_fraction': .75, 'mean_residence_s': .1}}}}]}})
    return {'package_version': '0.1.0', 'design': design, 'workspace': None, 'registry': None, 'runs': runs}


@pytest.fixture
def payload(prototype):
    return deepcopy(prototype)


def values(run):
    return run['replay']['snapshots'][-1]['metrics']['by_group']['cells']


def test_complete_paired_design_recommends_unique_best_without_unit_weighting(payload):
    before = deepcopy(payload)
    result = evaluate_design(payload)
    assert payload == before
    assert result['status'] == 'recommended'
    assert result['recommended_candidate_id'] == payload['design']['candidates'][0]['id']
    first = result['candidates'][0]
    assert first['control_comparison'] == {'n': 2, 'mean': 4., 'sample_sd': 0., 'minimum': 4., 'maximum': 4.}
    assert first['constraints'][0]['status'] == 'pass'
    assert first['constraints'][1]['status'] == 'fail'  # Report soft outcome without pooling its units.
    assert first['status'] == 'eligible'


@pytest.mark.parametrize('mutation,reason', [
    (lambda p: p['runs'].pop(), 'incomplete_candidate_evidence'),
    (lambda p: p['runs'].append(deepcopy(p['runs'][0])), 'incomplete_candidate_evidence'),
    (lambda p: p['runs'][0].update(status='failed'), 'incomplete_candidate_evidence'),
    (lambda p: p['runs'][0].update(completeness='partial'), 'incomplete_candidate_evidence'),
    (lambda p: values(p['runs'][0]).update(mean_displacement_um=None), 'incomplete_candidate_evidence'),
    (lambda p: values(p['runs'][0]).update(ever_arrived_fraction=None), 'incomplete_candidate_evidence'),
    (lambda p: p['runs'][0]['submission']['version_lock'].update(registry_sha256='c' * 64), 'incompatible_series'),
    (lambda p: p['design']['brief']['selection_policy'].update(min_repeats=3), 'insufficient_repeats'),
])
def test_incomplete_or_incompatible_evidence_never_selects_another_candidate(payload, mutation, reason):
    mutation(payload)
    result = evaluate_design(payload)
    assert result['status'] == 'no_recommendation'
    assert result['recommended_candidate_id'] is None
    assert reason in result['reasons']


def test_each_seed_must_meet_hard_condition_and_improve_control(payload):
    values(payload['runs'][0])['ever_arrived_fraction'] = .49
    result = evaluate_design(payload)
    assert result['candidates'][0]['status'] == 'excluded'
    assert result['recommended_candidate_id'] == payload['design']['candidates'][1]['id']
    # A large mean cannot hide one repeat which fails the paired threshold.
    values(payload['runs'][2])['mean_displacement_um'] = 1.
    values(payload['runs'][3])['mean_displacement_um'] = 100.
    result = evaluate_design(payload)
    assert result['status'] == 'no_recommendation'
    assert 'control_improvement_not_met' in result['candidates'][1]['reasons']


def test_minimization_sign_and_ties_are_explicit(payload):
    payload['design']['brief']['goal']['direction'] = 'minimize'
    values(payload['runs'][4])['mean_displacement_um'] = 20.
    values(payload['runs'][5])['mean_displacement_um'] = 21.
    result = evaluate_design(payload)
    assert result['recommended_candidate_id'] == payload['design']['candidates'][1]['id']
    for left, right in ((0, 2), (1, 3)):
        values(payload['runs'][left])['mean_displacement_um'] = values(payload['runs'][right])['mean_displacement_um']
    result = evaluate_design(payload)
    assert result['status'] == 'no_recommendation' and 'tied_objective' in result['reasons']


def test_old_design_does_not_silently_acquire_a_selection_policy(payload):
    payload['design']['design_version'] = '0.1.0'
    payload['design']['brief']['brief_version'] = '0.1.0'
    del payload['design']['brief']['selection_policy']
    del payload['design']['brief']['result_constraints']
    result = evaluate_design(payload)
    assert result['status'] == 'no_recommendation'
    assert 'selection_policy_not_configured' in result['reasons']


def test_native_package_and_reports_share_evaluation_and_preserve_inputs(payload):
    assert import_design_package(export_design_package(payload)) == payload
    report = list(csv.DictReader(io.StringIO(design_report_csv(payload))))
    row = next(r for r in report if r['record_type'] == 'evaluation')
    import json
    assert json.loads(row['evaluation_json']) == evaluate_design(payload)
    html = design_report_html(payload)
    assert '探索性推荐' in html and '统计显著性' in html
    assert payload['design']['candidates'][0]['id'] in html


@pytest.mark.parametrize('changes', [
    {'selection_policy': {'min_repeats': 1, 'min_control_improvement': 0}},
    {'selection_policy': {'min_repeats': 2, 'min_control_improvement': -1}},
    {'result_constraints': [{'id': 'x', 'kind': 'hard', 'metric': 'mean_displacement_um', 'group_id': 'missing', 'operator': '>=', 'value': 0}]},
])
def test_invalid_result_contracts_rejected_for_generation_and_import(payload, changes):
    payload['design']['brief'].update(changes)
    with pytest.raises(DesignError):
        generate_design(payload['design']['baseline_project'], payload['design']['settings'], payload['design']['brief'])
    with pytest.raises(DesignPackageError):
        evaluate_design(payload)


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'interrupted', 'rejected'])
def test_explicit_retry_keeps_failed_attempt_but_unique_success_can_be_evaluated(payload, status):
    failed = deepcopy(payload['runs'][0])
    failed.update(id='old-' + status, status=status, completeness='partial')
    failed['replay'] = None
    payload['runs'].insert(0, failed)
    original = deepcopy(payload)
    result = evaluate_design(payload)
    assert result['status'] == 'recommended'
    first = result['candidates'][0]
    assert first['objective']['n'] == 2 and first['seeds'] == [0, 1]
    assert first['excluded_attempts'] == [{'run_id':'old-' + status, 'seed':0,
        'reason':'failed_partial_or_incomplete', 'status':status, 'superseded_by_complete_repeat':True}]
    assert payload == original
    assert import_design_package(export_design_package(payload)) == original


@pytest.mark.parametrize('mutation,reason', [
    (lambda r: r['settings'].update(dt_s=.123), 'settings_mismatch'),
    (lambda r: r['project']['domain'].update(counts_xyz=[1,1,1]), 'project_mismatch'),
    (lambda r: r['submission'].pop('version_lock'), 'missing_version_lock'),
    (lambda r: r['submission']['version_lock'].update(implementations=[]), 'missing_implementation_lock'),
])
def test_successful_retry_does_not_hide_malformed_failed_input_or_lock(payload, mutation, reason):
    failed = deepcopy(payload['runs'][0])
    failed.update(id='bad-failure',status='failed',completeness='partial')
    mutation(failed)
    payload['runs'].append(failed)
    result = evaluate_design(payload)
    assert result['status'] == 'no_recommendation'
    assert reason in result['candidates'][0]['reasons']
    attempt = result['candidates'][0]['excluded_attempts'][0]
    assert attempt['reason'] == reason and not attempt['superseded_by_complete_repeat']


def test_claimed_success_with_bad_endpoint_cannot_be_hidden_by_another_success(payload):
    bad = deepcopy(payload['runs'][0])
    bad['id'] = 'fake-complete'
    bad['replay']['snapshots'][-1]['frame']['time_s'] = .01
    payload['runs'].append(bad)
    result = evaluate_design(payload)
    assert result['status'] == 'no_recommendation'
    first = result['candidates'][0]
    assert 'duplicate_seed' in first['reasons'] and 'incomplete_endpoint' in first['reasons']
    assert any(a['run_id']=='fake-complete' and a['reason']=='incomplete_endpoint' for a in first['excluded_attempts'])


@pytest.mark.parametrize('status', ['running', 'submission_unknown'])
def test_unresolved_attempt_is_not_treated_as_a_superseded_failure(payload, status):
    pending=deepcopy(payload['runs'][0]);pending.update(id='pending',status=status,completeness='none',replay=None)
    payload['runs'].append(pending)
    result=evaluate_design(payload)
    assert result['status']=='no_recommendation'
    assert not result['candidates'][0]['excluded_attempts'][0]['superseded_by_complete_repeat']
