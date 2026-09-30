"""Stable task schema and RFC 8785 / I-JSON transport regressions.

Official vectors: RFC 8785, sections 3.2.2 and 3.2.3, Appendix B:
https://www.rfc-editor.org/rfc/rfc8785.html
I-JSON constraints: https://www.rfc-editor.org/rfc/rfc7493.html#section-2
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
import unittest

from jsonschema import Draft202012Validator
from friskoli_cad.protocol.task_validation import (
    VERSION, TaskValidationError, canonical_bytes, canonical_loads, sha256, strict_json_loads,
    validate_submission, _validator,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / 'src/friskoli_cad/protocol/schemas/task-v0.1.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
API_PATH = ROOT / 'docs/protocol/tasks-openapi.json'
API = json.loads(API_PATH.read_text(encoding='utf-8'))


def submission():
    return {
        'task_contract_version': VERSION, 'request_id': 'request-1',
        'project': json.loads((ROOT / 'src/friskoli_cad/examples/workspace_3d.project.json').read_text(encoding='utf-8')),
        'edit_revision': 'edit-1',
        'version_lock': {'registry_sha256': 'a' * 64, 'implementations': [
            {'id': 'example', 'version': '0.1.0', 'sha256': 'b' * 64}]},
        'execution': {'semantics': 'legacy-explicit-v1', 'backend': 'numpy-cpu',
                      'dt_s': 0.1, 'steps': 10, 'seed': 1},
        'output_plan': {'frame_every_steps': 1, 'observables': [], 'include_fields': False},
    }


class StrictJsonTests(unittest.TestCase):
    def rejection(self, value, code=None, path=None):
        with self.assertRaises(TaskValidationError) as caught:
            strict_json_loads(value)
        issue = caught.exception.issues[0]
        _validator('Issue').validate(issue)
        if code is not None: self.assertEqual(issue['code'], code)
        if path is not None: self.assertEqual(issue['path'], path)
        return issue

    def test_nested_duplicate_and_escaped_names_are_not_silently_replaced(self):
        self.rejection('{"a":1,"a":2}', 'task.json_duplicate_member', '/a')
        self.rejection(r'{"a":1,"\u0061":2}', 'task.json_duplicate_member', '/a')
        self.rejection('{"items":[{"a/b~c":1,"a/b~c":2}]}',
                       'task.json_duplicate_member', '/items/0/a~1b~0c')
        self.assertEqual(strict_json_loads('{"a":{"x":1},"b":{"x":2}}'),
                         {'a': {'x': 1}, 'b': {'x': 2}})

    def test_finite_numbers_safe_integer_limits_and_underflow(self):
        for token in ('NaN', 'Infinity', '-Infinity', '1e309', '-1e309',
                      '1e-324', '-1e-9999', '9007199254740992', '-9007199254740992'):
            with self.subTest(token=token):
                self.rejection(token, 'task.json_number_range')
        for token, expected in [('9007199254740991', 9007199254740991),
                                ('-9007199254740991', -9007199254740991),
                                ('5e-324', 5e-324), ('1e30', 1e30),
                                ('0e-9999', 0.0), ('-0.0', -0.0)]:
            self.assertEqual(strict_json_loads(token), expected)
        # Huge integer tokens also produce our structured error, not Python's limit error.
        self.rejection('9' * 5000, 'task.json_number_range')
        self.rejection('1e-' + '9' * 5000, 'task.json_number_range')
        self.assertEqual(strict_json_loads('0e-' + '9' * 5000), 0.0)

    def test_unicode_utf8_and_no_normalization(self):
        for raw in (b'"\xff"', b'"\xed\xa0\x80"', r'"\ud800"', r'"\udfff"',
                    r'"\ufdd0"', r'"\uffff"', r'"\udbff\udfff"'):
            with self.subTest(raw=raw): self.rejection(raw, 'task.json_unicode')
        self.assertEqual(strict_json_loads(r'"\ud83d\ude00"'), '\U0001f600')
        self.assertEqual(strict_json_loads('"é"'.encode()), 'é')
        self.assertEqual(strict_json_loads(r'"e\u0301"'), 'e\u0301')
        self.assertNotEqual(sha256('é'), sha256('e\u0301'))

    def test_malformed_json_types_and_depth_are_structured(self):
        for raw in ('', '{', '{"x":1,}', '"bad\nline"', '\ufeff{}', '{} trailing'):
            with self.subTest(raw=raw): self.rejection(raw, 'task.json_invalid')
        self.rejection(42, 'task.json_type')
        self.rejection('[' * 2000 + '0' + ']' * 2000, 'task.json_depth')


class CanonicalizationTests(unittest.TestCase):
    def test_official_rfc8785_appendix_b_number_vectors(self):
        # Exact binary64 encodings from RFC 8785 Appendix B (not decimal approximations).
        vectors = {
            '0000000000000000': '0', '8000000000000000': '0',
            '0000000000000001': '5e-324', '8000000000000001': '-5e-324',
            '7fefffffffffffff': '1.7976931348623157e+308',
            'ffefffffffffffff': '-1.7976931348623157e+308',
            '4340000000000000': '9007199254740992',
            'c340000000000000': '-9007199254740992',
            '4430000000000000': '295147905179352830000',
            '44b52d02c7e14af5': '9.999999999999997e+22',
            '44b52d02c7e14af6': '1e+23',
            '44b52d02c7e14af7': '1.0000000000000001e+23',
            '444b1ae4d6e2ef4e': '999999999999999700000',
            '444b1ae4d6e2ef4f': '999999999999999900000',
            '444b1ae4d6e2ef50': '1e+21',
            '3eb0c6f7a0b5ed8c': '9.999999999999997e-7',
            '3eb0c6f7a0b5ed8d': '0.000001',
            '41b3de4355555553': '333333333.3333332',
            '41b3de4355555554': '333333333.33333325',
            '41b3de4355555555': '333333333.3333333',
            '41b3de4355555556': '333333333.3333334',
            '41b3de4355555557': '333333333.33333343',
            'becbf647612f3696': '-0.0000033333333333333333',
            '43143ff3c1cb0959': '1424953923781206.2',
        }
        for bits, expected in vectors.items():
            with self.subTest(bits=bits):
                number = struct.unpack('>d', bytes.fromhex(bits))[0]
                self.assertEqual(canonical_bytes(number), expected.encode('ascii'))
                self.assertEqual(canonical_bytes(canonical_loads(expected)), expected.encode('ascii'))
        for bits in ('7fffffffffffffff', '7ff0000000000000'):
            with self.assertRaises(TaskValidationError):
                canonical_bytes(struct.unpack('>d', bytes.fromhex(bits))[0])

    def test_canonical_storage_preserves_large_floats_without_weakening_transport(self):
        value = {'values': [1e16, -1e16, 1e20, 1e30, 5e-324]}
        encoded = canonical_bytes(value)
        self.assertEqual(canonical_loads(encoded), value)
        self.assertEqual(canonical_bytes(canonical_loads(encoded)), encoded)
        with self.assertRaises(TaskValidationError):
            strict_json_loads(encoded)
        for invalid in (b'{"value":10000000000000001}', b'{"x":1,"x":1}', b'{"x": 1}', b'1e16'):
            with self.subTest(invalid=invalid), self.assertRaises(TaskValidationError):
                canonical_loads(invalid)

    def test_official_rfc8785_utf16_property_order(self):
        original = {'\u20ac': 'Euro Sign', '\r': 'Carriage Return',
                    '\ufb33': 'Hebrew Letter Dalet With Dagesh',
                    '1': 'One', '\U0001f600': 'Emoji: Grinning Face',
                    '\u0080': 'Control', '\u00f6': 'Latin Small Letter O With Diaeresis'}
        ordered = json.loads(canonical_bytes(original))
        self.assertEqual(list(ordered), ['\r', '1', '\u0080', '\u00f6', '\u20ac', '\U0001f600', '\ufb33'])
        # Python sort_keys orders Unicode code points rather than UTF-16 units.
        self.assertNotEqual(canonical_bytes(original),
                            json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())

    def test_jcs_strings_negative_zero_and_recursive_arrays(self):
        self.assertEqual(canonical_bytes({'b': -0.0, 'a': [1.0, True, False, None]}),
                         b'{"a":[1,true,false,null],"b":0}')
        self.assertEqual(canonical_bytes('\b\t\n\f\r"\\/'), b'"\\b\\t\\n\\f\\r\\"\\\\/"')
        self.assertEqual(canonical_bytes({'a': [{'z': 1, 'a': 2}]}), b'{"a":[{"a":2,"z":1}]}')

    def test_hash_object_order_array_order_missing_and_null(self):
        left = {'z': 1.0, 'a': [1, 2]}
        right = {'a': [1, 2], 'z': 1}
        self.assertEqual(sha256(left), sha256(right))
        self.assertEqual(sha256(left), hashlib.sha256(canonical_bytes(left)).hexdigest())
        self.assertNotEqual(sha256(left), sha256({'z': 1, 'a': [2, 1]}))
        self.assertNotEqual(sha256({}), sha256({'a': None}))

    def test_non_json_python_values_rejected_before_hashing(self):
        circular = []; circular.append(circular)
        for value in ((1, 2), {1: 'x'}, float('nan'), float('inf'),
                      9007199254740992, '\ud800', '\ufffe', circular):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(TaskValidationError): canonical_bytes(value)


class StableContractTests(unittest.TestCase):
    def test_stable_schema_and_examples(self):
        self.assertEqual(VERSION, '0.1.0')
        bad_timestamp = copy.deepcopy(SCHEMA['examples'][0])
        bad_timestamp['updated_at'] = '2026-02-30T00:00:00Z'
        self.assertFalse(_validator('Task').is_valid(bad_timestamp))
        Draft202012Validator.check_schema(SCHEMA)
        for example in SCHEMA['examples']: _validator('Task').validate(example)
        self.assertEqual(SCHEMA['x-state-transitions']['completed'], [])
        self.assertEqual(set(SCHEMA['x-state-transitions']['running']),
                         {'completed', 'cancelled', 'failed', 'interrupted'})

    def test_submission_uses_real_project_schemas_and_rejects_unknowns(self):
        document = submission()
        self.assertIsNone(validate_submission(document))
        numeric_equivalent = copy.deepcopy(document)
        numeric_equivalent['execution']['steps'] = 10.0
        self.assertIsNone(validate_submission(numeric_equivalent))
        self.assertEqual(sha256(document), sha256(numeric_equivalent))
        for pointer, mutate in [
            ('/task_contract_version', lambda d: d.update(task_contract_version='0.1.0-draft.1')),
            ('/execution/steps', lambda d: d['execution'].update(steps=0)),
            ('/project', lambda d: d['project'].pop('domain')),
            ('/project', lambda d: d['project'].update(project_version='99.0.0')),
            ('/output_plan/include_fields', lambda d: d['output_plan'].update(include_fields=True)),
            ('', lambda d: d.update(unknown=True)),
        ]:
            invalid = copy.deepcopy(document); mutate(invalid)
            with self.assertRaises(TaskValidationError) as caught: validate_submission(invalid)
            self.assertIn(pointer, [issue['path'] for issue in caught.exception.issues])
            for issue in caught.exception.issues: _validator('Issue').validate(issue)

    def test_terminal_completeness_remains_closed(self):
        for status, completeness in [('completed', 'partial'), ('completed', 'none'),
                                     ('failed', 'complete'), ('cancelled', 'complete'),
                                     ('interrupted', 'complete'), ('running', 'complete')]:
            document = copy.deepcopy(SCHEMA['examples'][0])
            document['status'] = status; document['result']['completeness'] = completeness
            self.assertFalse(_validator('Task').is_valid(document), (status, completeness))

    def test_real_backend_plan_provenance_and_exact_digests(self):
        from friskoli_cad.tasks.metadata import compiled_plan, provenance, registry_metadata
        registry, lock, sources = registry_metadata()
        project = submission()['project']
        plan = compiled_plan(project, registry)
        environment = provenance(7, sources)
        _validator('CompiledPlan').validate(plan)
        _validator('Provenance').validate(environment)
        _validator('Lock').validate(lock)
        self.assertEqual(lock['registry_sha256'], sha256(
            {'manifests': list(registry.manifests), 'catalog': registry.catalog}))
        for implementation in lock['implementations']:
            self.assertEqual(implementation['sha256'], sha256(
                {'id': implementation['id'], 'version': implementation['version'], 'sources': sources}))
        self.assertTrue(all('/' in name or name.endswith('.py') for name in sources))
        self.assertEqual(environment['rng'], {'algorithm': 'none-deterministic', 'seed': 7, 'used': False})
        # Full plan bytes are sensitive to execution order and parameter changes.
        reordered = copy.deepcopy(plan); reordered['nodes'].reverse()
        self.assertNotEqual(sha256(plan), sha256(reordered))
        changed = copy.deepcopy(plan); changed['plan_version'] = 'future'
        self.assertFalse(_validator('CompiledPlan').is_valid(changed))
        changed = copy.deepcopy(environment); changed['precision'] = 'float32'
        self.assertFalse(_validator('Provenance').is_valid(changed))
        changed = copy.deepcopy(environment); changed['source_sha256']['../arbitrary.py'] = 'a' * 64
        self.assertFalse(_validator('Provenance').is_valid(changed))
        task = SCHEMA['examples'][0]
        manifest = {'task_contract_version': VERSION, 'run_id': task['run_id'],
            'status': 'completed', 'completeness': 'complete',
            'input_snapshot': task['input_snapshot'], 'progress': task['progress'],
            'chunks': [{'chunk_id': 'chunk_1', 'href': '/api/runs/run_example/chunks/chunk_1',
                        'sha256': 'a' * 64, 'bytes': 128, 'media_type': 'application/json',
                        'first_step': 0, 'last_step': 10}], 'issues': [],
            'compiled_plan': plan, 'provenance': environment}
        _validator('Manifest').validate(manifest)
        for field in ('compiled_plan', 'provenance'):
            changed = copy.deepcopy(manifest); del changed[field]
            self.assertFalse(_validator('Manifest').is_valid(changed))

    def test_openapi_stable_references_and_historical_freeze(self):
        self.assertEqual(API['info']['version'], VERSION)
        self.assertEqual(API['x-implementation-status'], 'implemented')
        def walk(value):
            if isinstance(value, dict):
                if '$ref' in value: yield value['$ref']
                for child in value.values(): yield from walk(child)
            elif isinstance(value, list):
                for child in value: yield from walk(child)
        refs = list(walk(API)); self.assertTrue(refs)
        for reference in refs:
            name, fragment = reference.split('#', 1)
            schema = json.loads((API_PATH.parent / name).read_text(encoding='utf-8'))
            for part in fragment.lstrip('/').split('/'):
                schema = schema[part.replace('~1', '/').replace('~0', '~')]
            Draft202012Validator.check_schema(schema)
            self.assertNotIn('draft', reference)
        historical = json.loads((API_PATH.parent / 'tasks-openapi-draft.json').read_text(encoding='utf-8'))
        self.assertEqual(historical['info']['version'], '0.1.0-draft.1')
        self.assertEqual(historical['x-implementation-status'], 'draft-not-implemented')
        self.assertEqual(set(API['paths']), set(historical['paths']))
        capabilities = SCHEMA['$defs']['TaskCapabilities']
        self.assertIn('version_lock', capabilities['required'])
        self.assertIn('execution', capabilities['required'])
        self.assertIn('415', API['paths']['/api/runs']['post']['responses'])


if __name__ == '__main__':
    unittest.main()
