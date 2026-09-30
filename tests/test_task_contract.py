"""N1 document contract checks; these do not exercise an async task service."""
from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import re
import unittest
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / 'src/friskoli_cad/protocol/schemas'
TASK = json.loads((SCHEMAS / 'task-draft.schema.json').read_text(encoding='utf-8'))
# Keep the N1 frozen specification under test after the stable N2 release.
API_PATH = ROOT / 'docs/protocol/tasks-openapi-draft.json'
API = json.loads(API_PATH.read_text(encoding='utf-8'))
VERSION = '0.1.0-draft.1'
FORMATS = FormatChecker()


@FORMATS.checks('date-time', raises=(ValueError, TypeError))
def rfc3339_datetime(value):
    # jsonschema's optional RFC3339 dependency is not required by this project.
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}'
                        r'(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})', value):
        return False
    value = value.upper().replace('Z', '+00:00')
    # RFC3339 allows an announced leap second; calendar dates still validate.
    if value[17:19] == '60':
        value = value[:17] + '59' + value[19:]
    return datetime.fromisoformat(value).tzinfo is not None


def registry():
    result = Registry()
    for path in SCHEMAS.glob('*.json'):
        schema = json.loads(path.read_text(encoding='utf-8'))
        resource = Resource.from_contents(schema)
        result = result.with_resource(schema['$id'], resource)
        result = result.with_resource(urljoin(TASK['$id'], path.name), resource)
    return result


def validator(name='Task'):
    return Draft202012Validator(
        {'$ref': TASK['$id'] + '#/$defs/' + name},
        registry=registry(), format_checker=FORMATS,
    )


