"""Spatial contracts add a profile without widening the prior documents."""
from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from friskoli_cad.engine.profiles import SPATIAL_PROFILE, registry_for_profile
from friskoli_cad.project import validate_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.protocol.task_validation import _validator, canonical_bytes
from friskoli_cad.registry import validate_catalog


def project():
    return json.loads(files("friskoli_cad").joinpath("examples", "spatial_baseline.project.json").read_text(encoding="utf-8"))


def test_new_schemas_and_explicit_shared_error():
    folder = files("friskoli_cad.protocol").joinpath("schemas")
    for name in ("project-v0.4", "catalog-v0.3", "task-v0.3"):
        schema = json.loads(folder.joinpath(name + ".schema.json").read_text())
        Draft202012Validator.check_schema(schema)
    task = json.loads(folder.joinpath("task-v0.3.schema.json").read_text())
    assert task["$defs"]["Error"]["$ref"] == "task-v0.1.schema.json#/$defs/Error"
    assert task["$defs"]["CommonError"]["$ref"] == "task-v0.1.schema.json#/$defs/Error"
    _validator("CommonError", "0.3.0").validate({"task_contract_version": "0.1.0", "issues": [{
        "code": "task.invalid", "severity": "error", "phase": "request", "path": "", "targets": [], "message": "invalid"}]})


def test_catalog_environment_parameter_contract_and_all_references():
    catalog = registry_for_profile(SPATIAL_PROFILE).catalog
    assert catalog["catalog_version"] == "0.3.0"
    source = next(i for i, obj in enumerate(catalog["objects"]) if obj["initializer"]["module"] == "source.finite_local@1.0.0")
    for mutate in (lambda o: o["properties"][0].update(type="vector3"),
                   lambda o: o["properties"][0].update(unit="bogus"),
                   lambda o: o["properties"][0].update(path="missing"),
                   lambda o: o["initializer"].update(module="unknown@1.0.0"),
                   lambda o: o["initializer"].update(data_modules=["unknown@1.0.0"]),
                   lambda o: o["initializer"].update(data_modules=["pts.capsule_area@1.0.0"])):
        changed = deepcopy(catalog)
        mutate(changed["objects"][source])
        with pytest.raises(ProtocolError):
            validate_catalog(changed)


@pytest.mark.parametrize("seed", [None, -1, True, 9007199254740992, 1.5])
def test_project_seed_is_required_safe_nonnegative_integer(seed):
    doc = project()
    if seed is None:
        del doc["random_seed"]
    else:
        doc["random_seed"] = seed
    with pytest.raises(ProtocolError):
        validate_project(doc, registry_for_profile(SPATIAL_PROFILE).manifests)


def test_preflight_rejects_excess_grid_before_runtime(monkeypatch):
    from friskoli_cad.engine import spatial_runtime
    def unexpected_allocation(*args, **kwargs):
        pytest.fail("Oversized grid reached field allocation")
    monkeypatch.setattr(spatial_runtime, "make_local_field_state", unexpected_allocation)
    doc = project()
    doc["domain"]["counts_xyz"] = [spatial_runtime.MAX_VOXELS + 1, 1, 1]
    with pytest.raises(ProtocolError, match="spatial.resource_limit"):
        validate_project(doc, registry_for_profile(SPATIAL_PROFILE).manifests)


def test_field_envelope_rejects_wrong_unit_negative_and_nonfinite():
    # Other required fields are supplied by a real task test; inspect this independent new field contract here.
    schema = json.loads(files("friskoli_cad.protocol").joinpath("schemas", "task-v0.3.schema.json").read_text())
    validator = Draft202012Validator(schema["$defs"]["FrameEnvelope"]["properties"]["concentrations"])
    validator.validate({"s": {"unit": "uM", "values_zyx": [[[0, 1]]]}})
    for field in ({"unit": "mM", "values_zyx": [[[1]]]}, {"unit": "uM", "values_zyx": [[[-1]]]}, {"unit": "uM", "values_zyx": [[]]}):
        with pytest.raises(ValidationError): validator.validate({"s": field})
    with pytest.raises(ValueError): canonical_bytes({"values_zyx": [[[float("nan")]]]})


def test_new_openapi_covers_three_profiles_and_common_errors():
    api = json.loads((Path(__file__).parents[1] / "docs/protocol/tasks-openapi-v0.3.json").read_text())
    refs = api["x-capabilities-extension"]["schema"]["anyOf"]
    assert len(refs) == 3
    assert api["info"]["version"] == "0.3.0"


def test_older_catalogs_do_not_gain_environment_adapter():
    catalog = registry_for_profile(SPATIAL_PROFILE).catalog
    catalog["catalog_version"] = "0.2.0"
    catalog["execution_semantics"] = "conservative-pts-bulk-v1"
    with pytest.raises(ProtocolError): validate_catalog(catalog)


def test_role_requirements_only_accept_explicit_compatible_providers():
    catalog = registry_for_profile(SPATIAL_PROFILE).catalog
    material = next((i for i, obj in enumerate(catalog["objects"]) if obj["initializer"].get("requirements")), None)
    assert material is not None, "material placement requires its declared degradation role"
    for mutate in (lambda c: c["objects"][material]["initializer"]["requirements"][0].update(default_module="unknown@1.0.0"),
                   lambda c: c["objects"][material]["initializer"]["requirements"][0].update(role="unprovided.role")):
        changed = deepcopy(catalog);mutate(changed)
        with pytest.raises(ProtocolError): validate_catalog(changed)
    changed = deepcopy(catalog)
    requirement = changed["objects"][material]["initializer"]["requirements"][0]
    provider = next(entry for entry in changed["entries"] if entry["key"] == requirement["default_module"])
    provider.pop("provides_roles")
    with pytest.raises(ProtocolError, match="catalog.requirement"): validate_catalog(changed)


def test_constructed_defaults_obey_manifest_contract():
    catalog = registry_for_profile(SPATIAL_PROFILE).catalog
    entries = [entry for entry in catalog["entries"] if entry.get("default_parameters")]
    assert entries
    for entry in entries:
        changed = deepcopy(catalog)
        changed_entry = next(item for item in changed["entries"] if item["key"] == entry["key"])
        name = next(iter(changed_entry["default_parameters"]))
        changed_entry["default_parameters"][name] = -1
        with pytest.raises(ProtocolError, match="catalog.default_parameter"): validate_catalog(changed)


def test_compiled_schedule_freezes_global_source_and_enzyme_bindings():
    from friskoli_cad.tasks.metadata import compiled_plan
    doc = project()
    registry = registry_for_profile(SPATIAL_PROFILE)
    schedule = compiled_plan(doc, registry)["schedule"]
    nodes = doc["graph"]["nodes"]
    for binding in schedule["field_sources"]:
        for module, key in (("source.finite_local", "source_nodes"), ("material.degradable_box", "material_nodes")):
            assert set(binding[key]) == {n["id"] for n in nodes if n["module_id"] == module and n["parameters"]["species"]["value"] == binding["species"]}
    for module, key in (("material.degradable_box", "material_nodes"), ("surface.enzyme_activity", "enzyme_nodes")):
        assert set(schedule["degradation_participants"][key]) == {n["id"] for n in nodes if n["module_id"] == module}
    assert schedule["field_order"] == ["contact_degradation", "finite_release", "stable_diffusion_substeps", "shared_uptake"]
    _validator("CompiledPlan", "0.3.0").validate(compiled_plan(doc, registry))
