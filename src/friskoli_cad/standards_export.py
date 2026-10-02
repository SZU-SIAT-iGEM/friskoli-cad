"""Explicit, data-only exchange subsets. Never label an ABM as an ODE model."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile

from .design_delivery import import_design_package, export_design_package, MAX_ARCHIVE_BYTES, MAX_TOTAL_BYTES


class StandardsExportError(ValueError):
    def __init__(self, code, message, *, status=422, report=None):
        self.code, self.status, self.report = 'standards.' + code, status, report
        super().__init__(message)


def standards_capabilities():
    from importlib.util import find_spec
    available = find_spec('sbol3') is not None
    return {'href':'/api/design/standards', 'method':'POST',
            'formats':['omex','loss-report'] + (['sbol3'] if available else []),
            'sbol3':{'available':available, 'requires_explicit_components':True,
                     'optional_dependency':'friskoli-cad[standards]'},
            'unsupported':{name:unsupported_report(name)['entries'][0]['reason']
                           for name in ('sbml','sedml','genbank','fasta')}}


def _archive(files):
    if sum(map(len, files.values())) > MAX_TOTAL_BYTES:
        raise StandardsExportError('size', 'Standard archive exceeds 128 MiB expanded limit', status=413)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()): archive.writestr(path, data)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise StandardsExportError('size', 'Standard archive exceeds 64 MiB compressed limit', status=413)
    return result


def loss_report(format_name, entries):
    return {'loss_report_version': '0.1.0', 'format': format_name,
            'native_required_for_lossless_recovery': True, 'entries': entries}


def entry(path, status, reason, impact):
    return dict(path=path, status=status, reason=reason, impact=impact)


def unsupported_report(format_name):
    reasons = {'sbml': 'No mapped kinetic submodel with external numerical comparison is available; spatial ABM is not an ODE model.',
               'sedml': 'No supported externally reproducible model/algorithm pair is available.',
               'genbank': 'No verified annotated sequence export adapter is available.',
               'fasta': 'Sequence export is not supported by this component-only adapter.'}
    if format_name not in reasons:
        raise ValueError('Unknown unsupported format')
    return loss_report(format_name, [entry('/', 'unsupported', reasons[format_name], 'No standard document was emitted; retain native package.')])


def _uri(value):
    if not isinstance(value, str) or urlsplit(value).scheme not in ('https', 'http') or not urlsplit(value).netloc or any(c.isspace() for c in value):
        raise ValueError('Component identities, types, roles and evidence require absolute HTTP(S) URIs')
    return value


def export_sbol_components(components):
    """Return SBOL3 N-Triples and report from explicit evidence-bearing records.

    The caller supplies real biological identity/type claims; graph modules and
    descriptive chassis labels are deliberately not inferred as biological parts.
    pySBOL3 is an optional export dependency, never needed for the simulator.
    """
    if not isinstance(components, list) or not components:
        raise ValueError('SBOL requires at least one explicitly identified component')
    if len(components)>256 or len(json.dumps(components,ensure_ascii=False,allow_nan=False).encode())>1024*1024:
        raise StandardsExportError('size', 'SBOL component input exceeds 256 components / 1 MiB', status=413)
    try:
        import sbol3
    except ImportError as error:
        raise StandardsExportError('dependency_missing', 'Install friskoli-cad[standards] to enable validated SBOL export', status=503) from error
    doc = sbol3.Document()
    expected = {}
    for item in components:
        if not isinstance(item, dict) or set(item) != {'identity', 'name', 'types', 'roles', 'derived_from'}:
            raise ValueError('Component requires exactly identity, name, types, roles, derived_from; sequence/features are unsupported')
        identity = _uri(item['identity'])
        if identity in expected or not isinstance(item['name'], str) or not item['name'].strip():
            raise ValueError('Duplicate identity or missing name')
        if not isinstance(item['types'], list) or not item['types'] or not isinstance(item['roles'], list) or not isinstance(item['derived_from'], list) or not item['derived_from']:
            raise ValueError('Explicit component types and evidence are required')
        for key in ('types', 'roles', 'derived_from'):
            for value in item[key]: _uri(value)
        obj = sbol3.Component(identity, item['types'], name=item['name'], roles=item['roles'])
        obj.derived_from = item['derived_from']
        doc.add(obj)
        expected[identity] = item
    validation = doc.validate()
    if validation.errors:
        raise ValueError('; '.join(str(e) for e in validation.errors))
    serialized = doc.write_string(sbol3.SORTED_NTRIPLES)
    readback = sbol3.Document()
    readback.read_string(serialized, sbol3.NTRIPLES)
    if readback.validate().errors:
        raise ValueError('SBOL readback validation failed')
    for identity, item in expected.items():
        obj = readback.find(identity)
        if obj is None or obj.name != item['name'] or set(obj.types) != set(item['types']) or set(obj.roles) != set(item['roles']) or set(obj.derived_from) != set(item['derived_from']):
            raise ValueError('SBOL semantic readback mismatch')
    report = loss_report('SBOL3-component-subset', [
        entry('/components', 'exact', 'Explicit Component identities, names, types, roles and derivation URIs.', 'No biological identity inferred.'),
        entry('/sequences', 'omitted', 'This subset has no sequence mapping.', 'No DNA/RNA/protein sequence claimed.'),
        entry('/design', 'omitted', 'No graph, geometry, runtime, parameter or candidate mapping.', 'Retain native design for computation.')])
    report['validation'] = {'tool': 'pySBOL3', 'errors': 0, 'warnings': [str(w) for w in validation.warnings], 'readback': 'passed', 'numerical_reproduction': 'not_applicable'}
    return serialized.encode(), report


def export_omex(native_bytes, components=None):
    """OMEX v1 container; no executable master experiment is claimed."""
    import_design_package(native_bytes)
    files = {'design.friskoli': bytes(native_bytes)}
    formats = {'design.friskoli': 'http://purl.org/NET/mediatypes/application/zip'}
    report = loss_report('OMEX-v1', [entry('/design.friskoli', 'exact', 'Original native archive bytes retained.', 'Requires Friskoli for recomputation; container alone is not executable.')])
    for name in ('sbml', 'sedml', 'genbank', 'fasta'):
        report['entries'].extend(unsupported_report(name)['entries'])
        report['entries'][-1]['path'] = '/' + name
    if components is not None:
        files['components.nt'], subreport = export_sbol_components(components)
        files['sbol-loss-report.json'] = json.dumps(subreport, ensure_ascii=False, indent=2).encode()
        report['sbol_subset'] = subreport
        files['component-source.json'] = json.dumps(components, ensure_ascii=False, indent=2).encode()
        formats['components.nt'] = 'http://identifiers.org/combine.specifications/sbol'
    files['loss-report.json'] = json.dumps(report, ensure_ascii=False, indent=2).encode()
    files['checksums.json'] = json.dumps({p: hashlib.sha256(b).hexdigest() for p, b in files.items()}, sort_keys=True).encode()
    ns = 'http://identifiers.org/combine.specifications/omex-manifest'
    manifest = ET.Element('omexManifest', xmlns=ns)
    ET.SubElement(manifest, 'content', location='.', format='http://identifiers.org/combine.specifications/omex')
    ET.SubElement(manifest, 'content', location='./manifest.xml', format=ns)
    for path in sorted(files):
        ET.SubElement(manifest, 'content', location='./' + path, format=formats.get(path, 'http://purl.org/NET/mediatypes/application/json'))
    files['manifest.xml'] = ET.tostring(manifest, encoding='utf-8', xml_declaration=True)
    return _archive(files), report


def export_standard_payload(payload, format_name, components=None):
    """Validated transport entry point: return bytes, media type and fixed filename."""
    # The same native exporter applies data-only validation and byte budgets to
    # every requested format, including a report-only request.
    native = export_design_package(payload)
    if format_name in ('sbml','sedml','genbank','fasta'):
        report = unsupported_report(format_name)
        raise StandardsExportError('unsupported', report['entries'][0]['reason'], report=report)
    if format_name == 'sbol3':
        if not components:
            raise StandardsExportError('components_required', 'SBOL requires explicit evidence-bearing biological Components; no identity is inferred from the design')
        data, report = export_sbol_components(components)
        report['native_sha256'] = hashlib.sha256(native).hexdigest()
        result = _archive({'components.nt':data,
                          'component-source.json':json.dumps(components,ensure_ascii=False,indent=2).encode(),
                          'loss-report.json':json.dumps(report,ensure_ascii=False,indent=2).encode()})
        return result, 'application/zip', 'design-sbol3.zip'
    if format_name not in ('omex','loss-report'):
        raise StandardsExportError('unsupported', 'Supported formats are omex, loss-report and conditional sbol3; no file generated')
    archive, report = export_omex(native, components)
    if format_name == 'loss-report':
        return json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2).encode(), 'application/json; charset=utf-8', 'loss-report.json'
    return archive, 'application/zip', 'design.omex'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('native', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--components', type=Path)
    args = parser.parse_args()
    if args.output.exists(): parser.error('Output already exists')
    components = json.loads(args.components.read_text(encoding='utf-8')) if args.components else None
    archive, _ = export_omex(args.native.read_bytes(), components)
    with args.output.open('xb') as stream: stream.write(archive)


if __name__ == '__main__': main()