class TaskContractTests(unittest.TestCase):
    def test_schema_and_all_documented_examples(self):
        Draft202012Validator.check_schema(TASK)
        self.assertEqual({e['status'] for e in TASK['examples']},
                         {'completed', 'failed', 'cancelled'})
        for example in TASK['examples']:
            with self.subTest(status=example['status']):
                validator().validate(example)

    def test_terminal_matrix_is_closed_and_no_resume(self):
        matrix = TASK['x-state-transitions']
        self.assertEqual(set(matrix), {'queued', 'running', 'completed',
                                      'failed', 'cancelled', 'interrupted'})
        for state in ('completed', 'failed', 'cancelled', 'interrupted'):
            self.assertEqual(matrix[state], [])
        self.assertEqual(set(matrix['running']),
                         {'completed', 'failed', 'cancelled', 'interrupted'})
        self.assertEqual(set(matrix['queued']),
                         {'running', 'failed', 'cancelled', 'interrupted'})
        for state in ('queued', 'running', 'interrupted'):
            record = copy.deepcopy(TASK['examples'][1])
            record['status'] = state
            if state != 'interrupted':
                record['finished_at'] = None
                record['issues'] = []
            if state == 'queued':
                record['result']['completeness'] = 'none'
                record['progress'] = {'committed_step': 0, 'simulation_time_s': 0}
            validator().validate(record)
        properties = TASK['$defs']['TaskCapabilities']['properties']
        for capability in ('pause', 'resume', 'checkpoint'):
            self.assertIs(properties[capability]['const'], False)

    def test_invalid_status_completeness_and_terminal_fields(self):
        for status, completeness in [('completed', 'partial'), ('completed', 'none'),
                                     ('failed', 'complete'), ('cancelled', 'complete'),
                                     ('interrupted', 'complete'), ('running', 'complete')]:
            with self.subTest(status=status, completeness=completeness):
                record = copy.deepcopy(TASK['examples'][1])
                record['status'] = status
                record['result']['completeness'] = completeness
                if status == 'completed':
                    record['issues'] = []
                if status == 'running':
                    record['finished_at'] = None
                if status == 'cancelled':
                    record['cancel_requested'] = True
                self.assertFalse(validator().is_valid(record))
        for field, value in [('status', 'cancelling'), ('status', 'paused'),
                             ('task_contract_version', '0.1.0'),
                             ('finished_at', None), ('updated_at', 'yesterday')]:
            record = copy.deepcopy(TASK['examples'][0])
            record[field] = value
            self.assertFalse(validator().is_valid(record), (field, value))
        record = copy.deepcopy(TASK['examples'][2])
        record['cancel_requested'] = False
        self.assertFalse(validator().is_valid(record))
        record = copy.deepcopy(TASK['examples'][1])
        record['issues'] = []
        self.assertFalse(validator().is_valid(record))
        # Cancellation may lose the terminal race to complete publication.
        record = copy.deepcopy(TASK['examples'][0])
        record['cancel_requested'] = True
        validator().validate(record)

    def test_input_snapshot_and_error_location_validation(self):
        for key, bad in [('document_sha256', 'xyz'),
                         ('href', '/tmp/arbitrary/input'),
                         ('canonicalization', 'python-json')]:
            record = copy.deepcopy(TASK['examples'][0])
            record['input_snapshot'][key] = bad
            self.assertFalse(validator().is_valid(record))
        record = copy.deepcopy(TASK['examples'][0])
        record['checkpoint'] = '/tmp/x'
        self.assertFalse(validator().is_valid(record))
        issue = copy.deepcopy(TASK['examples'][1]['issues'][0])
        for pointer in ('', '/project/groups/a~1b', '/project/groups/a~0b'):
            issue['path'] = pointer
            validator('Issue').validate(issue)
        for pointer in ('project/groups', '/bad~2escape'):
            issue['path'] = pointer
            self.assertFalse(validator('Issue').is_valid(issue))

    def test_submission_uses_actual_project_schema(self):
        project = json.loads((ROOT / 'src/friskoli_cad/examples/workspace_3d.project.json')
                             .read_text(encoding='utf-8'))
        submission = {
            'task_contract_version': VERSION, 'request_id': 'request-1',
            'project': project, 'edit_revision': 'edit-1',
            'version_lock': {'registry_sha256': 'a' * 64, 'implementations': [
                {'id': 'example', 'version': '0.1.0', 'sha256': 'b' * 64}]},
            'execution': {'semantics': 'legacy-explicit-v1', 'backend': 'cpu.numpy',
                          'dt_s': 0.1, 'steps': 10, 'seed': 1},
            'output_plan': {'frame_every_steps': 1, 'observables': [],
                            'include_fields': False},
        }
        validator('Submission').validate(submission)
        for field, value in [('dt_s', 0), ('steps', 0), ('seed', -1), ('steps', 1.5)]:
            changed = copy.deepcopy(submission)
            changed['execution'][field] = value
            self.assertFalse(validator('Submission').is_valid(changed))
        changed = copy.deepcopy(submission)
        changed['project']['project_version'] = '99.0.0'
        self.assertFalse(validator('Submission').is_valid(changed))
        changed = copy.deepcopy(submission)
        del changed['project']['domain']
        self.assertFalse(validator('Submission').is_valid(changed))
        changed = copy.deepcopy(submission)
        changed['output_plan']['include_fields'] = True
        self.assertFalse(validator('Submission').is_valid(changed))

    def test_event_and_manifest_examples(self):
        task = copy.deepcopy(TASK['examples'][0])
        event = {'seq': task['last_event_seq'], 'type': 'completed',
                 'created_at': task['updated_at'], 'task': task}
        page = {'run_id': task['run_id'], 'events': [event], 'next_after': 4,
                'has_more': False, 'latest_seq': 4}
        validator('EventPage').validate(page)
        event['type'] = 'failed'
        self.assertFalse(validator('EventPage').is_valid(page))
        event['type'] = 'completed'
        event['seq'] = 0
        self.assertFalse(validator('EventPage').is_valid(page))
        page['events'] = []
        validator('EventPage').validate(page)
        manifest = {'task_contract_version': VERSION, 'run_id': task['run_id'],
                    'status': 'completed', 'completeness': 'complete',
                    'input_snapshot': task['input_snapshot'], 'progress': task['progress'],
                    'issues': [], 'chunks': [{'chunk_id': 'chunk_1',
                        'href': '/api/runs/run_example/chunks/chunk_1',
                        'sha256': 'a' * 64, 'bytes': 128, 'media_type': 'application/json',
                        'first_step': 0, 'last_step': 10}]}
        validator('Manifest').validate(manifest)
        manifest['status'] = 'failed'
        self.assertFalse(validator('Manifest').is_valid(manifest))
        manifest['completeness'] = 'partial'
        manifest['issues'] = copy.deepcopy(TASK['examples'][1]['issues'])
        validator('Manifest').validate(manifest)
        manifest['chunks'] = []
        self.assertFalse(validator('Manifest').is_valid(manifest))

    def test_frame_chunk_references_legacy_frame_schema(self):
        frame = json.loads((ROOT / 'examples/protocol/frames.json').read_text(encoding='utf-8'))[0]
        chunk = {'task_contract_version': VERSION, 'run_id': 'run_example',
                 'chunk_id': 'chunk_1', 'frames': [{
                     'sequence': 0, 'step_index': 0, 'time_s': 0,
                     'grid_revision': 'grid-1', 'frame': frame}]}
        validator('ChunkBody').validate(chunk)
        frame['protocol_version'] = '99.0.0'
        self.assertFalse(validator('ChunkBody').is_valid(chunk))

    def test_published_resource_bounds_are_positive_and_single_worker(self):
        limits = {name: 1 for name in TASK['$defs']['Limits']['properties']}
        validator('Limits').validate(limits)
        for field in limits:
            changed = dict(limits, **{field: 0})
            self.assertFalse(validator('Limits').is_valid(changed), field)
        changed = dict(limits, active_runs=2)
        self.assertFalse(validator('Limits').is_valid(changed))
        del limits['estimated_memory_bytes']
        self.assertFalse(validator('Limits').is_valid(limits))

    def test_openapi_references_status_codes_and_draft_boundary(self):
        self.assertEqual(API['openapi'], '3.1.0')
        self.assertEqual(API['info']['version'], VERSION)
        self.assertEqual(API['x-implementation-status'], 'draft-not-implemented')
        refs = []

        def walk(value):
            if isinstance(value, dict):
                if '$ref' in value:
                    refs.append(value['$ref'])
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(API)
        self.assertTrue(refs)
        for reference in refs:
            filename, fragment = reference.split('#', 1)
            document = json.loads((API_PATH.parent / filename).read_text(encoding='utf-8'))
            for part in fragment.lstrip('/').split('/'):
                document = document[part.replace('~1', '/').replace('~0', '~')]
            Draft202012Validator.check_schema(document)
        ids = []
        for path, item in API['paths'].items():
            self.assertTrue(path.startswith('/api/runs'))
            for method in ('get', 'post'):
                if method in item:
                    operation = item[method]
                    ids.append(operation['operationId'])
                    self.assertEqual(operation['x-implementation-status'], 'draft-not-implemented')
                    self.assertTrue(operation['responses'])
        self.assertEqual(len(ids), len(set(ids)))
        submit = API['paths']['/api/runs']['post']
        self.assertTrue({'200', '202', '400', '409', '413', '422', '429', '503'}
                        <= set(submit['responses']))
        self.assertEqual(submit['parameters'][0]['name'], 'Idempotency-Key')
        self.assertTrue(submit['parameters'][0]['required'])
        events = API['paths']['/api/runs/{id}/events']['get']
        self.assertIn('410', events['responses'])
        self.assertEqual(events['parameters'][0]['name'], 'after')
        current = json.loads((ROOT / 'docs/openapi.json').read_text(encoding='utf-8'))
        self.assertIn('/api/replay', current['paths'])
        self.assertNotIn('/api/replay', API['paths'])


if __name__ == '__main__':
    unittest.main()
