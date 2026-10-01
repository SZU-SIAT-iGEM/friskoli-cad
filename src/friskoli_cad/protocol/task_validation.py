"""Strict I-JSON parsing and RFC 8785 hashing for task contract 0.1.0.

These helpers check transport and structural validity. Runtime admission still
resolves exact implementation locks, project semantics and resource limits.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import hashlib
from importlib.resources import files
import json
import math
import re
from typing import Any
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
import rfc8785

VERSION = "0.1.0"
MAX_SAFE_INTEGER = (1 << 53) - 1
_SCHEMA_NAME = "task-v0.1.schema.json"
_FORMATS = FormatChecker()


@_FORMATS.checks("date-time", raises=(ValueError, TypeError))
def _rfc3339_datetime(value: Any) -> bool:
    # Keep timestamp validation active without jsonschema's optional extras.
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}"
                        r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})", value):
        return False
    value = value.upper().replace("Z", "+00:00")
    if value[17:19] == "60":
        value = value[:17] + "59" + value[19:]
    return datetime.fromisoformat(value).tzinfo is not None


class TaskValidationError(ValueError):
    """A request rejection containing stable, JSON-serializable issue records."""

    def __init__(self, issues: list[dict[str, Any]]):
        self.issues = issues
        super().__init__("; ".join(issue["message"] for issue in issues))


def _fail(code: str, message: str, path: str = "", *, phase: str = "request") -> None:
    raise TaskValidationError([{"code": code, "severity": "error",
        "phase": phase, "path": path, "targets": [], "message": message}])


def _pointer(parts) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1")
                   for part in parts)


def _string(value: str, path: str) -> None:
    # RFC 7493 section 2.1 excludes both lone surrogates and noncharacters.
    for character in value:
        point = ord(character)
        if 0xD800 <= point <= 0xDFFF or 0xFDD0 <= point <= 0xFDEF or (point & 0xFFFF) in (0xFFFE, 0xFFFF):
            _fail("task.json_unicode", "JSON strings must contain valid I-JSON Unicode characters.", path)


def _check(value: Any, path: str = "", active: set[int] | None = None) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        _string(value, path)
        return
    if type(value) is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            _fail("task.json_number_range", "Integer is outside the interoperable IEEE-754 safe range.", path)
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail("task.json_number_range", "JSON numbers must be finite IEEE-754 binary64 values.", path)
        return
    if type(value) not in (dict, list):
        _fail("task.json_type", "Value is not a JSON type.", path)
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        _fail("task.json_type", "JSON values cannot contain cycles.", path)
    active.add(identity)
    try:
        if type(value) is dict:
            for key, child in value.items():
                if type(key) is not str:
                    _fail("task.json_type", "JSON object member names must be strings.", path)
                child_path = path + _pointer((key,))
                _string(key, child_path)
                _check(child, child_path, active)
        else:
            for index, child in enumerate(value):
                _check(child, path + _pointer((index,)), active)
    finally:
        active.remove(identity)


class _ObjectPairs(list):
    pass


def _materialize(value: Any, path: str = "") -> Any:
    if isinstance(value, _ObjectPairs):
        result = {}
        for key, child in value:
            child_path = path + _pointer((key,))
            if key in result:
                _fail("task.json_duplicate_member", "Duplicate JSON object member.", child_path)
            _string(key, child_path)
            result[key] = _materialize(child, child_path)
        return result
    if type(value) is list:
        return [_materialize(child, path + _pointer((index,)))
                for index, child in enumerate(value)]
    _check(value, path)
    return value


def _parse_integer(token: str) -> int:
    try:
        value = int(token)
    except ValueError:
        _fail("task.json_number_range", "Integer is outside the interoperable IEEE-754 safe range.")
    _check(value)
    return value


def _parse_float(token: str) -> float:
    value = float(token)
    _check(value)
    if value == 0.0 and any(c in "123456789" for c in token.lower().split("e", 1)[0]):
        _fail("task.json_number_range", "Nonzero number underflows IEEE-754 binary64.")
    return value


def _parse_constant(token: str) -> None:
    _fail("task.json_number_range", "NaN and Infinity are not JSON numbers.")


def strict_json_loads(data: bytes | str) -> Any:
    """Decode UTF-8 JSON without duplicate members or I-JSON data loss.

    Integer tokens are restricted to +/- (2**53 - 1). Fractional/exponent
    tokens use binary64 rounding, as required by JCS; overflow and nonzero
    underflow are rejected. Unicode is never normalized.
    """
    return _json_loads(data, _parse_integer)


def _json_loads(data: bytes | str, parse_integer) -> Any:
    if type(data) is bytes:
        try:
            data = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _fail("task.json_unicode", "Request body must be valid UTF-8.")
    if type(data) is not str:
        _fail("task.json_type", "JSON input must be bytes or a string.")
    try:
        decoded = json.loads(data, object_pairs_hook=_ObjectPairs,
                             parse_int=parse_integer, parse_float=_parse_float,
                             parse_constant=_parse_constant)
        return _materialize(decoded)
    except json.JSONDecodeError as exc:
        _fail("task.json_invalid", f"Malformed JSON at line {exc.lineno}, column {exc.colno}.")
    except RecursionError:
        _fail("task.json_depth", "JSON nesting exceeds the supported parser depth.")


def canonical_loads(data: bytes | str) -> Any:
    """Read internally generated JCS bytes while preserving binary64 numbers.

    JCS emits some finite binary64 values as integer tokens outside the safe
    integer range. Restore those as floats and verify exact re-encoding. This
    is for persisted/worker data only; external input uses strict_json_loads.
    """
    def parse_integer(token):
        try:
            value = int(token)
        except ValueError:
            _fail("task.json_number_range", "Stored integer exceeds the supported range.")
        return value if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else _parse_float(token)

    value = _json_loads(data, parse_integer)
    raw = data.encode("utf-8") if type(data) is str else data
    if canonical_bytes(value) != raw:
        _fail("task.json_canonicalization", "Stored JSON is not canonical RFC 8785.")
    return value


def canonical_bytes(obj: Any) -> bytes:
    """Return genuine RFC 8785 UTF-8 bytes; never a sort_keys substitute."""
    try:
        _check(obj)
        return rfc8785.dumps(obj)
    except RecursionError:
        _fail("task.json_depth", "JSON nesting exceeds the supported parser depth.")
    except rfc8785.CanonicalizationError:
        _fail("task.json_canonicalization", "Value cannot be represented by RFC 8785.")


def sha256(obj: Any) -> str:
    """SHA-256 hex digest of canonical_bytes(obj), including its full content."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


