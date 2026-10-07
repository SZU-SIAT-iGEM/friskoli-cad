"""Portable Task 0.6 records with verified, package-relative array segments."""
import hashlib
import io
import json
from pathlib import PurePosixPath
import zipfile

import numpy as np

from .tasks.arrays import validate_descriptor, read_array

MAX_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 8192


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _sha(body):
    return hashlib.sha256(body).hexdigest()


def _chunks(service, run_id):
    offset = 0
    while True:
        manifest = service.manifest(run_id, offset=offset)
        for descriptor in manifest['chunks']:
            yield from json.loads(service.chunk(run_id, descriptor['chunk_id']))['frames']
        offset = manifest.get('next_chunk_offset')
        if offset is None:
            return


def collect_task_export(service, run_id, *, inline_arrays=False):
    """Capture actual frozen inputs and all published frames, including parents."""
    task, submission, manifest = service.get(run_id), service.input(run_id), service.manifest(run_id)
    if task['task_contract_version'] != '0.6.0' or task['status'] != 'completed' or task['result']['completeness'] != 'complete':
        raise ValueError('A portable research record requires a completed Task 0.6 run')
    segments, seen = [], set()
    current = run_id
    while current:
        if current in seen or len(seen) >= 64:
            raise ValueError('Invalid run ancestry')
        seen.add(current)
        m = service.manifest(current)
        segments.insert(0, (current, service.input(current), m))
        current = m.get('parent_run_id')
    snapshots, sources = {}, {}
    for sid, frozen, m in segments:
        for item in _chunks(service, sid):
            step = item['frame']['frame_index']
            snapshot = {'frame': item['frame'], 'concentrations': item.get('concentrations', {}),
                        'metrics': item['metrics'], 'object_states': item.get('object_states', {}),
                        'lifecycle_details': item.get('lifecycle_details', {}),
                        'display_domain': frozen['project']['domain'], 'segment_run_id': sid}
            if step in snapshots and not snapshot['frame']['events']:
                snapshot['frame']['events'] = snapshots[step]['frame']['events']
                snapshot['lifecycle_details'] = snapshots[step]['lifecycle_details']
            snapshots[step], sources[step] = snapshot, sid
    project = submission['project']
    replay = {'replay_format_version': '0.1.0', 'project_id': project['id'], 'run': project['run'],
              'domain': project['domain'], 'snapshots': [snapshots[k] for k in sorted(snapshots)],
              'execution': {'task_contract_version': '0.6.0', 'task_run_id': run_id,
                            'status': manifest['status'], 'completeness': manifest['completeness'],
                            'input_snapshot': manifest['input_snapshot']}}
    if len(segments) > 1:
        replay['lineage'] = [{'run_id': sid, 'submission': frozen, 'manifest': m} for sid, frozen, m in segments]
    if inline_arrays:
        for snapshot in replay['snapshots']:
            sid = snapshot['segment_run_id']
            for field in snapshot['concentrations'].values():
                descriptor = field.pop('array', None)
                if descriptor is not None:
                    for segment in descriptor['segments']:
                        service.artifact(sid, segment['href'].rsplit('/', 1)[-1])
                    field['values_zyx'] = read_array(descriptor, service.directory / 'runs' / sid, MAX_BYTES).tolist()
    return {'task_contract_version': '0.6.0', 'task_run_id': run_id, 'status': task['status'],
            'completeness': task['result']['completeness'], 'task': task, 'submission': submission,
            'manifest': manifest, 'replay': replay}


