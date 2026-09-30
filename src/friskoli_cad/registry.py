"""Versioned catalog adapter around unchanged module 0.1 manifests.

Declarations describe editor capabilities. They never provide executable code.
"""
from __future__ import annotations

from copy import deepcopy

from .protocol import ProtocolError, validate_manifest
from .protocol.validation import _check_schema


# These fields are interpreted by the versioned population.block@1 adapter.
# New initializer algorithms need their own contract, not alternate field types.
_BLOCK_FIELDS = {
    "count": ("integer", "1"), "seed": ("integer", "1"),
    "length": ("number", "um"), "diameter": ("number", "um"),
    "center": ("vector3", "um"), "size": ("vector3", "um"),
    "rotation": ("vector3", "degree"),
}


def module_key(manifest):
    return f"{manifest['id']}@{manifest['version']}"


def _port_contract(port, semantics="legacy-explicit-v1"):
    prefix = port["shape"].split(".")[0]
    return {"shape": port["shape"], "quantity": port["quantity"], "unit": port["unit"],
            "entity": {"cell": "owner.cells", "field": "world.grid", "source": "owner.source",
                       "global": "world", "event": "events"}[prefix],
            "time": semantics + "; timing is declared on each edge",
            "species_parameter": port.get("species_parameter")}


def validate_catalog(catalog):
    _check_schema("catalog-v0.2" if catalog.get("catalog_version") == "0.2.0" else "catalog", catalog)
    manifests = {}
    for index, manifest in enumerate(catalog["modules"]):
        validate_manifest(manifest)
        key = module_key(manifest)
        if key in manifests:
            raise ProtocolError("catalog.duplicate", f"/modules/{index}", key)
        manifests[key] = manifest
    entries = {}
    for index, entry in enumerate(catalog["entries"]):
        key = entry["key"]
        if key in entries:
            raise ProtocolError("catalog.duplicate", f"/entries/{index}/key", key)
        if key not in manifests:
            raise ProtocolError("catalog.reference", f"/entries/{index}/key", key)
        expected = {side: {name: _port_contract(port, catalog["execution_semantics"]) for name, port in manifests[key][side].items()}
                    for side in ("inputs", "outputs")}
        if entry["ports"] != expected:
            raise ProtocolError("catalog.port", f"/entries/{index}/ports", "declaration differs from executable manifest")
        math = entry["mathematics"]
        if math["kind"] == "equations" and not math["equations"]:
            raise ProtocolError("catalog.equations", f"/entries/{index}/mathematics", "equations are required")
        if math["verification"]["status"] == "tested" and not math["verification"]["tests"]:
            raise ProtocolError("catalog.evidence", f"/entries/{index}/mathematics", "test references are required")
        entries[key] = entry
    if set(entries) != set(manifests):
        raise ProtocolError("catalog.coverage", "/entries", "every runtime needs a declaration")
    ids = set()
    for index, item in enumerate(catalog["objects"]):
        if item["id"] in ids:
            raise ProtocolError("catalog.duplicate", f"/objects/{index}/id", item["id"])
        ids.add(item["id"])
        initializer = item["initializer"]
        references = [("module", initializer["module"])] + [
            (f"data_modules/{i}", key) for i, key in enumerate(initializer["data_modules"])
        ]
        for field, key in references:
            path = f"/objects/{index}/initializer/{field}"
            if key not in manifests or manifests[key]["scope"] != "population":
                raise ProtocolError("catalog.reference", path, key)
            if initializer["adapter"] == "population.block@1":
                if manifests[key]["parameters"] or manifests[key]["inputs"]:
                    raise ProtocolError("catalog.initializer", path,
                                        "block initializer cannot supply parameters or inputs")
                if field.startswith("data_modules/") and entries[key]["world_access"] != "read_only":
                    raise ProtocolError("catalog.initializer", path,
                                        "data modules must be read-only")
        if initializer["module"] in initializer["data_modules"]:
            raise ProtocolError("catalog.duplicate", f"/objects/{index}/initializer/data_modules",
                                "initializer is also listed as a data module")
        paths = [field["path"] for field in item["properties"]]
        if len(paths) != len(set(paths)):
            raise ProtocolError("catalog.duplicate", f"/objects/{index}/properties", "duplicate property path")
        for property_index, prop in enumerate(item["properties"]):
            path = f"/objects/{index}/properties/{property_index}"
            lower, upper = prop.get("minimum"), prop.get("maximum")
            if lower is not None and upper is not None and lower > upper:
                raise ProtocolError("catalog.property_range", path, "minimum exceeds maximum")
            if prop["type"] == "integer" and any(
                bound is not None and int(bound) != bound for bound in (lower, upper)
            ):
                raise ProtocolError("catalog.property_range", path, "integer bounds must be integral")
            if initializer["adapter"] == "population.block@1":
                if (prop["type"], prop["unit"]) != _BLOCK_FIELDS[prop["path"]]:
                    raise ProtocolError("catalog.property_type", path, "field differs from block adapter contract")
                if prop["path"] == "count" and (lower is None or lower < 1 or upper is None or upper > 2000):
                    raise ProtocolError("catalog.property_range", path, "count must stay within 1..2000")
                if prop["path"] in ("length", "diameter", "size") and (lower is None or lower < 0):
                    raise ProtocolError("catalog.property_range", path, "geometry bounds must be nonnegative")
        if initializer["adapter"] == "population.block@1" and set(paths) != set(_BLOCK_FIELDS):
            raise ProtocolError("catalog.property_set", f"/objects/{index}/properties",
                                "block adapter requires all declared placement fields")
    return catalog


