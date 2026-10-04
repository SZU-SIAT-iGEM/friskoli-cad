"""Biological chassis/part assemblies on real chemotaxis templates."""

from copy import deepcopy

import pytest

from friskoli_cad.biological_assemblies import (
    apply_assembly,
    extract_assembly,
    validate_assembly,
)
from friskoli_cad.engine.presets import EXAMPLES, make_example
from friskoli_cad.engine.profiles import registry_for_project
from friskoli_cad.project import validate_project
from friskoli_cad.protocol import ProtocolError


def metadata(identifier="ecoli-core", *, kind="part"):
    return {
        "id": identifier,
        "name": "E. coli chemotaxis core",
        "version": "1.0.0",
        "kind": kind,
        "biological_role": "nutrient sensing and motility",
        "provenance": {"kind": "example", "reference": "tests; constructed template"},
    }


def valid_project(project):
    registry = registry_for_project(project)
    validate_project(project, registry.manifests, registry=registry)


def clone_population(project, source="cells", target="reference"):
    """Add a second complete population branch to test preservation semantics."""
    result = deepcopy(project)
    source_group = result["groups"][source]
    result["groups"][target] = deepcopy(source_group)
    result["groups"][target]["ids"] = [f"{target}-{cell}" for cell in source_group["ids"]]

    source_ids = {
        node["id"]
        for node in result["graph"]["nodes"]
        if node["owner"] == {"kind": "population", "id": source}
    }
    mapping = {node_id: f"{target}_{node_id}" for node_id in source_ids}
    cloned_nodes = []
    for node in result["graph"]["nodes"]:
        if node["id"] not in source_ids:
            continue
        clone = deepcopy(node)
        clone["id"] = mapping[node["id"]]
        clone["owner"] = {"kind": "population", "id": target}
        cloned_nodes.append(clone)
    result["graph"]["nodes"].extend(cloned_nodes)

    cloned_edges = []
    for edge in result["graph"]["edges"]:
        if edge["from"]["node"] not in source_ids and edge["to"]["node"] not in source_ids:
            continue
        clone = deepcopy(edge)
        clone["id"] = f"{target}_{edge['id']}"
        clone["from"]["node"] = mapping.get(clone["from"]["node"], clone["from"]["node"])
        clone["to"]["node"] = mapping.get(clone["to"]["node"], clone["to"]["node"])
        cloned_edges.append(clone)
    result["graph"]["edges"].extend(cloned_edges)

    result["run"]["groups"] = [*result["run"]["groups"], target]
    for channel_id, channel in list(result["run"]["channels"].items()):
        if channel["node"] not in source_ids:
            continue
        clone = deepcopy(channel)
        clone["node"] = mapping[channel["node"]]
        clone["group_id"] = target
        result["run"]["channels"][f"{target}_{channel_id}"] = clone
    return result


@pytest.mark.parametrize("template", tuple(EXAMPLES))
def test_real_templates_extract_validate_and_apply_without_losing_environment(template):
    project = make_example(template)
    valid_project(project)
    group_id = next(iter(project["groups"]))
    original_environment = deepcopy([
        node for node in project["graph"]["nodes"]
        if node["owner"]["kind"] == "environment"
    ])
    assembly = extract_assembly(project, group_id, metadata(f"{template}-part"))
    validate_assembly(assembly)
    assert assembly["kind"] == "part"
    assert assembly["graph"]["nodes"]
    assert all(node["owner"] == {"kind": "population", "id": group_id}
               for node in assembly["graph"]["nodes"])
    assert assembly["input_requirements"]

    applied = apply_assembly(project, group_id, assembly)
    valid_project(applied)
    assert [node for node in applied["graph"]["nodes"]
            if node["owner"]["kind"] == "environment"] == original_environment
    assert applied["groups"][group_id] == project["groups"][group_id]
    assert set(applied["run"]["groups"]) == {group_id}


def test_application_preserves_other_population_and_allows_provider_id_binding():
    project = clone_population(make_example("center-pts-a-small"))
    valid_project(project)
    assembly = extract_assembly(project, "cells", metadata("pts-part"))

    # Rename the shared field provider while preserving its registered module,
    # proving that a compatible provider can be bound by explicit node ID.
    rebound = deepcopy(project)
    provider = next(node for node in rebound["graph"]["nodes"] if node["id"] == "sugar_field")
    provider["id"] = "sugar_field_rebound"
    for edge in rebound["graph"]["edges"]:
        if edge["from"]["node"] == "sugar_field":
            edge["from"]["node"] = "sugar_field_rebound"
    valid_project(rebound)
    requirement = next(req for req in assembly["input_requirements"]
                       if req["source"]["node"] == "sugar_field")
    applied = apply_assembly(
        rebound,
        "reference",
        assembly,
        bindings={requirement["id"]: {"node": "sugar_field_rebound", "port": requirement["source"]["port"]}},
    )
    valid_project(applied)
    assert applied["groups"]["cells"] == rebound["groups"]["cells"]
    assert applied["groups"]["reference"] == rebound["groups"]["reference"]
    assert any(node["id"] == "sugar_field_rebound" for node in applied["graph"]["nodes"])
    assert any(node["owner"] == {"kind": "population", "id": "cells"}
               for node in applied["graph"]["nodes"])


def test_chassis_replaces_geometry_while_part_keeps_geometry():
    project = make_example("center-pts-a-small")
    group_id = "cells"
    original = deepcopy(project["groups"][group_id]["initial_geometry"])
    part = extract_assembly(project, group_id, metadata("pts-part", kind="part"))
    part_result = apply_assembly(project, group_id, part)
    assert part_result["groups"][group_id]["initial_geometry"] == original

    chassis = extract_assembly(project, group_id, metadata("large-chassis", kind="chassis"))
    chassis["population"]["initial_geometry"] = [
        {**geometry, "length_um": geometry["length_um"] + 1.0}
        for geometry in chassis["population"]["initial_geometry"]
    ]
    chassis_result = apply_assembly(project, group_id, chassis)
    assert chassis_result["groups"][group_id]["initial_geometry"] != original
    valid_project(chassis_result)


@pytest.mark.parametrize(
    "mutator,code",
    [
        (lambda value: value["provenance"].update(reference=""), "assembly.provenance"),
        (lambda value: value.update(kind="gene"), "assembly.kind"),
        (lambda value: value.update(version="1.0"), "assembly.version"),
        (lambda value: value["graph"]["nodes"][0].update(owner={"kind": "population", "id": "other"}), "assembly.owner"),
    ],
)
def test_malformed_or_incompatible_assemblies_are_rejected(mutator, code):
    project = make_example("center-pts-a-small")
    assembly = extract_assembly(project, "cells", metadata())
    mutator(assembly)
    with pytest.raises(ProtocolError, match=code):
        validate_assembly(assembly)


def test_unknown_binding_and_empty_target_are_rejected():
    project = make_example("center-pts-a-small")
    assembly = extract_assembly(project, "cells", metadata())
    req = assembly["input_requirements"][0]
    with pytest.raises(ProtocolError, match="assembly.binding"):
        apply_assembly(project, "cells", assembly,
                       bindings={req["id"]: {"node": "missing", "port": req["source"]["port"]}})

    empty = deepcopy(project)
    empty["groups"]["empty"] = deepcopy(empty["groups"]["cells"])
    empty["groups"]["empty"]["ids"] = [f"empty-{cell}" for cell in empty["groups"]["empty"]["ids"]]
    with pytest.raises(ProtocolError, match="assembly.empty_target"):
        apply_assembly(empty, "empty", assembly)
