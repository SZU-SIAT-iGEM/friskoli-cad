"""Portable task checkpoint: bounded JSON metadata and checked raw array segments."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import zipfile
import numpy as np
from friskoli_cad.tasks.arrays import write_array, read_array, validate_descriptor
from friskoli_cad.protocol.task_validation import canonical_bytes, strict_json_loads, sha256

METADATA_LIMIT = 16 * 1024 * 1024
SEGMENT_LIMIT = 4 * 1024 * 1024


def adapters(project):
    profile = project.get('execution_profile')
    if profile == 'modular-spatial-v1':
        from .modular_checkpoint import export_checkpoint, restore_checkpoint
    elif profile == 'chemotaxis-spatial-v1':
        from .chemotaxis_checkpoint import export_checkpoint, restore_checkpoint
    elif profile == 'spatial-unbiased-v1':
        from .spatial_checkpoint import export_checkpoint, restore_checkpoint
    else:
        raise ValueError('This profile does not declare complete checkpoint support')
    return export_checkpoint, restore_checkpoint


def save_task_checkpoint(sim, destination, *, maximum, task_context=None):
    export, _ = adapters(sim.project)
    payload = export(sim, binary=True)
    destination = Path(destination)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.checkpoint-') as temporary:
        directory = Path(temporary)
        arrays = []
        def encode(value):
            if isinstance(value, np.ndarray):
                index = len(arrays)
                arrays.append(write_array(value, directory, f'array_{index:06d}', SEGMENT_LIMIT))
                return {'$task_array': index}
            if isinstance(value, dict):
                return {k: encode(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [encode(v) for v in value]
            return value
        document = {'task_checkpoint_version': '1.0', 'project': sim.project,
                    'payload': encode(payload), 'arrays': arrays, 'task_context': task_context or {}}
        document['sha256'] = sha256(document)
        # JSON float tokens are preserved for strict I-JSON reload.
        metadata = json.dumps(document, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(metadata) > METADATA_LIMIT or len(metadata) + sum(a['bytes'] for a in arrays) > maximum:
            raise ValueError('Checkpoint exceeds metadata or total byte budget')
        pending = directory / 'checkpoint.pending'
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr('manifest.json', metadata)
            for descriptor in arrays:
                for segment in descriptor['segments']:
                    archive.write(directory / segment['name'], segment['name'])
        if pending.stat().st_size > maximum:
            raise ValueError('Checkpoint container exceeds total byte budget')
        with pending.open('rb+') as stream:
            os.fsync(stream.fileno())
        os.replace(pending, destination)
    from friskoli_cad.tasks.artifacts import file_digest
    size, digest = file_digest(destination)
    return {'bytes': size, 'sha256': digest, 'step_index': sim.frame_index,
            'time_s': sim.time_s, 'media_type': 'application/vnd.friskoli.checkpoint+zip'}


def _load_task_checkpoint(source, *, maximum, return_document=False):
    source = Path(source)
    if source.stat().st_size > maximum:
        raise ValueError('Checkpoint exceeds total byte budget')
    with tempfile.TemporaryDirectory(prefix='friskoli-checkpoint-') as temporary:
        directory = Path(temporary)
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > 65536 or len({e.filename for e in entries}) != len(entries):
                raise ValueError('Duplicate or excessive checkpoint entries')
            if any(e.compress_type != zipfile.ZIP_STORED for e in entries):
                raise ValueError('Compressed checkpoint entries are unsupported')
            if sum(e.file_size for e in entries) > maximum:
                raise ValueError('Decoded checkpoint exceeds byte budget')
            info = archive.getinfo('manifest.json')
            if info.file_size > METADATA_LIMIT:
                raise ValueError('Checkpoint metadata exceeds byte budget')
            document = strict_json_loads(archive.read(info))
            if document.get('task_checkpoint_version') != '1.0' or document.get('sha256') != sha256({k:v for k,v in document.items() if k != 'sha256'}):
                raise ValueError('Checkpoint metadata version/hash mismatch')
            names = {'manifest.json'}
            total_arrays = 0
            for descriptor in document['arrays']:
                total_arrays += validate_descriptor(descriptor, maximum)
                if total_arrays > maximum:
                    raise ValueError('Array allocation budget exceeded')
                for segment in descriptor['segments']:
                    if segment['name'] in names or segment['bytes'] > SEGMENT_LIMIT:
                        raise ValueError('Duplicate segment or excessive segment bytes')
                    names.add(segment['name'])
                    info = archive.getinfo(segment['name'])
                    if info.file_size != segment['bytes']:
                        raise ValueError('Checkpoint segment length mismatch')
                    with archive.open(info) as incoming, (directory / segment['name']).open('wb') as outgoing:
                        while block := incoming.read(1024 * 1024):
                            outgoing.write(block)
            if names != {e.filename for e in entries}:
                raise ValueError('Undeclared checkpoint content')
        arrays = [read_array(d, directory, maximum, SEGMENT_LIMIT) for d in document['arrays']]
        def decode(value):
            if isinstance(value, dict):
                if set(value) == {'$task_array'}:
                    index = value['$task_array']
                    if type(index) is not int or not 0 <= index < len(arrays):
                        raise ValueError('Invalid checkpoint array reference')
                    return arrays[index]
                return {k: decode(v) for k, v in value.items()}
            if isinstance(value, list):
                return [decode(v) for v in value]
            return value
        payload = decode(document['payload'])
        _, restore = adapters(document['project'])
        simulation = restore(document['project'], payload)
        return (simulation, document) if return_document else simulation



def load_task_checkpoint(source, *, maximum, return_document=False):
    try:
        return _load_task_checkpoint(source, maximum=maximum, return_document=return_document)
    except (KeyError, TypeError, AttributeError, zipfile.BadZipFile, RuntimeError) as error:
        raise ValueError('Invalid bounded task checkpoint: ' + str(error)) from error