def export_task_package(service, run_id):
    record = collect_task_export(service, run_id)
    members = {}
    for snapshot in record['replay']['snapshots']:
        sid = snapshot['segment_run_id']
        for field in snapshot['concentrations'].values():
            descriptor = field.get('array')
            if descriptor is None:
                continue
            for segment in descriptor['segments']:
                artifact_id = segment['href'].rsplit('/', 1)[-1]
                path, metadata = service.artifact(sid, artifact_id)
                body = path.read_bytes()
                if len(body) != segment['bytes'] or _sha(body) != segment['sha256']:
                    raise ValueError('Frame array differs from the published artifact')
                name = f'{sid}_{path.name}'
                relative = 'arrays/' + name
                members[relative] = body
                segment['name'], segment['href'] = name, relative
    # The same-version checkpoint and final full-resolution fields are part of
    # the delivered record even if display fields use coarse volume averages.
    for artifact_id in ('checkpoint', 'final_fields'):
        if any(a.get('artifact_id') == artifact_id or a.get('href', '').rsplit('/', 1)[-1] == artifact_id for a in record['manifest'].get('artifacts', [])):
            path, _ = service.artifact(run_id, artifact_id)
            members['artifacts/' + artifact_id + ('.zip' if artifact_id == 'checkpoint' else '.npz')] = path.read_bytes()
    members['run.result.json'] = _json(record)
    if sum(len(v) for v in members.values()) > MAX_BYTES or len(members) > MAX_MEMBERS:
        raise ValueError('Research package exceeds its explicit 128 MiB/8192 member budget')
    package = {'run_package_version': '1.0.0', 'task_contract_version': '0.6.0', 'run_id': run_id,
               'files': {k: {'bytes': len(v), 'sha256': _sha(v)} for k, v in sorted(members.items())}}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, body in members.items():
            archive.writestr(name, body)
        archive.writestr('run-package.json', _json(package))
    return output.getvalue()


def is_task_package(raw):
    if not raw.startswith(b'PK'):
        return False
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return 'run-package.json' in archive.namelist()


def read_task_package(raw, *, decode_arrays=True):
    """Read without a running service, plugins, or extraction to disk."""
    if len(raw) > MAX_BYTES:
        raise ValueError('Research archive exceeds byte budget')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()
        names = [v.filename for v in info]
        if len(names) != len(set(names)) or len(names) > MAX_MEMBERS or sum(v.file_size for v in info) > MAX_BYTES:
            raise ValueError('Duplicate members or expanded package exceeds budget')
        for item in info:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or item.flag_bits & 1:
                raise ValueError('Unsafe archive member')
        package = json.loads(archive.read('run-package.json'))
        if package.get('run_package_version') != '1.0.0' or package.get('task_contract_version') != '0.6.0':
            raise ValueError('Unsupported research package version')
        if set(names) != {'run-package.json'} | set(package['files']):
            raise ValueError('Missing or unknown research package members')
        for name, expected in package['files'].items():
            body = archive.read(name)
            if len(body) != expected['bytes'] or _sha(body) != expected['sha256']:
                raise ValueError('Research package member checksum/length mismatch')
        record = json.loads(archive.read('run.result.json'))
        if record.get('task_run_id') != package['run_id']:
            raise ValueError('Research package identity mismatch')
        if decode_arrays:
            for snapshot in record['replay']['snapshots']:
                for field in snapshot['concentrations'].values():
                    descriptor = field.pop('array', None)
                    if descriptor is None:
                        continue
                    size = validate_descriptor(descriptor, MAX_BYTES)
                    if descriptor['dtype'] != '<f8' or descriptor.get('axis_order') != 'zyx' or len(descriptor['shape']) != 3:
                        raise ValueError('Concentration array requires float64 ZYX')
                    buffer = bytearray(size)
                    for segment in descriptor['segments']:
                        name = 'arrays/' + segment['name']
                        if segment['href'] != name or name not in package['files']:
                            raise ValueError('Array path is not package-relative')
                        body = archive.read(name)
                        if len(body) != segment['bytes'] or _sha(body) != segment['sha256']:
                            raise ValueError('Array segment differs from descriptor')
                        buffer[segment['offset']:segment['offset'] + len(body)] = body
                    if _sha(buffer) != descriptor['sha256']:
                        raise ValueError('Array checksum mismatch')
                    values = np.frombuffer(buffer, dtype='<f8').reshape(descriptor['shape'])
                    if not np.isfinite(values).all() or (values < 0).any():
                        raise ValueError('Invalid concentration array')
                    field['values_zyx'] = values.tolist()
    from .wiki_export import read_run_export, validate_wiki_record
    payload = read_run_export(_json(record))
    if decode_arrays:
        validate_wiki_record(payload)
    return payload
