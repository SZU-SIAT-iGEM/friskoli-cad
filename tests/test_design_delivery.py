"""Native data safety, losslessness and design-specific repeat statistics."""
from copy import deepcopy
import csv
import hashlib
import io
import json
import math
import zipfile

import pytest

from friskoli_cad.design import generate_design
from friskoli_cad.design_delivery import (DesignPackageError, export_design_package,
    import_design_package, design_report_csv, design_report_html)
from friskoli_cad.engine.chemotaxis_templates import make_example


@pytest.fixture(scope='module')
def prototype():
    project = make_example('chemotaxis-pts-a')
    brief = {'brief_version': '0.1.0', 'id': 'native', 'name': 'Native design',
        'goal': {'metric': 'mean_displacement_um', 'direction': 'maximize', 'group_id': 'cells'},
        'chassis': {'name': 'Example', 'provenance': 'Constructed; not calibrated'},
        'variables': [{'node_id': 'capacity', 'parameter': 'g_requested', 'values': [1., 2.]}],
        'constraints': [], 'seeds': [0, 1], 'max_runs': 32}
    design = generate_design(project, {'dt_s': .05, 'steps': 2}, brief)
    return {'package_version': '0.1.0', 'design': design,
        'workspace': {'workspace_format_version': '0.5.0', 'project': project, 'design': design},
        'runs': [], 'registry': None, 'extension': {'preserve': ['未知字段', 1, None]}}


@pytest.fixture
def payload(prototype):
    return deepcopy(prototype)


def run_for(payload, seed=0, value=3., lock_letter='a'):
    candidate = payload['design']['candidates'][0]
    project = deepcopy(candidate['project'])
    project['random_seed'] = seed
    project['run']['run_id'] = f'run-{seed}'
    modules = {(n['module_id'], n['module_version']) for n in project['graph']['nodes']}
    lock = {'registry_sha256': lock_letter * 64,
        'implementations': [{'id': i, 'version': v, 'sha256': 'b' * 64} for i, v in sorted(modules)]}
    execution = {'dt_s': .05, 'steps': 2, 'seed': seed, 'backend': 'numpy-cpu', 'semantics': 'chemotaxis-spatial-v1'}
    output = {'frame_every_steps': 1, 'include_fields': True, 'observables': list(project['run']['channels'])}
    return {'id': f'run-{seed}', 'localId': f'local-{seed}', 'status': 'completed', 'completeness': 'complete',
        'design_ref': {'design_id': payload['design']['id'], 'candidate_id': candidate['id'], 'candidate_name': candidate['name']},
        'project': project, 'settings': {**execution, **output},
        'submission': {'project': deepcopy(project), 'execution': execution, 'output_plan': output,
            'version_lock': lock, 'task_contract_version': '0.4.0'},
        'replay': {'snapshots': [{'frame': {'frame_index': 2, 'time_s': .1}, 'metrics': {
            'metric_version': '0.1.0', 'observation_id': project.get('observation', {}).get('id', 'whole_domain'),
            'by_group': {'cells': {'mean_displacement_um': value}}}}]}}


def rows(payload):
    return list(csv.DictReader(io.StringIO(design_report_csv(payload))))


def first_summary(payload):
    return next(r for r in rows(payload) if r['record_type'] == 'summary')


def rewrite(archive, mutate):
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        entries = {i.filename: z.read(i) for i in z.infolist()}
    mutate(entries)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, value in entries.items(): z.writestr(name, value)
    return out.getvalue()


def rehash(entries, name):
    manifest = json.loads(entries['manifest.json'])
    manifest['files'][name] = {'bytes': len(entries[name]), 'sha256': hashlib.sha256(entries[name]).hexdigest()}
    entries['manifest.json'] = json.dumps(manifest).encode()


def test_lossless_deterministic_data_only_roundtrip(payload):
    payload['runs'] = [run_for(payload), run_for(payload, 1, None)]
    original = deepcopy(payload)
    archive = export_design_package(payload)
    assert archive == export_design_package(payload)
    assert import_design_package(archive) == original == payload
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        assert {'manifest.json', 'design/package.json', 'evidence/runs.json', 'reports/design.html'} <= set(z.namelist())


def test_sample_statistics_and_traceable_csv(payload):
    payload['runs'] = [run_for(payload, 0, 3.), run_for(payload, 1, 5.)]
    summary = first_summary(payload)
    assert summary['n'] == '2' and float(summary['mean']) == 4.
    assert float(summary['sample_sd']) == pytest.approx(math.sqrt(2))
    run = next(r for r in rows(payload) if r['record_type'] == 'run')
    assert json.loads(run['frozen_input_json'])['submission']['version_lock'] == payload['runs'][0]['submission']['version_lock']


