import json
import unittest
from copy import deepcopy
from importlib.resources import files

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


class WorkspaceSchemaTests(unittest.TestCase):
    def test_registry_metadata_and_collapsed_layout_validate_with_project_references(self):
        schemas = [json.loads(p.read_text(encoding="utf-8")) for p in
                   files("friskoli_cad.protocol").joinpath("schemas").iterdir() if p.name.endswith(".json")]
        registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
        schema = next(s for s in schemas if s["$id"] == "urn:friskoli:workspace:0.3.0")
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, registry=registry)
        project = json.loads(files("friskoli_cad").joinpath("examples", "registry_readout.project.json").read_text(encoding="utf-8"))
        document = {"workspace_format_version": "0.3.0", "project": project,
                    "population_blocks": [{"id": "group_1", "name": "example", "center": [5,5,5],
                        "size": [4,4,4], "count": 1, "length": 2, "diameter": 1, "seed": 1,
                        "dirty": False, "object_type": "core.population", "binding": {"data_nodes": ["geometry"]}}],
                    "graph_layout": {"geometry": {"x": 70, "y": 70, "collapsed": True}},
                    "run_settings": {"dt_s": .1, "steps": 2}}
        validator.validate(document)
        for mutate in (lambda d: d.update(workspace_format_version="9.0.0"),
                       lambda d: d["graph_layout"]["geometry"].update(collapsed="yes"),
                       lambda d: d["population_blocks"][0]["binding"]["data_nodes"].append("geometry")):
            bad = deepcopy(document)
            mutate(bad)
            self.assertTrue(list(validator.iter_errors(bad)))


if __name__ == "__main__":
    unittest.main()
