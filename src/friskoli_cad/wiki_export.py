"""Publish verified past runs to a standalone static directory; no solver shipped."""
from __future__ import annotations
import argparse
import base64
import re
import csv
import io
from copy import deepcopy
import hashlib
from importlib.resources import files
import json
import math
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

from .design_delivery import import_design_package, design_report_csv, design_report_html, _run_reason, _loads, _project, _lock, MAX_ARCHIVE_BYTES
from .protocol.validation import validate_frame_sequence
from .protocol.task_validation import sha256
from .engine.profiles import registry_for_project


def read_run_export(raw):
    """Validate the frontend's asynchronous Export run JSON without a design."""
    original = _loads(raw)
    if original.get('task_contract_version') != '0.6.0':
        raise ValueError('First-release Wiki requires a Task 0.6 run export')
    submission, task, manifest = (original.get(k) for k in ('submission', 'task', 'manifest'))
    if not all(isinstance(v, dict) for v in (submission, task, manifest)):
        raise ValueError('Run export requires submission, task and manifest')
    run_id = original.get('task_run_id')
    for holder in (original, task, manifest):
        if holder.get('status') != 'completed' or holder.get('completeness', holder.get('result', {}).get('completeness')) != 'complete':
            raise ValueError('Run export must be completed and complete')
        if holder.get('task_contract_version') != submission.get('task_contract_version'):
            raise ValueError('Run export contract mismatch')
    if not run_id or task.get('run_id') != run_id or manifest.get('run_id') != run_id:
        raise ValueError('Run identity mismatch')
    expected = {'document_sha256': sha256(submission),
                'scientific_sha256': sha256({k: submission[k] for k in ('project','version_lock','execution','output_plan')}),
                'registry_sha256': submission['version_lock']['registry_sha256']}
    for holder in (task, manifest, original['replay'].get('execution', {})):
        snapshot = holder.get('input_snapshot', {})
        if any(snapshot.get(k) != v for k, v in expected.items()):
            raise ValueError('Frozen input provenance hash mismatch')
    project = submission['project']
    _project(project)
    _lock(submission['version_lock'])
    execution = submission['execution']
    if project.get('random_seed') != execution.get('seed'):
        raise ValueError('Frozen project seed mismatch')
    if type(execution.get('steps')) is not int or execution['steps'] < 1 or type(execution.get('dt_s')) not in (float,int) or not math.isfinite(execution['dt_s']) or execution['dt_s'] <= 0:
        raise ValueError('Invalid frozen execution settings')
    if type(submission['output_plan'].get('frame_every_steps')) is not int or submission['output_plan']['frame_every_steps'] < 1:
        raise ValueError('Invalid output frame interval')
    for holder in (task, manifest):
        progress = holder.get('progress', {})
        if progress.get('committed_step') != execution['steps'] or not math.isclose(progress.get('simulation_time_s', -1), execution['steps']*execution['dt_s'], rel_tol=1e-10, abs_tol=1e-10):
            raise ValueError('Completed task progress does not reach the requested endpoint')
    run = {'id':run_id, 'status':'completed', 'completeness':'complete', 'project':project,
           'settings':{**submission['execution'], **submission['output_plan']},
           'submission':submission, 'task':task, 'manifest':manifest, 'replay':original['replay']}
    return {'wiki_record_version':'0.1.0', 'record_kind':'standalone-task', 'record_id':run_id,
            'name':project.get('name', project['id']), 'runs':[run], 'original_file':'run.result.json'}


