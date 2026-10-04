from copy import deepcopy
import hashlib
import base64
import re
import io
import json
import xml.etree.ElementTree as ET
import zipfile
import pytest

from test_design_delivery import prototype, payload, run_for
from friskoli_cad.design_delivery import export_design_package
from friskoli_cad.replay_service import build_replay
from friskoli_cad.wiki_export import build_wiki
from friskoli_cad.standards_export import export_omex, export_sbol_components, unsupported_report
from friskoli_cad.protocol.task_validation import sha256


def test_wiki_real_frames_and_exact_native(payload, tmp_path):
    run = run_for(payload)
    run['replay'] = build_replay(run['project'], dt_s=.05, steps=2)
    payload['runs'] = [run]
    native = export_design_package(payload)
    output = tmp_path / 'wiki'
    manifest = build_wiki(native, output, download_url='https://example.org/download')
    assert (output / 'design.friskoli').read_bytes() == native
    assert json.loads((output / 'record.json').read_text(encoding='utf-8')) == payload
    assert manifest['native_sha256'] == hashlib.sha256(native).hexdigest()
    assert not any(x.name.startswith('kernel') for x in output.iterdir())
    assert 'solver_included' in (output / 'examples.json').read_text()
    # A public export contains the renderer's full local import closure and
    # licenses; every nested asset is covered by the published checksum list.
    assert 'vendor/three/build/three.core.js' in manifest['files']
    assert 'vendor/three/LICENSE' in manifest['files']
    assert 'field-slice.mjs' in manifest['files']
    for name, identity in manifest['files'].items():
        raw = (output / name).read_bytes()
        assert len(raw) == identity['bytes']
        assert hashlib.sha256(raw).hexdigest() == identity['sha256']
    html = (output / 'index.html').read_text(encoding='utf-8')
    importmap = re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1)
    digest = base64.b64encode(hashlib.sha256(importmap.encode()).digest()).decode()
    assert "'sha256-" + digest + "'" in html
    assert json.loads(importmap)['imports']['three'].startswith('./')
    assert '__IMPORTMAP_HASH__' not in html
    assert 'localhost' not in html and '127.0.0.1' not in html
    runtime = (output / 'wiki.mjs').read_text(encoding='utf-8')
    assert "import('./scene3d.mjs')" in runtime and 'kernel-client' not in runtime
    adapters = json.loads((output / 'viewer-catalog.json').read_text(encoding='utf-8'))
    assert 'not historical scientific provenance' in adapters['purpose']
    with pytest.raises(ValueError, match='new directory'):
        build_wiki(native, output, download_url='https://example.org/download')
    run['replay']['snapshots'].pop(1)
    with pytest.raises(ValueError, match='missing or unordered'):
        build_wiki(export_design_package(payload), tmp_path/'bad', download_url='https://example.org/download')
    assert not (tmp_path/'bad').exists()


def test_wiki_refuses_partial_draft_and_unsafe_link(payload, tmp_path):
    with pytest.raises(ValueError, match='completed historical'):
        build_wiki(export_design_package(payload), tmp_path/'draft', download_url='https://example.org/download')
    payload['runs'] = [run_for(payload)]
    payload['runs'][0]['status'] = 'cancelled'
    with pytest.raises(ValueError, match='Wiki run rejected'):
        build_wiki(export_design_package(payload), tmp_path/'partial', download_url='https://example.org/download')
    with pytest.raises(ValueError, match='HTTPS'):
        build_wiki(export_design_package(payload), tmp_path/'unsafe', download_url='javascript:alert(1)')


def test_omex_manifest_exact_native_and_explicit_limits(payload):
    native = export_design_package(payload)
    result, report = export_omex(native)
    with zipfile.ZipFile(io.BytesIO(result)) as archive:
        assert archive.read('design.friskoli') == native
        manifest = ET.fromstring(archive.read('manifest.xml'))
        locations = {e.attrib['location'] for e in manifest}
        assert locations == {'.'} | {'./'+n for n in archive.namelist()}
        assert all('master' not in e.attrib for e in manifest)
        checksums = json.loads(archive.read('checksums.json'))
        for path, digest in checksums.items(): assert hashlib.sha256(archive.read(path)).hexdigest() == digest
        assert not any(n.endswith(('.sbml', '.sedml', '.gb')) for n in archive.namelist())
    assert {e['path'] for e in report['entries'] if e['status']=='unsupported'} == {'/sbml','/sedml','/genbank','/fasta'}


def test_sbol_explicit_component_validation_and_readback():
    sbol3 = pytest.importorskip('sbol3')
    # Test-only declared generic protein, not a claim of a real engineered strain.
    components = [{'identity':'https://example.org/test/protein', 'name':'Declared test protein',
                   'types':[sbol3.SBO_PROTEIN], 'roles':[], 'derived_from':['https://example.org/test/evidence']}]
    data, report = export_sbol_components(components)
    assert report['validation']['errors'] == 0 and report['validation']['readback'] == 'passed'
    document = sbol3.Document(); document.read_string(data.decode(), sbol3.NTRIPLES)
    assert len(document.objects) == 1
    assert not document.find(components[0]['identity']).sequences
    for bad in ([], [dict(components[0], derived_from=[])], [dict(components[0], sequence='ATGC')]):
        with pytest.raises(ValueError): export_sbol_components(bad)
    assert unsupported_report('sbml')['entries'][0]['status'] == 'unsupported'


def test_standalone_real_task06_hashes_no_invented_design(tmp_path):
    from friskoli_cad.engine.presets import make_example
    from friskoli_cad.tasks import TaskService
    from friskoli_cad.run_delivery import collect_task_export,export_task_package
    from test_first_release_workflow import submission,wait_task
    with TaskService(tmp_path/'service') as service:
        p=make_example();task,_=service.submit(submission(service,p,steps=2),'wiki-actual')
        wait_task(service,task['run_id'])
        original=collect_task_export(service,task['run_id'],inline_arrays=True)
        archive=export_task_package(service,task['run_id'])
    build_wiki(archive,tmp_path/'standalone')
    record=json.loads((tmp_path/'standalone'/'record.json').read_text(encoding='utf-8'))
    assert 'design' not in record and 'design_ref' not in record['runs'][0]
    assert (tmp_path/'standalone'/'run.friskoli').read_bytes()==archive
    assert (tmp_path/'standalone'/'install.html').exists()
    original['submission']['execution']['seed']=55
    with pytest.raises(ValueError,match='provenance hash'):
        build_wiki(json.dumps(original).encode(),tmp_path/'tampered')
