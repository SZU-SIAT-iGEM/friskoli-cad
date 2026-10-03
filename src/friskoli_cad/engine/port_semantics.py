"""Resolved port identity and runtime value checks for module protocol 0.2."""
from __future__ import annotations

from collections.abc import Mapping
import json
import numpy as np

from friskoli_cad.protocol import ProtocolError

SEMANTIC_FIELDS = ("dtype", "tensor_shape", "entity_set", "entity_order", "coordinate_frame",
                   "axis_order", "location", "domain_revision", "grid_revision", "temporal")
RECORD_BYTE_LIMIT = 1024 * 1024


def validate_record(value, path):
    """Bound a record's UTF-8 JSON size without building a second large tree."""
    remaining = RECORD_BYTE_LIMIT

    def charge(size):
        nonlocal remaining
        remaining -= size
        if remaining < 0:
            raise ProtocolError('module.record_size', path,
                                f'Each structured port/state is limited to {RECORD_BYTE_LIMIT} JSON bytes')

    def visit(item, depth=0):
        if depth > 64:
            raise ProtocolError('module.record_depth', path, 'Structured values may nest at most 64 levels')
        if isinstance(item, np.generic):
            item = item.item()
        if isinstance(item, np.ndarray) and item.ndim == 0:
            item = item.item()
        if isinstance(item, Mapping):
            charge(2)
            for index, (key, child) in enumerate(item.items()):
                if not isinstance(key, str):
                    raise ProtocolError('module.record_key', path, 'Record keys must be strings')
                if len(key) > RECORD_BYTE_LIMIT:
                    charge(len(key))
                try:
                    key_size = len(json.dumps(key, ensure_ascii=False).encode('utf-8'))
                except UnicodeError as error:
                    raise ProtocolError('module.record_key', path, 'Record keys must be valid UTF-8') from error
                charge(key_size + 1 + bool(index))
                visit(child, depth + 1)
        elif isinstance(item, (list, tuple, np.ndarray)):
            charge(2)
            for index, child in enumerate(item):
                charge(bool(index))
                visit(child, depth + 1)
        elif isinstance(item, (str, bool, int, float, type(None))):
            if isinstance(item, str) and len(item) > RECORD_BYTE_LIMIT:
                charge(len(item))
            try:
                size = len(json.dumps(item, ensure_ascii=False, allow_nan=False).encode('utf-8'))
            except (ValueError, UnicodeError) as error:
                raise ProtocolError('module.record_value', path, 'Record values must be finite UTF-8 JSON') from error
            charge(size)
        else:
            raise ProtocolError('module.record_value', path, 'Unsupported structured value')

    visit(value)
    return RECORD_BYTE_LIMIT - remaining


def port_semantics(port):
    shape = port["shape"]
    scope, _, kind = shape.partition(".")
    dtype = "bool" if kind == "boolean" else "int64" if kind == "index" else "json" if kind == "record" or shape == "event" else "float64"
    defaults = {"dtype": dtype, "tensor_shape": [3] if kind == "vector" else [],
        "entity_set": "owner.cells" if scope == "cell" else "world.grid" if scope == "field" else "world",
        "entity_order": "stable_id" if scope == "cell" else "zyx" if scope == "field" else "none",
        "coordinate_frame": "world" if scope in ("cell", "field") else "none",
        "axis_order": "xyz" if kind == "vector" else "zyx" if scope == "field" else "none",
        "location": "cell_center" if scope == "cell" else "voxel_center" if scope == "field" else "global",
        "domain_revision": "current", "grid_revision": "current",
        "temporal": "instant"}
    return {key: port.get(key, value) for key, value in defaults.items()}


def resolve_port(port, node):
    result = port_semantics(port)
    parameters = node["parameters"]
    for key, value in tuple(result.items()):
        if isinstance(value, str):
            if value == "owner.cells":
                result[key] = "population:" + node["owner"]["id"]
            elif value.startswith("parameter:"):
                name = value.split(":", 1)[1]
                if name not in parameters:
                    raise ProtocolError("port.binding", "/parameters", f"Missing semantic binding {name}")
                result[key] = parameters[name]["value"]
    result['tensor_shape'] = [parameters[d.split(':', 1)[1]]['value'] if isinstance(d, str) and d.startswith('parameter:') else d
                              for d in result['tensor_shape']]
    return result


def entity_ids_for_port(port, context):
    entity_set = port.get('entity_set', 'owner.cells')
    if entity_set == 'owner.cells':
        return context.entity_ids
    if entity_set.startswith('parameter:'):
        entity_set = context.parameters.get(entity_set.split(':', 1)[1])
    if entity_set == 'population:' + context.owner_id:
        return context.entity_ids
    ids = context.entity_sets.get(entity_set)
    if ids is None:
        raise ProtocolError('module.entity_set', '/context/entity_sets', f'Unknown explicit entity set {entity_set}')
    return ids


def validate_connection(source, target, source_node, target_node, path):
    a, b = resolve_port(source, source_node), resolve_port(target, target_node)
    different = [key for key in SEMANTIC_FIELDS if a[key] != b[key]]
    if different:
        raise ProtocolError("edge.semantic_type", path,
                            "Explicit conversion or entity mapping required: " + ", ".join(different))


def validate_port_value(value, port, context, path):
    shape = port["shape"]
    scope, _, kind = shape.partition(".")
    if kind == "record" or shape == "event":
        if not isinstance(value, (Mapping, tuple, list)):
            raise ProtocolError("module.shape", path, "Structured record or event required")
        if scope == "cell" and len(value) != len(entity_ids_for_port(port, context)):
            raise ProtocolError("module.entity_count", path, "Cell records must follow context entity IDs")
        validate_record(value, path)
        return
    array = np.asarray(value)
    semantics = port_semantics(port)
    expected = tuple(context.parameters.get(d.split(':', 1)[1]) if isinstance(d, str) and d.startswith('parameter:') else d
                     for d in semantics["tensor_shape"])
    if any(type(d) is not int or d < 1 for d in expected):
        raise ProtocolError('module.tensor_shape', path, 'Tensor dimensions require positive integer parameter bindings')
    if scope == "cell":
        expected = (len(entity_ids_for_port(port, context)),) + expected
    elif scope == "field":
        grid = context.world.get("grid_shape_zyx")
        if grid is not None:
            expected = tuple(grid) + expected
        elif array.ndim < 3:
            raise ProtocolError("module.shape", path, "Field output requires ZYX dimensions")
        else:
            expected = tuple(array.shape[:3]) + expected
    if array.shape != expected:
        raise ProtocolError("module.shape", path, f"Expected {expected}, got {array.shape}")
    dtype = semantics["dtype"]
    kinds = {"bool": "b", "int32": "i", "int64": "i", "uint32": "u", "uint64": "u",
             "float32": "f", "float64": "f"}
    if dtype not in kinds or array.dtype.kind != kinds[dtype]:
        # Scalar Python numbers have no explicit width; vectors do.
        if not (array.ndim == 0 and dtype in ("float32", "float64") and array.dtype.kind in "iu"):
            raise ProtocolError("module.dtype", path, f"Expected {dtype}, got {array.dtype}")
    if isinstance(value, np.ndarray) and array.dtype != np.dtype(dtype):
        raise ProtocolError("module.dtype", path, f"Expected exact {dtype}, got {array.dtype}")
    if not np.isfinite(array).all():
        raise ProtocolError("module.non_finite", path, "Numeric port values must be finite")
