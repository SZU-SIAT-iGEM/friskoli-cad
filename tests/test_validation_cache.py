"""Content caches preserve validation after callers mutate previously valid inputs."""
from copy import deepcopy

import pytest

from friskoli_cad.protocol import ProtocolError, validate_manifest
from friskoli_cad.protocol.validation import _parameter_value
from friskoli_cad.engine.presets import make_example
from friskoli_cad.engine.profiles import registry_for_project
from friskoli_cad.project import validate_project


def test_cached_manifest_is_checked_again_after_in_place_mutation():
    registry = registry_for_project(make_example())
    manifest = deepcopy(registry.manifests[0])
    validate_manifest(manifest)
    manifest['scope'] = 'unknown-owner'
    with pytest.raises(ProtocolError):
        validate_manifest(manifest)


def test_cached_structured_parameter_checks_content_schema_and_error_path():
    definition = {'type': 'array', 'unit': '1', 'items': {'type': 'number', 'minimum': 0}}
    node = {'parameters': {'values': {'value': [1., 2.], 'unit': '1'}}}
    _parameter_value(node, 3, 'values', definition)
    node['parameters']['values']['value'][1] = -1.
    with pytest.raises(ProtocolError) as error:
        _parameter_value(node, 3, 'values', definition)
    assert error.value.path == '/nodes/3/parameters/values/value/1'
    node['parameters']['values']['value'][1] = 2.
    definition['items']['minimum'] = 3.
    with pytest.raises(ProtocolError):
        _parameter_value(node, 3, 'values', definition)


def test_empty_legacy_controls_is_optional_but_active_controls_are_rejected():
    project = make_example()
    registry = registry_for_project(project)
    for controls in (None, {}):
        if controls is not None:
            project['controls'] = controls
        validate_project(project, registry.manifests, registry=registry)
    project['controls'] = {'obsolete': {}}
    with pytest.raises(ProtocolError):
        validate_project(project, registry.manifests, registry=registry)
