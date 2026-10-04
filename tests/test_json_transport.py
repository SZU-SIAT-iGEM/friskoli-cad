import json, math, unittest
from pathlib import Path
from friskoli_cad.protocol.task_validation import TaskValidationError, strict_json_loads, canonical_bytes, canonical_loads, sha256, _validator

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