@lru_cache(maxsize=16)
def _validator(definition: str, version: str = VERSION) -> Draft202012Validator:
    folder = files("friskoli_cad.protocol").joinpath("schemas")
    schema_name = {VERSION: _SCHEMA_NAME, "0.2.0": "task-v0.2.schema.json", "0.3.0": "task-v0.3.schema.json", "0.4.0": "task-v0.4.schema.json"}.get(version)
    if schema_name is None:
        _fail("task.version", "Unsupported task contract version.", "/task_contract_version")
    schema = json.loads(folder.joinpath(schema_name).read_text(encoding="utf-8"))
    registry = Registry()
    for path in folder.iterdir():
        if path.name.endswith(".schema.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            resource = Resource.from_contents(document)
            registry = registry.with_resource(document["$id"], resource)
            registry = registry.with_resource(urljoin(schema["$id"], path.name), resource)
    return Draft202012Validator({"$ref": schema["$id"] + "#/$defs/" + definition},
                                registry=registry, format_checker=_FORMATS)


def validate_submission(obj: Any) -> None:
    """Validate stable Submission structure; runtime semantic checks follow."""
    canonical_bytes(obj)
    version = obj.get("task_contract_version", VERSION) if isinstance(obj, dict) else VERSION
    errors = sorted(_validator("Submission", version).iter_errors(obj),
                    key=lambda error: (_pointer(error.absolute_path), error.message))
    if errors:
        raise TaskValidationError([{"code": "task.schema_invalid",
            "severity": "error", "phase": "validate",
            "path": _pointer(error.absolute_path), "targets": [],
            "message": error.message} for error in errors])