def validate_wiki_record(payload):
    standalone = payload.get('record_kind') == 'standalone-task'
    design = payload.get('design')
    candidates = {c['id']: c for c in design['candidates']} if design else {}
    if not payload['runs']:
        raise ValueError('Wiki requires completed historical runs, not a draft')
    for run in payload['runs']:
        reason = None if standalone else _run_reason(run, design, candidates)
        # A complete run can have an undefined goal; the viewer must show it as
        # missing, never reject or fabricate its scientific result.
        if reason not in (None, 'null_or_missing_metric'):
            raise ValueError('Wiki run rejected: ' + reason)
        replay = run['replay']
        project = run['project']
        if replay.get('domain') != project['domain'] or replay.get('run') != project['run']:
            raise ValueError('Replay domain/run must match frozen project')
        snapshots = replay['snapshots']
        settings = run['settings'] if standalone else design['settings']
        steps = settings['steps']
        every = settings.get('frame_every_steps', 1)
        expected = list(range(0, steps + 1, every))
        if expected[-1] != steps: expected.append(steps)
        if replay.get('lineage'):
            expected = sorted(set(expected) | {segment['manifest'].get('start_step', 0) for segment in replay['lineage']})
        if [s['frame']['frame_index'] for s in snapshots] != expected:
            raise ValueError('Replay has missing or unordered committed frames')
        frames = []
        nx, ny, nz = project['domain']['counts_xyz']
        for i, snapshot in enumerate(snapshots):
            frame = deepcopy(snapshot['frame'])
            if not math.isclose(frame['time_s'], expected[i] * settings['dt_s'], rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError('Replay frame time differs from execution')
            frame['frame_index'] = i  # Shared validator checks event continuity.
            frames.append(frame)
            concentrations = snapshot.get('concentrations')
            if not isinstance(concentrations, dict): raise ValueError('Missing concentrations mapping')
            for field in concentrations.values():
                physical_domain = snapshot.get('display_domain', project['domain'])
                display_domain = field.get('field_domain', physical_domain)
                stride = settings.get('field_stride_xyz', [1, 1, 1])
                if (display_domain.get('geometry') != physical_domain['geometry']
                        or display_domain.get('counts_xyz') != [n // s for n, s in zip(physical_domain['counts_xyz'], stride)]
                        or any(n % s for n, s in zip(physical_domain['counts_xyz'], stride))
                        or display_domain.get('spacing_um_xyz') != [d * s for d, s in zip(physical_domain['spacing_um_xyz'], stride)]):
                    raise ValueError('Preview grid differs from declared volume aggregation')
                if 'field_domain' in field and field.get('aggregation') != 'volume_mean':
                    raise ValueError('Unsupported field aggregation')
                nx, ny, nz = display_domain['counts_xyz']
                values = field.get('values_zyx')
                if (not isinstance(values, list) or len(values) != nz or
                    any(not isinstance(layer, list) or len(layer) != ny or
                        any(not isinstance(row, list) or len(row) != nx or
                            any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in row) for row in layer) for layer in values)):
                    raise ValueError('Malformed concentration array')
        validate_frame_sequence(frames, project['run'])


def build_wiki(native_bytes, output, *, download_url=None):
    """Build into a new directory; preserve exact native bytes and full history."""
    destination = Path(output)
    if destination.exists(): raise ValueError('Wiki output must be a new directory')
    url = urlsplit(download_url or '')
    if download_url is not None and (url.scheme != 'https' or not url.netloc):
        raise ValueError('Full-version download URL must be an explicit HTTPS URL')
    if len(native_bytes) > MAX_ARCHIVE_BYTES: raise ValueError('Input exceeds size budget')
    from .run_delivery import is_task_package, read_task_package
    task_package = is_task_package(native_bytes)
    standalone = not native_bytes.startswith(b'PK') or task_package
    payload = read_task_package(native_bytes) if task_package else read_run_export(native_bytes) if standalone else import_design_package(native_bytes)
    validate_wiki_record(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.wiki-build-', dir=destination.parent))
    try:
        # This is the same renderer used by CAD, with its complete relative
        # import closure. Editor utility imports supply pure geometry helpers;
        # wiki.mjs never installs editing actions or loads a kernel client.
        web = files('friskoli_cad').joinpath('web')
        assets = ['wiki.mjs', 'wiki.css', 'scene3d.mjs', 'catalog.mjs',
                  'placeables.mjs', 'population.mjs', 'graph-edit.mjs', 'field-slice.mjs',
                  'assets/friskoli.svg', 'assets/cad.svg',
                  'vendor/three/build/three.module.js', 'vendor/three/build/three.core.js',
                  'vendor/three/examples/jsm/controls/OrbitControls.js',
                  'vendor/three/examples/jsm/controls/TransformControls.js', 'vendor/three/LICENSE']
        for asset in assets:
            target = stage / asset
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(web.joinpath(asset).read_bytes())
        html = web.joinpath('wiki.html').read_text(encoding='utf-8')
        importmap = re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1)
        digest = base64.b64encode(hashlib.sha256(importmap.encode('utf-8')).digest()).decode('ascii')
        html = html.replace('__IMPORTMAP_HASH__', "'sha256-" + digest + "'")
        (stage / 'index.html').write_text(html, encoding='utf-8')
        viewer_catalog = {'purpose': 'Display adapters only, captured from export software; not historical scientific provenance.',
                          'profiles': {}, 'unavailable_profiles': []}
        for run in payload['runs']:
            profile = run['project']['execution_profile']
            if profile in viewer_catalog['profiles'] or profile in viewer_catalog['unavailable_profiles']:
                continue
            try:
                viewer_catalog['profiles'][profile] = registry_for_project(run['project']).catalog
            except (ValueError, KeyError):
                viewer_catalog['unavailable_profiles'].append(profile)
        (stage / 'viewer-catalog.json').write_text(json.dumps(viewer_catalog, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        (stage / 'record.json').write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        original_name = 'run.friskoli' if task_package else 'run.result.json' if standalone else 'design.friskoli'
        (stage / original_name).write_bytes(native_bytes)
        if standalone:
            buffer = io.StringIO(); writer = csv.writer(buffer)
            writer.writerow(['time_s','frame_index','metrics_json'])
            for snap in payload['runs'][0]['replay']['snapshots']:
                writer.writerow([snap['frame']['time_s'],snap['frame']['frame_index'],json.dumps(snap.get('metrics'),ensure_ascii=False)])
            (stage / 'results.csv').write_text(buffer.getvalue(), encoding='utf-8')
        else:
            (stage / 'results.csv').write_text(design_report_csv(payload), encoding='utf-8')
            (stage / 'report.html').write_text(design_report_html(payload), encoding='utf-8')
        (stage / 'install.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>本地安装</title><h1>在本地编辑与计算</h1><p>此静态网页不含求解内核。目前未配置公开下载地址。请向项目维护者取得正式发布的 Friskoli wheel 包及依赖说明，然后运行：</p><pre>python -m pip install /path/to/friskoli_cad-VERSION-py3-none-any.whl\npython -m friskoli_cad.replay_service --help</pre><p>安装前需确认发布包许可证和来源；这不是在线计算入口。</p><a href="index.html">返回记录</a></html>',encoding='utf-8')
        catalog = {'wiki_version': '0.1.0', 'mode': 'read-only-past-computation', 'solver_included': False,
                   'download_url': download_url, 'original_file':original_name, 'has_design_report':not standalone,
                   'record_bytes': (stage / 'record.json').stat().st_size,
                   'examples': [{'name': payload['name'] if standalone else payload['design']['brief']['name'], 'record': 'record.json'}]}
        (stage / 'examples.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
        manifest = {'wiki_version': '0.1.0', 'native_sha256': hashlib.sha256(native_bytes).hexdigest(),
                    'provenance': 'record.json contains complete original package payload, frozen inputs, locks, parameter evidence and all recorded frames.',
                    'runtime_environment': 'Preserved when recorded; missing historical environment is unknown, not reconstructed from export host.',
                    'viewer_catalog': 'viewer-catalog.json is an export-host display adapter catalog only; frozen scientific provenance remains unchanged in record.json.',
                    'files': {p.relative_to(stage).as_posix(): {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                              for p in sorted(stage.rglob('*')) if p.is_file()}}
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--download-url')
    args = parser.parse_args()
    build_wiki(args.native.read_bytes(), args.output, download_url=args.download_url)


if __name__ == '__main__': main()
