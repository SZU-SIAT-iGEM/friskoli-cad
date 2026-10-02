from friskoli_cad.assembly_library import official_assemblies
from friskoli_cad.biological_assemblies import validate_assembly
from friskoli_cad.protocol.task_validation import sha256
from friskoli_cad.engine.chemotaxis_templates import template_catalog


def test_reviewed_templates_are_real_validated_assemblies_and_isolated():
    records=official_assemblies()
    assert len(records)==len(template_catalog())
    for record in records:
        assembly=record['assembly']
        validate_assembly(assembly)
        assert assembly['provenance']['kind']=='example'
        assert record['sha256']==sha256(assembly)
        assert assembly['module_manifests'] and assembly['input_requirements']
    records[0]['assembly']['name']='changed'
    assert official_assemblies()[0]['assembly']['name']!='changed'
