"""Read-only mechanism data extracted from reviewed scientific examples."""
from functools import lru_cache
from copy import deepcopy
from .biological_assemblies import extract_assembly
from .engine.presets import make_example, template_catalog
from .protocol.task_validation import sha256


@lru_cache(maxsize=1)
def _catalog():
    result = []
    for template in template_catalog():
        project = make_example(template['example_id'])
        group_id = next(iter(project['groups']))
        assembly = extract_assembly(project, group_id, {
            'id': template['id'], 'name': template['label'], 'version': template['version'],
            'kind':'part', 'biological_role':'population mechanism',
            'provenance':{'kind':'example', 'reference':template['source']}})
        result.append({'assembly':assembly, 'sha256':sha256(assembly),
                       'example_id':template['example_id'], 'maturity':'exploratory'})
    return result


def official_assemblies():
    """Return isolated records; frozen module_manifests are exact contracts."""
    return deepcopy(_catalog())
