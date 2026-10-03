"""Versioned, non-executable little-endian arrays with bounded I/O segments."""
from __future__ import annotations
import hashlib
import math
import os
from pathlib import Path
import numpy as np

DTYPES = {'<f8': np.dtype('<f8'), '|b1': np.dtype('bool'), '<i8': np.dtype('<i8')}


def array_digest(value):
    array = canonical_array(value)
    digest = hashlib.sha256()
    view = memoryview(array.reshape(-1).view(np.uint8))
    for start in range(0, len(view), 1024 * 1024):
        digest.update(view[start:start + 1024 * 1024])
    return {'dtype': array.dtype.str, 'shape': list(array.shape), 'bytes': array.nbytes,
            'sha256': digest.hexdigest()}


def canonical_array(value):
    array = np.asarray(value)
    if array.dtype.kind == 'b':
        dtype = '|b1'
    elif array.dtype.kind in 'iu':
        dtype = '<i8'
    elif array.dtype.kind == 'f':
        dtype = '<f8'
    else:
        raise ValueError('Only numeric and boolean arrays are supported')
    result = np.ascontiguousarray(array, dtype=DTYPES[dtype]).reshape(array.shape)
    if result.dtype.kind == 'f' and not np.isfinite(result).all():
        raise ValueError('Arrays must contain finite values')
    return result


def hashable(value):
    """Old JSON hashes stay identical; binary payloads hash array bytes explicitly."""
    if isinstance(value, np.ndarray):
        return {'$typed_array': array_digest(value)}
    if isinstance(value, dict):
        return {k: hashable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [hashable(v) for v in value]
    return value


def write_array(value, directory, prefix, maximum):
    array = canonical_array(value)
    maximum = maximum // 8 * 8
    if maximum < 8:
        raise ValueError('Array segment budget must be at least eight bytes')
    descriptor = {**array_digest(array), 'array_version': '1.0', 'order': 'C', 'segments': []}
    view = memoryview(array.reshape(-1).view(np.uint8))
    directory = Path(directory)
    for index, offset in enumerate(range(0, len(view), maximum)):
        body = view[offset:offset + maximum]
        name = f'{prefix}_{index:06d}.bin'
        temporary = directory / (name + '.pending')
        with temporary.open('wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
        descriptor['segments'].append({'name': name, 'offset': offset, 'bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest()})
    return descriptor


def validate_descriptor(value, maximum):
    if value.get('array_version') != '1.0' or value.get('order') != 'C' or value.get('dtype') not in DTYPES:
        raise ValueError('Unsupported array contract')
    shape = value.get('shape')
    if not isinstance(shape, list) or len(shape) > 8 or any(type(n) is not int or n < 0 for n in shape):
        raise ValueError('Invalid array shape')
    size = math.prod(shape) * DTYPES[value['dtype']].itemsize
    if type(value.get('bytes')) is not int or size != value['bytes'] or size > maximum:
        raise ValueError('Decoded array exceeds its declared byte budget')
    offset = 0
    for segment in value['segments']:
        name = segment.get('name', '')
        if Path(name).name != name or not name.endswith('.bin') or '/' in name or '\\' in name:
            raise ValueError('Invalid array segment path')
        if segment['offset'] != offset or type(segment['bytes']) is not int or segment['bytes'] <= 0:
            raise ValueError('Array segments must exactly and contiguously cover the array')
        offset += segment['bytes']
    if offset != size:
        raise ValueError('Array segment size mismatch')
    return size


def read_array(descriptor, directory, maximum, segment_maximum=4 * 1024 * 1024):
    size = validate_descriptor(descriptor, maximum)
    result = np.empty(descriptor['shape'], dtype=DTYPES[descriptor['dtype']])
    view = memoryview(result.reshape(-1).view(np.uint8))
    digest = hashlib.sha256()
    for segment in descriptor['segments']:
        if segment['bytes'] > segment_maximum:
            raise ValueError('Array segment exceeds decode budget')
        with (Path(directory) / segment['name']).open('rb') as stream:
            body = stream.read(segment['bytes'] + 1)
        if len(body) != segment['bytes'] or hashlib.sha256(body).hexdigest() != segment['sha256']:
            raise ValueError('Array segment checksum or size mismatch')
        view[segment['offset']:segment['offset'] + len(body)] = body
        digest.update(body)
    if digest.hexdigest() != descriptor['sha256']:
        raise ValueError('Array checksum mismatch')
    if result.dtype.kind == 'f' and not np.isfinite(result).all():
        raise ValueError('Array contains nonfinite values')
    return result
