"""Design transport, interchange schemas and a real local HTTP round trip."""
import base64
from copy import deepcopy
from importlib.resources import files
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from friskoli_cad.engine.presets import make_example
from friskoli_cad.engine.profiles import registry_for_project
from friskoli_cad.replay_service import ReplayHandler


@pytest.fixture
def server_url():
    server = ThreadingHTTPServer(('127.0.0.1', 0), ReplayHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    worker.join(2)


def request(root, path, value):
    data = value if isinstance(value, bytes) else json.dumps(value, allow_nan=False).encode()
    return urlopen(Request(root + path, data=data, headers={'Content-Type': 'application/json'}), timeout=30)


def design_input():
    project = make_example('center-pts-a-small')
    return {'project': project, 'settings': {'dt_s': .05, 'steps': 2, 'include_fields': True},
        'brief': {'brief_version': '0.1.0', 'id': 'http-design', 'name': 'Transport capacity study',
            'goal': {'metric': 'mean_displacement_um', 'direction': 'maximize', 'group_id': 'cells'},
            'chassis': {'name': 'Exploratory template', 'provenance': 'Constructed demonstration; not calibrated'},
            'variables': [{'node_id': 'capacity', 'parameter': 'g_requested', 'values': [1., 2.]}],
            'constraints': [], 'seeds': [0, 1], 'max_runs': 32}}


def test_design_generation_and_native_package_roundtrip_over_http(server_url):
    source = design_input()
    original = deepcopy(source)
    with request(server_url, '/api/design/generate', source) as response:
        design = json.load(response)
    assert source == original
    assert len(design['candidates']) == 3
    assert design['budget']['total_runs'] == 6
    assert sum(c['kind'] == 'control' for c in design['candidates']) == 1
    workspace = {'workspace_format_version': '0.7.0', 'project': source['project'],
        'population_blocks': [], 'graph_layout': {}, 'run_settings': source['settings'],
        'design': design, 'design_brief': source['brief']}
    schemas = [json.loads(p.read_text(encoding='utf-8')) for p in
        files('friskoli_cad.protocol').joinpath('schemas').iterdir() if p.name.endswith('.json')]
    resources = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    for name, value in [('design-brief', source['brief']), ('design', design), ('workspace', workspace)]:
        version = '0.7.0' if name == 'workspace' else '0.1.0'
        schema = next(s for s in schemas if s['$id'] == f'urn:friskoli:{name}:{version}')
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, registry=resources).validate(value)
    payload = {'package_version': '0.1.0', 'design': design, 'workspace': workspace,
        'runs': [], 'registry': registry_for_project(source['project']).catalog}
    with request(server_url, '/api/design/export', payload) as response:
        archive = response.read()
        assert response.headers.get_content_type() == 'application/zip'
        assert archive.startswith(b'PK')
    with request(server_url, '/api/design/import', {'archive_base64': base64.b64encode(archive).decode()}) as response:
        assert json.load(response) == payload

    for format, media in [('html', 'text/html'), ('csv', 'text/csv')]:
        with request(server_url, '/api/design/report', {'payload': payload, 'format': format}) as response:
            assert response.headers.get_content_type() == media
            assert response.read()


def test_biological_assembly_roundtrip_is_data_only_and_preserves_environment(server_url):
    project = design_input()['project']
    metadata = {'id': 'pts-core', 'name': 'PTS chemotaxis core', 'version': '1.0.0',
                'kind': 'part', 'biological_role': 'nutrient sensing and motility',
                'provenance': {'kind': 'example', 'reference': 'constructed template'}}
    with request(server_url, '/api/assemblies/extract',
                 {'project': project, 'group_id': 'cells', 'metadata': metadata}) as response:
        assembly = json.load(response)
    assert assembly['kind'] == 'part'
    assert assembly['input_requirements']
    with request(server_url, '/api/assemblies/apply',
                 {'project': project, 'group_id': 'cells', 'assembly': assembly}) as response:
        applied = json.load(response)
    assert applied['groups']['cells'] == project['groups']['cells']
    assert [n for n in applied['graph']['nodes'] if n['owner']['kind'] == 'environment'] == [
        n for n in project['graph']['nodes'] if n['owner']['kind'] == 'environment']


@pytest.mark.parametrize('path,body,status', [
    ('/api/design/generate', b'{"project":null,"project":{}}', 422),
    ('/api/design/generate', b'{"project":NaN}', 422),
    ('/api/design/generate', [], 422),
    ('/api/design/generate', {}, 422),
    ('/api/design/import', {'archive_base64': 'not base64!'}, 422),
    ('/api/design/import', {'archive_base64': base64.b64encode(b'not a zip').decode()}, 422),
    ('/api/design/report', {'payload': {}, 'format': 'python'}, 422),
    ('/api/design/execute', {}, 404),
    ('/api/design/generate?execute=1', {}, 404),
])
def test_design_transport_rejections_are_structured(server_url, path, body, status):
    with pytest.raises(HTTPError) as captured:
        request(server_url, path, body)
    assert captured.value.code == status
    error = json.load(captured.value)
    assert 'error' in error or 'issues' in error


def test_capabilities_publish_design_data_contract_without_starting_a_task(server_url):
    with urlopen(server_url + '/api/capabilities') as response:
        caps = json.load(response)
    assert caps['design']['design_version'] == '0.3.0'
    assert caps['design']['design_versions'] == ['0.1.0', '0.2.0', '0.3.0']
    assert caps['design']['max_runs'] == 32
    assert '0.7.0' in caps['workspace_versions']
    assert 'task' not in caps


def test_result_criteria_are_versioned_and_evaluation_is_read_only(server_url):
    source = design_input()
    source['brief'].update(brief_version='0.2.0', result_constraints=[{
        'id': 'arrival', 'kind': 'hard', 'metric': 'ever_arrived_fraction',
        'group_id': 'cells', 'operator': '>=', 'value': .5}],
        selection_policy={'min_repeats': 2, 'min_control_improvement': 0.})
    with request(server_url, '/api/design/generate', source) as response:
        design = json.load(response)
    assert design['design_version'] == '0.2.0'
    assert design['brief']['result_constraints'] == source['brief']['result_constraints']
    workspace = {'workspace_format_version': '0.7.0', 'project': source['project'],
        'population_blocks': [], 'graph_layout': {}, 'run_settings': source['settings'],
        'design': design, 'design_brief': source['brief']}
    schemas = [json.loads(p.read_text(encoding='utf-8')) for p in
        files('friskoli_cad.protocol').joinpath('schemas').iterdir() if p.name.endswith('.json')]
    resources = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    schema = next(s for s in schemas if s['$id'] == 'urn:friskoli:workspace:0.7.0')
    Draft202012Validator(schema, registry=resources).validate(workspace)
    payload = {'package_version': '0.1.0', 'design': design, 'workspace': workspace, 'runs': [], 'registry': None}
    original = deepcopy(payload)
    with request(server_url, '/api/design/evaluate', payload) as response:
        result = json.load(response)
    assert result['status'] == 'no_recommendation'
    assert result['recommended_candidate_id'] is None
    assert result['reasons']
    assert payload == original
    with request(server_url, '/api/design/export', payload) as response:
        archive = response.read()
    with request(server_url, '/api/design/import', {'archive_base64': base64.b64encode(archive).decode()}) as response:
        assert json.load(response) == payload
