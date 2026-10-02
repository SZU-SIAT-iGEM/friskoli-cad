import io
import json
import zipfile
from urllib.error import HTTPError
from urllib.request import urlopen
import pytest

from test_design_http import server_url, request, design_input
from friskoli_cad.design import generate_design
from friskoli_cad.design_delivery import import_design_package


@pytest.fixture(scope='module')
def payload():
    source=design_input()
    return {'package_version':'0.1.0','design':generate_design(source['project'],source['settings'],source['brief']),
            'workspace':None,'runs':[],'registry':None}


def test_omex_http_reuses_native_validation_and_emits_loss_report(server_url,payload):
    with request(server_url,'/api/design/standards',{'payload':payload,'format':'omex'}) as response:
        assert response.headers.get_content_type()=='application/zip'
        assert 'design.omex' in response.headers['Content-Disposition']
        archive=response.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        assert import_design_package(z.read('design.friskoli'))==payload
        report=json.loads(z.read('loss-report.json'))
        assert len([e for e in report['entries'] if e['status']=='unsupported'])==4
    with request(server_url,'/api/design/standards',{'payload':payload,'format':'loss-report'}) as response:
        assert response.headers.get_content_type()=='application/json'
        assert json.load(response)==report


@pytest.mark.parametrize('format_name',['sbml','sedml','genbank','fasta'])
def test_unsupported_formats_return_machine_readable_reason_not_fake_file(server_url,payload,format_name):
    with pytest.raises(HTTPError) as error:
        request(server_url,'/api/design/standards',{'payload':payload,'format':format_name})
    assert error.value.code==422
    result=json.load(error.value)
    assert result['error']['code']=='standards.unsupported'
    assert result['loss_report']['entries'][0]['status']=='unsupported'


def test_conditional_sbol_http_official_validation_and_report_bundle(server_url,payload):
    sbol3=pytest.importorskip('sbol3')
    components=[{'identity':'https://example.org/software-test/protein','name':'Test-only declaration',
                 'types':[sbol3.SBO_PROTEIN],'roles':[],'derived_from':['https://example.org/software-test/evidence']}]
    with request(server_url,'/api/design/standards',{'payload':payload,'format':'sbol3','components':components}) as response:
        assert response.headers.get_content_type()=='application/zip'
        result=response.read()
    with zipfile.ZipFile(io.BytesIO(result)) as z:
        document=sbol3.Document();document.read_string(z.read('components.nt').decode(),sbol3.NTRIPLES)
        assert not document.validate().errors
        assert json.loads(z.read('loss-report.json'))['validation']['readback']=='passed'
        assert json.loads(z.read('component-source.json'))==components


@pytest.mark.parametrize('change',[{'payload':{}},{'format':'python'},{'format':'sbol3'},{'components':None},{'unexpected':True}])
def test_invalid_standard_requests_never_bypass_native_package_checks(server_url,payload,change):
    with pytest.raises(HTTPError) as error:
        request(server_url,'/api/design/standards',{'payload':payload,'format':'omex',**change})
    assert error.value.code==422
    assert 'error' in json.load(error.value)


def test_standard_request_limit_matches_design_export(server_url,payload,monkeypatch):
    import friskoli_cad.replay_service as service
    monkeypatch.setattr(service,'MAX_DESIGN_REQUEST_BYTES',64)
    for path in ('/api/design/export','/api/design/standards'):
        with pytest.raises(HTTPError) as error:
            request(server_url,path,{'payload':payload,'format':'omex'})
        assert error.value.code==413


def test_optional_sbol_dependency_missing_is_structured(server_url,payload,monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'sbol3',None)
    components=[{'identity':'https://example.org/test/protein','name':'Test','types':['https://example.org/type'],
                 'roles':[],'derived_from':['https://example.org/evidence']}]
    with pytest.raises(HTTPError) as error:
        request(server_url,'/api/design/standards',{'payload':payload,'format':'sbol3','components':components})
    assert error.value.code==503 and json.load(error.value)['error']['code']=='standards.dependency_missing'
    with request(server_url,'/api/design/standards',{'payload':payload,'format':'omex'}) as response:
        assert response.status==200


def test_standard_component_and_output_limits_are_enforced(server_url,payload,monkeypatch):
    with pytest.raises(HTTPError) as error:
        request(server_url,'/api/design/standards',{'payload':payload,'format':'sbol3','components':[{}]*257})
    assert error.value.code==413
    import friskoli_cad.standards_export as standards
    monkeypatch.setattr(standards,'MAX_ARCHIVE_BYTES',10)
    with pytest.raises(HTTPError) as error:
        request(server_url,'/api/design/standards',{'payload':payload,'format':'omex'})
    assert error.value.code==413


def test_capability_exposes_conditional_formats_without_creating_task(server_url):
    with urlopen(server_url+'/api/capabilities') as response:
        caps=json.load(response)
    standards=caps['design']['standards']
    assert standards['href']=='/api/design/standards'
    assert {'omex','loss-report'}<=set(standards['formats'])
    assert standards['sbol3']['requires_explicit_components']
    assert set(standards['unsupported'])=={'sbml','sedml','genbank','fasta'}
    assert 'task' not in caps