def build_catalog(modules, execution_semantics="legacy-explicit-v1"):
    catalog = {"catalog_version": "0.1.0" if execution_semantics == "legacy-explicit-v1" else "0.2.0", "module_protocol_versions": ["0.1.0"],
               "execution_semantics": execution_semantics, "modules": [], "entries": [], "objects": []}
    for module in modules:
        manifest = deepcopy(dict(module.manifest))
        declaration = deepcopy(getattr(module, "declaration", {}))
        entry = {"key": module_key(manifest), "label": manifest["id"],
                 "category": manifest["scientific_role"], "summary": manifest.get("description", manifest["id"]),
                 "devices": ["cpu"],
                 "world_access": getattr(module, "world_access", "legacy_inferred"), "mathematics": {
                     "kind": "undocumented", "equations": [], "algorithm": "",
                     "implementation": f"{type(module).__module__}.{type(module).__name__}",
                     "verification": {"status": "unreviewed", "tests": []},
                     "assumptions": ["Scientific explanation has not yet been registered."]},
                 "ports": {side: {name: _port_contract(port, execution_semantics) for name, port in manifest[side].items()}
                           for side in ("inputs", "outputs")}}
        entry.update(declaration)
        if entry["world_access"] != getattr(module, "world_access", "legacy_inferred"):
            raise ProtocolError("catalog.world_access", f"/entries/{len(catalog['entries'])}/world_access",
                                "declaration differs from executable module")
        if entry["key"] != module_key(manifest):
            raise ProtocolError("catalog.reference", f"/entries/{len(catalog['entries'])}/key",
                                "declaration differs from executable module")
        mathematics = entry.get("mathematics")
        implementation = f"{type(module).__module__}.{type(module).__name__}"
        if isinstance(mathematics, dict) and mathematics.get("implementation", implementation) != implementation:
            raise ProtocolError("catalog.implementation",
                                f"/entries/{len(catalog['entries'])}/mathematics/implementation",
                                "source reference differs from executable module")
        catalog["modules"].append(manifest)
        catalog["entries"].append(entry)
        catalog["objects"].extend(deepcopy(getattr(module, "object_types", [])))
    return validate_catalog(catalog)