@pytest.mark.parametrize('mutation,reason', [
    (lambda r: r.update(status='failed'), 'failed_partial_or_incomplete'),
    (lambda r: r.update(completeness='partial'), 'failed_partial_or_incomplete'),
    (lambda r: r['design_ref'].update(design_id='other'), 'foreign_design'),
    (lambda r: r['design_ref'].update(candidate_id='other'), 'unknown_candidate'),
    (lambda r: r['settings'].update(dt_s=.1), 'settings_mismatch'),
    (lambda r: r['settings'].update(seed=1), 'seed_mismatch'),
    (lambda r: r['project']['domain'].update(counts_xyz=[1, 1, 1]), 'project_mismatch'),
    (lambda r: r['replay']['snapshots'][-1]['metrics'].update(observation_id='other'), 'observation_mismatch'),
    (lambda r: r['replay']['snapshots'][-1]['frame'].update(time_s=.05), 'incomplete_endpoint'),
    (lambda r: r['replay']['snapshots'][-1]['metrics']['by_group']['cells'].update(mean_displacement_um=None), 'null_or_missing_metric'),
    (lambda r: r['submission'].pop('version_lock'), 'missing_version_lock'),
])
def test_invalid_evidence_is_preserved_but_never_averaged(payload, mutation, reason):
    run = run_for(payload)
    mutation(run)
    payload['runs'] = [run]
    summary = first_summary(payload)
    assert summary['n'] == '0' and summary['mean'] == summary['sample_sd'] == ''
    assert any(r['status_or_reason'] == reason for r in rows(payload))
    assert import_design_package(export_design_package(payload)) == payload


def test_duplicate_seed_excludes_all_duplicates_and_separate_locks_do_not_pool(payload):
    payload['runs'] = [run_for(payload), run_for(payload)]
    assert first_summary(payload)['n'] == '0'
    assert sum(r['status_or_reason'] == 'duplicate_seed' for r in rows(payload)) == 2
    payload['runs'] = [run_for(payload), run_for(payload, 1, 99., 'c')]
    summaries = [r for r in rows(payload) if r['record_type'] == 'summary' and r['n'] == '1']
    assert len(summaries) == 2
    assert all(r['sample_sd'] == '' and r['status_or_reason'] == 'insufficient_repeats' for r in summaries)


def test_html_escapes_and_csv_neutralizes_formula_names(payload):
    payload['design']['brief']['name'] = '</title><script>alert(1)</script>'
    payload['design']['candidates'][0]['name'] = ' \t=HYPERLINK("evil")'
    html = design_report_html(payload)
    assert '<script>' not in html and '&lt;script&gt;' in html and 'maximize' in html
    assert first_summary(payload)['candidate_name'].startswith("'")


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/drive', 'design\\package.json', 'extra.py'])
def test_unexpected_and_unsafe_paths_rejected(payload, name):
    archive = rewrite(export_design_package(payload), lambda entries: entries.update({name: b'bad'}))
    with pytest.raises(DesignPackageError, match='path'):
        import_design_package(archive)


def test_checksum_and_consistent_manifest_do_not_hide_divergent_evidence(payload):
    archive = export_design_package(payload)
    with pytest.raises(DesignPackageError) as error:
        import_design_package(rewrite(archive, lambda e: e.update({'design/brief.json': b'{}'})))
    assert error.value.code == 'design_package.checksum'
    def tamper(entries):
        entries['design/brief.json'] = b'{}'
        rehash(entries, 'design/brief.json')
    with pytest.raises(DesignPackageError) as error:
        import_design_package(rewrite(archive, tamper))
    assert error.value.code == 'design_package.cross_reference'


def test_duplicate_zip_member_and_expansion_budget_rejected(payload, monkeypatch):
    archive = export_design_package(payload)
    out = io.BytesIO(archive)
    with pytest.warns(UserWarning), zipfile.ZipFile(out, 'a') as z:
        z.writestr('manifest.json', b'{}')
    with pytest.raises(DesignPackageError): import_design_package(out.getvalue())
    monkeypatch.setattr('friskoli_cad.design_delivery.MAX_TOTAL_BYTES', 100)
    with pytest.raises(DesignPackageError) as error: import_design_package(archive)
    assert error.value.code == 'design_package.size'


def test_readonly_historical_lock_does_not_require_current_install_but_cross_checks(payload):
    payload['runs'] = [run_for(payload, lock_letter='f')]
    assert import_design_package(export_design_package(payload)) == payload
    payload['runs'][0]['manifest'] = {'input_snapshot': {'registry_sha256': 'e' * 64}}
    with pytest.raises(DesignPackageError) as error: export_design_package(payload)
    assert error.value.code == 'design_package.lock'


def test_undeclared_candidate_change_and_false_frozen_input_hash_rejected(payload):
    candidate = payload['design']['candidates'][0]
    candidate['project']['domain']['spacing_um_xyz'][0] += 1
    with pytest.raises(DesignPackageError) as error: export_design_package(payload)
    assert error.value.code == 'design_package.provenance'
    candidate['project']['domain']['spacing_um_xyz'][0] -= 1
    payload['runs'] = [run_for(payload)]
    payload['runs'][0]['manifest'] = {'input_snapshot': {'registry_sha256': 'a' * 64, 'document_sha256': 'f' * 64}}
    with pytest.raises(DesignPackageError) as error: export_design_package(payload)
    assert error.value.code == 'design_package.lock'


def test_malformed_nested_records_are_structured_rejections(payload):
    payload['runs'] = [{'replay': []}]
    with pytest.raises(DesignPackageError) as error: design_report_html(payload)
    assert error.value.code == 'design_package.structure'
