"""Data-only native design packages and provenance-aware descriptive reports.

Import never resolves plugins, executes a project, or replaces historical locks
with the local installation's lock. Checksums establish consistency, not authorship.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import csv
import hashlib
from html import escape
import io
import json
import math
from pathlib import PurePosixPath
import re
import stat
import statistics
import zipfile


VERSION = '0.1.0'
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 32
_HASH = re.compile(r'^[a-f0-9]{64}$')
_MEMBERS = {'design/package.json', 'design/brief.json', 'design/candidates.json',
            'evidence/runs.json', 'dependencies/registry.json', 'dependencies/locks.json',
            'reports/design.html', 'reports/design.csv'}
# Reports are deliberately retained as historical, human-readable artefacts.
# Their bytes may differ between renderer revisions while the structured
# evidence remains lossless and independently checked below.
_STRUCTURED_MEMBERS = _MEMBERS - {'reports/design.html', 'reports/design.csv'}


class DesignPackageError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _fail(code, message):
    raise DesignPackageError('design_package.' + code, message)


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        _fail('json', f'Expected finite JSON data: {error}')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('json', 'Duplicate JSON member: ' + key)
        result[key] = value
    return result


def _loads(data):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda value: _fail('json', 'Nonfinite JSON value'))
    except (UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, DesignPackageError):
            raise
        _fail('json', f'Invalid UTF-8 JSON: {error}')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _object(value, label):
    if type(value) is not dict:
        _fail('structure', label + ' must be an object')
    return value


def _list(value, label):
    if type(value) is not list:
        _fail('structure', label + ' must be an array')
    return value


def _text(value, label):
    if not isinstance(value, str) or not value:
        _fail('structure', label + ' must be a nonempty string')


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _lock(lock):
    _object(lock, 'version_lock')
    if not isinstance(lock.get('registry_sha256'), str) or not _HASH.fullmatch(lock['registry_sha256']):
        _fail('lock', 'Invalid registry SHA-256')
    implementations = _list(lock.get('implementations'), 'implementations')
    seen = set()
    for item in implementations:
        _object(item, 'implementation')
        _text(item.get('id'), 'implementation.id')
        _text(item.get('version'), 'implementation.version')
        key = item['id'], item['version']
        if key in seen or not isinstance(item.get('sha256'), str) or not _HASH.fullmatch(item['sha256']):
            _fail('lock', 'Duplicate implementation or invalid implementation SHA-256')
        seen.add(key)


def _project(project):
    _object(project, 'project')
    _object(project.get('groups'), 'project.groups')
    _object(project.get('domain'), 'project.domain')
    _object(project.get('run'), 'project.run')
    nodes = _list(_object(project.get('graph'), 'project.graph').get('nodes'), 'graph.nodes')
    edges = _list(project['graph'].get('edges'), 'graph.edges')
    for edge in edges:
        _object(edge, 'edge')
        _text(edge.get('id'), 'edge.id')
        for key in ('from', 'to'):
            endpoint = _object(edge.get(key), 'edge.' + key)
            _text(endpoint.get('node'), 'edge node')
            _text(endpoint.get('port'), 'edge port')
    seen = set()
    for node in nodes:
        _object(node, 'node')
        _text(node.get('id'), 'node.id')
        if node['id'] in seen:
            _fail('structure', 'Duplicate project node')
        seen.add(node['id'])
        _text(node.get('module_id'), 'module_id')
        _text(node.get('module_version'), 'module_version')
        for parameter in _object(node.get('parameters'), 'parameters').values():
            _object(parameter, 'parameter')
            if not {'value', 'provenance'} <= parameter.keys():
                _fail('provenance', 'Parameter value and provenance must be preserved')
            _object(parameter['provenance'], 'parameter.provenance')


def _derivation(candidate, baseline):
    expected = deepcopy(baseline)
    nodes = {n['id']: n for n in expected['graph']['nodes']}
    actual_nodes = {n['id']: n for n in candidate['project']['graph']['nodes']}
    added = set()
    for override in candidate['overrides']:
        nid, parameter = override['node_id'], override['parameter']
        if nid not in nodes:
            new = actual_nodes[nid]
            if candidate['kind'] != 'control' or new['module_id'] != 'signal.constant_bias' or parameter != 'bias':
                _fail('provenance', 'Unexpected added node in candidate derivation')
            nodes[nid] = deepcopy(new)
            expected['graph']['nodes'].append(nodes[nid])
            added.add(nid)
        elif parameter not in nodes[nid]['parameters']:
            _fail('provenance', 'Override parameter is absent from baseline')
        else:
            nodes[nid]['parameters'][parameter].update(value=deepcopy(override['value']), provenance=deepcopy(override['provenance']))
    if added:
        edges = candidate['project']['graph']['edges']
        for edge in expected['graph']['edges']:
            replacements = [e for e in edges if e['to'] == edge['to'] and e['from']['node'] in added]
            if replacements:
                target = nodes.get(edge['to']['node'], {})
                if (len(replacements) != 1 or edge['to']['port'] != 'motor_bias'
                        or target.get('module_id') != 'motion.hazard_run_tumble'
                        or replacements[0]['from']['port'] != 'motor_bias'
                        or replacements[0].get('timing') != 'previous_step'):
                    _fail('provenance', 'Control changes an undeclared signal path')
                edge.clear()
                edge.update(deepcopy(replacements[0]))
    actual = deepcopy(candidate['project'])
    expected['graph']['edges'].sort(key=lambda e: e['id'])
    actual['graph']['edges'].sort(key=lambda e: e['id'])
    if _project_science(expected) != _project_science(actual):
        _fail('provenance', 'Candidate changes baseline evidence outside declared overrides/control wiring')


def _validate(payload):
    # Copy through JSON to reject non-data objects and avoid caller mutation.
    data = _loads(_json(payload))
    _object(data, 'package')
    if data.get('package_version') != VERSION:
        _fail('version', 'Unsupported native design package version; use the design-package import action')
    if not {'design', 'workspace', 'runs', 'registry'} <= data.keys():
        _fail('structure', 'Package must preserve design, workspace, runs and registry')
    design = _object(data['design'], 'design')
    if design.get('design_version') not in ('0.1.0', '0.2.0'):
        _fail('version', 'Unsupported design version')
    _text(design.get('id'), 'design.id')
    brief = _object(design.get('brief'), 'brief')
    if brief.get('brief_version') != design['design_version']:
        _fail('version', 'Unsupported brief version')
    if brief['brief_version'] == '0.2.0':
        from friskoli_cad.design import _brief_validator
        error = next(_brief_validator('0.2.0').iter_errors(brief), None)
        if error is not None:
            _fail('structure', 'Invalid design brief: ' + error.message)
    _text(brief.get('id'), 'brief.id')
    _text(brief.get('name'), 'brief.name')
    goal = _object(brief.get('goal'), 'goal')
    for key in ('metric', 'direction', 'group_id'):
        _text(goal.get(key), 'goal.' + key)
    if goal['direction'] not in ('maximize', 'minimize'):
        _fail('structure', 'Unsupported goal direction')
    _object(brief.get('chassis'), 'chassis')
    _list(brief.get('variables'), 'variables')
    seeds = _list(brief.get('seeds'), 'seeds')
    if not seeds or any(type(s) is not int or s < 0 for s in seeds) or len(set(seeds)) != len(seeds):
        _fail('structure', 'Brief seeds must be unique nonnegative integers')
    _project(design.get('baseline_project'))
    if goal['group_id'] not in design['baseline_project']['groups']:
        _fail('structure', 'Goal group is absent from baseline project')
    result_ids = set()
    for constraint in brief.get('result_constraints', []):
        if constraint['id'] in result_ids:
            _fail('structure', 'Duplicate result constraint id')
        result_ids.add(constraint['id'])
        group = design['baseline_project']['groups'].get(constraint['group_id'])
        if not isinstance(group, dict) or not group.get('ids'):
            _fail('structure', 'Result constraint requires a populated initial group')
    settings = _object(design.get('settings'), 'settings')
    if not _finite(settings.get('dt_s')) or settings['dt_s'] <= 0 or type(settings.get('steps')) is not int or settings['steps'] < 1:
        _fail('structure', 'Positive dt_s and integer steps are required')
    _object(design.get('budget'), 'budget')
    _list(design.get('excluded'), 'excluded')
    candidates = _list(design.get('candidates'), 'candidates')
    ids = set()
    for candidate in candidates:
        _object(candidate, 'candidate')
        _text(candidate.get('id'), 'candidate.id')
        _text(candidate.get('name'), 'candidate.name')
        if candidate['id'] in ids or candidate.get('kind') not in ('candidate', 'control'):
            _fail('structure', 'Duplicate candidate or invalid candidate kind')
        ids.add(candidate['id'])
        _project(candidate.get('project'))
        _text(candidate.get('explanation'), 'candidate.explanation')
        if not _finite(candidate.get('soft_penalty')) or candidate['soft_penalty'] < 0:
            _fail('structure', 'Candidate soft_penalty must be finite and nonnegative')
        nodes = {n['id']: n for n in candidate['project']['graph']['nodes']}
        for override in _list(candidate.get('overrides'), 'overrides'):
            _object(override, 'override')
            actual = nodes.get(override.get('node_id'), {}).get('parameters', {}).get(override.get('parameter'))
            if actual is None or any(actual.get(k) != override.get(k) for k in ('value', 'provenance')) or actual.get('unit', '1') != override.get('unit', '1'):
                _fail('provenance', 'Candidate override disagrees with its frozen project')
        _derivation(candidate, design['baseline_project'])
    if data['registry'] is not None:
        _object(data['registry'], 'registry')
    if data['workspace'] is not None:
        _object(data['workspace'], 'workspace')
    for run in _list(data['runs'], 'runs'):
        _object(run, 'run')
        for key in ('design_ref', 'submission', 'project', 'settings', 'replay', 'task', 'manifest'):
            if run.get(key) is not None:
                _object(run[key], 'run.' + key)
        submission = run.get('submission')
        if submission is not None:
            _object(submission, 'submission')
            for key in ('execution', 'output_plan', 'project'):
                if submission.get(key) is not None:
                    _object(submission[key], 'submission.' + key)
            if submission.get('version_lock') is not None:
                _lock(submission['version_lock'])
                expected = submission['version_lock']['registry_sha256']
                for holder in (run.get('task'), run.get('manifest'), (run.get('replay') or {}).get('execution')):
                    if isinstance(holder, dict) and isinstance(holder.get('input_snapshot'), dict):
                        snapshot = holder['input_snapshot']
                        if snapshot.get('registry_sha256') != expected:
                            _fail('lock', 'Historical input snapshot disagrees with its read-only version lock')
                        from friskoli_cad.protocol.task_validation import sha256
                        if 'document_sha256' in snapshot and snapshot['document_sha256'] != sha256(submission):
                            _fail('lock', 'Historical frozen-input document hash disagrees with submission')
                        if 'scientific_sha256' in snapshot and all(k in submission for k in ('project', 'version_lock', 'execution', 'output_plan')):
                            if snapshot['scientific_sha256'] != sha256({k: submission[k] for k in ('project', 'version_lock', 'execution', 'output_plan')}):
                                _fail('lock', 'Historical scientific-input hash disagrees with submission')
    return data


def _project_science(project):
    value = deepcopy(project)
    value.pop('id', None)
    value.pop('random_seed', None)
    if isinstance(value.get('run'), dict):
        value['run'].pop('run_id', None)
    return value


def _run_reason(run, design, candidates):
    ref = run.get('design_ref') or {}
    if ref.get('design_id') != design['id']:
        return 'foreign_design'
    candidate = candidates.get(ref.get('candidate_id'))
    if candidate is None:
        return 'unknown_candidate'
    for holder in (run.get('task'), run.get('manifest'), (run.get('replay') or {}).get('execution')):
        if isinstance(holder, dict):
            complete = holder.get('completeness', (holder.get('result') or {}).get('completeness'))
            if holder.get('status', run.get('status')) != run.get('status') or (complete is not None and complete != run.get('completeness')):
                return 'inconsistent_result_status'
    submission = run.get('submission') or {}
    project = run.get('project')
    if not isinstance(project, dict) or not isinstance(submission.get('project'), dict):
        return 'missing_frozen_project'
    if _project_science(project) != _project_science(candidate['project']) or project != submission['project']:
        return 'project_mismatch'
    execution, output = submission.get('execution') or {}, submission.get('output_plan') or {}
    settings = design['settings']
    actual = run.get('settings') or {}
    for key in ('dt_s', 'steps'):
        if execution.get(key) != settings[key] or actual.get(key) != execution.get(key):
            return 'settings_mismatch'
    for key, default in (('frame_every_steps', 1), ('include_fields', True)):
        if output.get(key) != settings.get(key, default) or actual.get(key) != output.get(key):
            return 'settings_mismatch'
    if execution.get('backend', 'numpy-cpu') != settings.get('backend', 'numpy-cpu') or actual.get('backend', 'numpy-cpu') != execution.get('backend', 'numpy-cpu'):
        return 'settings_mismatch'
    for key, default in (('field_stride_xyz', [1, 1, 1]), ('include_final_fields', False)):
        if output.get(key, default) != settings.get(key, default) or actual.get(key, default) != output.get(key, default):
            return 'settings_mismatch'
    seed = execution.get('seed')
    if (type(seed) is not int or seed not in design['brief']['seeds']
            or actual.get('seed') != seed or project.get('random_seed') != seed):
        return 'seed_mismatch'
    lock = submission.get('version_lock')
    if not isinstance(lock, dict):
        return 'missing_version_lock'
    required = {(n['module_id'], n['module_version']) for n in project['graph']['nodes']}
    provided = {(n['id'], n['version']) for n in lock['implementations']}
    if not required <= provided:
        return 'missing_implementation_lock'
    channels = (project.get('run') or {}).get('channels')
    observables = output.get('observables')
    actual_observables = actual.get('observables')
    if (not isinstance(channels, dict) or not isinstance(observables, list)
            or any(type(value) is not str for value in observables)
            or len(set(observables)) != len(observables)
            or set(observables) != set(channels)
            or actual_observables != observables):
        return 'observation_channels_mismatch'
    # Even unsuccessful attempts must retain coherent frozen inputs and locks.
    # A later retry must not hide a malformed earlier record behind its status.
    if run.get('status') != 'completed' or run.get('completeness') != 'complete':
        return 'failed_partial_or_incomplete'
    snapshots = (run.get('replay') or {}).get('snapshots')
    if not isinstance(snapshots, list) or not snapshots or not isinstance(snapshots[-1], dict):
        return 'missing_result'
    final = snapshots[-1]
    frame = final.get('frame') or {}
    if not isinstance(frame, dict):
        return 'invalid_frame'
    if (frame.get('frame_index') != settings['steps'] or not _finite(frame.get('time_s'))
            or not math.isclose(frame['time_s'], settings['dt_s'] * settings['steps'], rel_tol=1e-10, abs_tol=1e-10)):
        return 'incomplete_endpoint'
    metrics = final.get('metrics') or {}
    if not isinstance(metrics, dict) or not isinstance(metrics.get('by_group'), dict):
        return 'missing_group_metrics'
    if metrics.get('metric_version') != '0.1.0':
        return 'metric_version_mismatch'
    if metrics.get('observation_id') != project.get('observation', {}).get('id', 'whole_domain'):
        return 'observation_mismatch'
    values = (metrics.get('by_group') or {}).get(design['brief']['goal']['group_id'])
    if not isinstance(values, dict):
        return 'missing_group_metrics'
    value = values.get(design['brief']['goal']['metric'])
    if value is None:
        return 'null_or_missing_metric'
    if not _finite(value):
        return 'invalid_metric'
    return None


def _report(data):
    design = data['design']
    candidates = {c['id']: c for c in design['candidates']}
    records = []
    for run in data['runs']:
        reason = _run_reason(run, design, candidates)
        submission = run.get('submission') or {}
        lock = submission.get('version_lock')
        # Whole lock, backend and contract are part of a repeat-series identity.
        series = _hash(_json({'lock': lock, 'backend': (submission.get('execution') or {}).get('backend'),
                              'contract': submission.get('task_contract_version')})) if lock else ''
        record = {'run_id': run.get('runId') or run.get('id') or run.get('localId') or '',
                  'design_id': (run.get('design_ref') or {}).get('design_id'),
                  'candidate_id': (run.get('design_ref') or {}).get('candidate_id', ''),
                  'seed': (submission.get('execution') or {}).get('seed'), 'series': series,
                  'reason': reason, 'value': None, 'version_lock': lock}
        if reason is None:
            record['value'] = run['replay']['snapshots'][-1]['metrics']['by_group'][design['brief']['goal']['group_id']][design['brief']['goal']['metric']]
        records.append(record)
    # Failed attempts remain visible but are not successful repeats. Count all
    # claims of complete success (including malformed ones); neither an invalid
    # successful claim nor two successful attempts can be hidden by a retry.
    repeated = Counter((r['candidate_id'], r['seed']) for r, run in zip(records, data['runs'], strict=True)
                       if r['design_id'] == design['id'] and r['candidate_id'] in candidates
                       and r['seed'] is not None and run.get('status') == 'completed'
                       and run.get('completeness') == 'complete')
    for row in records:
        if (row['design_id'] == design['id'] and row['candidate_id'] in candidates
                and row['seed'] is not None
                and repeated[row['candidate_id'], row['seed']] > 1):
            row['duplicate_seed'] = True
            # Keep the specific malformed-record reason rather than replacing
            # it with the less informative duplicate label.
            if row['reason'] is None:
                row['reason'], row['value'] = 'duplicate_seed', None
    grouped = defaultdict(list)
    for row in records:
        if row['reason'] is None:
            grouped[row['candidate_id'], row['series']].append(row)
    summaries = []
    for candidate in design['candidates']:
        keys = sorted(k for k in grouped if k[0] == candidate['id']) or [(candidate['id'], '')]
        for key in keys:
            rows = grouped[key]
            values = [r['value'] for r in rows]
            summaries.append({'candidate_id': candidate['id'], 'candidate_name': candidate['name'],
                'kind': candidate['kind'], 'series': key[1], 'n': len(values),
                'mean': statistics.mean(values) if values else None,
                'sample_sd': statistics.stdev(values) if len(values) >= 2 else None,
                'soft_penalty': candidate['soft_penalty'],
                'uncertainty': 'sample_sd' if len(values) >= 2 else 'insufficient_repeats',
                'seeds': [r['seed'] for r in rows]})
    return summaries, records


def _csv_cell(value):
    if value is None:
        return ''
    if isinstance(value, str) and value.lstrip(' \t\r\n').startswith(('=', '+', '-', '@')):
        return "'" + value
    if isinstance(value, str) and value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def _csv(data):
    summaries, records = _report(data)
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\r\n')
    goal = data['design']['brief']['goal']
    rows = [['record_type', 'design_id', 'candidate_id', 'candidate_name', 'kind', 'metric', 'group_id',
             'source_series_sha256', 'run_id', 'seed', 'n', 'mean', 'sample_sd', 'value', 'status_or_reason', 'version_lock_json', 'soft_penalty', 'frozen_input_json']]
    for summary in summaries:
        rows.append(['summary', data['design']['id'], summary['candidate_id'], summary['candidate_name'], summary['kind'],
            goal['metric'], goal['group_id'], summary['series'], '', '', summary['n'], summary['mean'],
            summary['sample_sd'], '', summary['uncertainty'], '', summary['soft_penalty'], ''])
    candidates = {c['id']: c for c in data['design']['candidates']}
    for row, original in zip(records, data['runs'], strict=True):
        candidate = candidates.get(row['candidate_id'], {})
        rows.append(['run', data['design']['id'], row['candidate_id'], candidate.get('name', ''), candidate.get('kind', ''),
            goal['metric'], goal['group_id'], row['series'], row['run_id'], row['seed'], '', '', '', row['value'],
            row['reason'] or 'included', _json(row['version_lock']).decode('utf-8'), candidate.get('soft_penalty'),
            _json({'project': original.get('project'), 'settings': original.get('settings'), 'submission': original.get('submission')}).decode('utf-8')])
    if data['design']['brief']['brief_version'] == '0.2.0':
        from friskoli_cad.design_evaluation import _evaluate
        evaluation = _evaluate(data)
        rows[0].append('evaluation_json')
        for row in rows[1:]:
            row.append('')
        rows.append(['evaluation', data['design']['id'], evaluation['recommended_candidate_id'], '', '',
                     goal['metric'], goal['group_id'], '', '', '', '', '', '', '', evaluation['status'], '', '', '',
                     _json(evaluation).decode('utf-8')])
        for item in evaluation['candidates']:
            rows.append(['candidate_evaluation', data['design']['id'], item['candidate_id'], item['name'], item['kind'],
                         goal['metric'], goal['group_id'], '', '', '', item['objective']['n'], item['objective']['mean'],
                         item['objective']['sample_sd'], '', item['status'], '', '', '', _json(item).decode('utf-8')])
    for row in rows:
        writer.writerow([_csv_cell(v) for v in row])
    return stream.getvalue()


def _html(data):
    summaries, records = _report(data)
    design = data['design']
    h = lambda value: escape('—' if value is None else str(value), quote=True)
    rows = ''.join('<tr>' + ''.join('<td>' + h(s[k]) + '</td>' for k in ('candidate_name', 'kind', 'series', 'n', 'mean', 'sample_sd', 'soft_penalty', 'uncertainty')) + '</tr>' for s in summaries)
    excluded = ''.join('<li>' + h(r['run_id']) + ': ' + h(r['reason']) + '</li>' for r in records if r['reason'])
    evidence = _json({'brief': design['brief'], 'baseline_project': design['baseline_project'],
                      'candidates': design['candidates'], 'excluded': design['excluded'], 'settings': design['settings'],
                      'budget': design['budget'], 'run_evidence': [{k: r.get(k) for k in ('id', 'design_ref', 'project', 'settings', 'submission', 'task', 'manifest')} for r in data['runs']]}).decode('utf-8')
    evaluation_html = ''
    disclaimer = '此报告不宣称实验性能或自动推荐。'
    if design['brief']['brief_version'] == '0.2.0':
        from friskoli_cad.design_evaluation import _evaluate
        evaluation = _evaluate(data)
        disclaimer = '推荐仅为探索性模拟排序，不代表统计显著性或实验性能。'
        evaluation_html = '<h2>结果约束与探索性推荐</h2><p>' + h(evaluation['status']) + ' · ' + h(evaluation['recommended_candidate_id']) + '</p><p>' + h(', '.join(evaluation['reasons'])) + '</p>'
        for item in evaluation['candidates']:
            comparison = item['control_comparison']
            evaluation_html += '<h3>' + h(item['name']) + ' · ' + h(item['candidate_id']) + '</h3><p>' + h(item['status']) + ' · ' + h(', '.join(item['reasons'])) + '</p>'
            if comparison:
                evaluation_html += '<p>相同 seed 的有向改善：N=' + h(comparison['n']) + '，均值=' + h(comparison['mean']) + '，样本 SD=' + h(comparison['sample_sd']) + '，范围=[' + h(comparison['minimum']) + ', ' + h(comparison['maximum']) + ']</p>'
            evaluation_html += '<ul>' + ''.join('<li>' + h(c['id']) + ' · ' + h(c['kind']) + ' · ' + h(c['metric']) + ' ' + h(c['operator']) + ' ' + h(c['value']) + ' · ' + h(c['status']) + ' · [' + h(c['minimum']) + ', ' + h(c['maximum']) + ']</li>' for c in item['constraints']) + '</ul>'
        evaluation_html += '<p>' + h(' '.join(evaluation['limitations'])) + '</p>'
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<title>' + h(design['brief']['name']) + '</title><style>body{font:16px system-ui,sans-serif;max-width:1200px;margin:40px auto;padding:0 20px;color:#20332c}table{border-collapse:collapse;width:100%}td,th{padding:9px;border:1px solid #ccd8d1;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}small{color:#526359}</style>'
        '<h1>' + h(design['brief']['name']) + '</h1><p>设计 ' + h(design['id']) + ' · 指标 ' + h(design['brief']['goal']['metric'])
        + ' · 菌群 ' + h(design['brief']['goal']['group_id']) + ' · 方向 ' + h(design['brief']['goal']['direction']) + '</p>'
        '<p>仅纳入完整运行、匹配冻结输入和观测、seed 唯一的结果。不同来源锁分别统计。N 为有效 seed 数；SD 使用样本标准差。N&lt;2 时无法估计重复间不确定性；空值不当作零。' + disclaimer + '</p>'
        '<p>soft_penalty 单独列出，不与目标指标相加或用于自动排名。</p><table><thead><tr><th>候选</th><th>类型</th><th>来源系列 SHA-256</th><th>N</th><th>均值</th><th>样本 SD</th><th>soft_penalty</th><th>不确定性</th></tr></thead><tbody>' + rows + '</tbody></table>'
        + evaluation_html + '<h2>未参与统计的运行</h2><ul>' + (excluded or '<li>无</li>') + '</ul>'
        '<h2>只读来源与运行输入</h2><p>历史锁不会因导入而替换，也不会自动执行。文件校验值只能证明包内一致性，不能证明作者身份。</p><details><summary>参数 provenance、约束、排除原因、完整冻结输入和锁</summary><pre>'
        + h(evidence) + '</pre></details></html>')


def design_report_html(payload) -> str:
    return _html(_validate(payload))


def design_report_csv(payload) -> str:
    return _csv(_validate(payload))


def _contents(data):
    locks = [{'run_id': r.get('runId') or r.get('id'), 'design_ref': r.get('design_ref'),
              'version_lock': (r.get('submission') or {}).get('version_lock')} for r in data['runs']]
    return {'design/package.json': _json(data), 'design/brief.json': _json(data['design']['brief']),
            'design/candidates.json': _json(data['design']['candidates']), 'evidence/runs.json': _json(data['runs']),
            'dependencies/registry.json': _json(data['registry']), 'dependencies/locks.json': _json(locks),
            'reports/design.html': _html(data).encode('utf-8'), 'reports/design.csv': _csv(data).encode('utf-8')}


def export_design_package(payload) -> bytes:
    data = _validate(payload)
    contents = _contents(data)
    if any(len(v) > MAX_MEMBER_BYTES for v in contents.values()) or sum(map(len, contents.values())) > MAX_TOTAL_BYTES:
        _fail('size', 'Native package exceeds the uncompressed size budget; no data was truncated')
    manifest = {'format': 'friskoli-native-design', 'package_version': VERSION, 'read_only_history': True,
                'files': {name: {'sha256': _hash(value), 'bytes': len(value)} for name, value in contents.items()}}
    contents['manifest.json'] = _json(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, value in sorted(contents.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, value)
    result = buffer.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        _fail('size', 'Native package exceeds the compressed size budget')
    return result


def import_design_package(zip_bytes) -> dict:
    if not isinstance(zip_bytes, (bytes, bytearray)) or len(zip_bytes) > MAX_ARCHIVE_BYTES:
        _fail('size', 'Expected a bounded .friskoli ZIP file')
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_MEMBERS or sum(i.file_size for i in entries) > MAX_TOTAL_BYTES:
                _fail('size', 'Archive exceeds entry or expansion budget')
            seen, contents = set(), {}
            for info in entries:
                name = info.filename
                path = PurePosixPath(name)
                if ('\\' in name or ':' in name or path.is_absolute() or '..' in path.parts
                        or str(path) != name or name.casefold() in seen or name not in _MEMBERS | {'manifest.json'}):
                    _fail('path', 'Unexpected, duplicate or unsafe archive path')
                seen.add(name.casefold())
                if (info.is_dir() or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
                        or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)):
                    _fail('archive', 'Directories, links, encryption and unsupported compression are not accepted')
                if info.file_size > MAX_MEMBER_BYTES:
                    _fail('size', 'Archive member exceeds expansion budget')
                with archive.open(info) as stream:
                    value = stream.read(MAX_MEMBER_BYTES + 1)
                if len(value) != info.file_size or len(value) > MAX_MEMBER_BYTES:
                    _fail('size', 'Archive member length mismatch')
                contents[name] = value
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError) as error:
        _fail('archive', f'Invalid native .friskoli package: {error}')
    if set(contents) != _MEMBERS | {'manifest.json'}:
        _fail('structure', 'Incomplete native design package')
    manifest = _object(_loads(contents.pop('manifest.json')), 'manifest')
    if manifest.get('format') != 'friskoli-native-design' or manifest.get('package_version') != VERSION or manifest.get('read_only_history') is not True:
        _fail('version', 'Unsupported native design manifest')
    declared = _object(manifest.get('files'), 'manifest.files')
    if set(declared) != set(contents):
        _fail('checksum', 'Manifest member list differs from archive')
    for name, value in contents.items():
        if declared[name] != {'sha256': _hash(value), 'bytes': len(value)}:
            _fail('checksum', 'Checksum or byte count differs for ' + name)
    data = _validate(_loads(contents['design/package.json']))
    expected = _contents(data)
    # The manifest checksum above still authenticates the report bytes as the
    # bytes that were shipped in this archive.  Do not regenerate and compare
    # them here: a package opened after a renderer update must retain its
    # historical report, while the JSON evidence and dependency files must
    # remain exactly cross-referenced with package.json.
    if any(contents[name] != expected[name] for name in _STRUCTURED_MEMBERS):
        _fail('cross_reference', 'Structured evidence or dependencies disagree with the preserved payload')
    return data
