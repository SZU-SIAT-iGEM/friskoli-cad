from __future__ import annotations

import json
import math
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from friskoli_cad.engine import default_registry, SimulationError
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import ProtocolError, validate_graph
from friskoli_cad.registry import build_catalog, validate_catalog
from friskoli_cad.replay_service import EXAMPLE_PROJECT


ROOT = Path(__file__).resolve().parents[1]


def reader(project):
    node = {"id": "geometry", "module_id": "geometry.capsule_readout", "module_version": "1.0.0",
            "owner": {"kind": "population", "id": "group_1"}, "parameters": {}}
    project["graph"]["nodes"].append(node)
    manifest = default_registry().get(node["module_id"], node["module_version"]).manifest
    for name in ("surface_area", "volume", "length", "diameter"):
        project["run"]["channels"][f"geometry.{name}"] = {
            "node": "geometry", "port": name, "group_id": "group_1", **manifest["outputs"][name]}
    return project


class RegistryTests(unittest.TestCase):
    def assert_catalog_error(self, mutate, code, path):
        catalog = default_registry().catalog
        mutate(catalog)
        with self.assertRaises(ProtocolError) as caught:
            validate_catalog(catalog)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.path, path)

    def test_duplicate_catalog_identifiers_are_located(self):
        for collection, suffix in (("modules", ""), ("entries", "/key"), ("objects", "/id")):
            with self.subTest(collection=collection):
                count = len(default_registry().catalog[collection])
                self.assert_catalog_error(
                    lambda c: c[collection].append(deepcopy(c[collection][0])),
                    "catalog.duplicate", f"/{collection}/{count}{suffix}",
                )

    def test_world_access_cannot_override_execution_capability(self):
        actual = default_registry().get("geometry.capsule_readout", "1.0.0")
        for executable, declared in (("read_only", "legacy_inferred"), ("legacy_inferred", "read_only")):
            with self.subTest(executable=executable):
                module = SimpleNamespace(manifest=actual.manifest, world_access=executable,
                                         declaration={"world_access": declared})
                with self.assertRaises(ProtocolError) as caught:
                    build_catalog([module])
                self.assertEqual(caught.exception.code, "catalog.world_access")
                self.assertEqual(caught.exception.path, "/entries/0/world_access")
        self.assert_catalog_error(lambda c: c["entries"][0].pop("world_access"),
                                  "schema.invalid", "/entries/0")

    def test_initializer_properties_match_adapter_types_and_ranges(self):
        mutations = [(0, {"type": "number"}, "catalog.property_type"),
                     (1, {"unit": "uM"}, "catalog.property_type"),
                     (0, {"minimum": 3, "maximum": 2}, "catalog.property_range"),
                     (0, {"minimum": 1.5}, "catalog.property_range"),
                     (0, {"maximum": 2001}, "catalog.property_range"),
                     (2, {"minimum": -1}, "catalog.property_range")]
        for index, patch, code in mutations:
            with self.subTest(index=index, patch=patch):
                self.assert_catalog_error(
                    lambda c: c["objects"][0]["properties"][index].update(patch),
                    code, f"/objects/0/properties/{index}",
                )
        self.assert_catalog_error(lambda c: c["objects"][0]["properties"].pop(),
                                  "catalog.property_set", "/objects/0/properties")

    def test_declarations_cannot_override_identity_ports_or_source_reference(self):
        actual = default_registry().get("geometry.capsule_readout", "1.0.0")
        for patch, code, path in (
            ({"key": "geometry.other@1.0.0"}, "catalog.reference", "/entries/0/key"),
            ({"ports": {"inputs": {}, "outputs": {}}}, "catalog.port", "/entries/0/ports"),
            ({"mathematics": {"implementation": "invented.Source"}},
             "catalog.implementation", "/entries/0/mathematics/implementation"),
        ):
            with self.subTest(code=code):
                module = deepcopy(actual)
                module.declaration.update(patch)
                with self.assertRaises(ProtocolError) as caught:
                    build_catalog([module])
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.path, path)

    def test_initializer_references_must_be_executable_without_missing_inputs(self):
        self.assert_catalog_error(
            lambda c: c["objects"][0]["initializer"].update(module="field.sample_box_support@2.0.0"),
            "catalog.initializer", "/objects/0/initializer/module",
        )
        self.assert_catalog_error(
            lambda c: c["objects"][0]["initializer"].update(data_modules=["population.static@1.0.0"]),
            "catalog.initializer", "/objects/0/initializer/data_modules/0",
        )
        self.assert_catalog_error(
            lambda c: c["objects"][0]["initializer"]["data_modules"].append("geometry.capsule_readout@1.0.0"),
            "schema.invalid", "/objects/0/initializer/data_modules",
        )

    def test_catalog_preserves_legacy_manifests_and_is_detached(self):
        registry = default_registry()
        catalog = registry.catalog
        validate_catalog(catalog)
        self.assertEqual(catalog["modules"], list(registry.manifests))
        self.assertEqual(len(catalog["entries"]), len(catalog["modules"]))
        catalog["objects"][0]["label"] = "changed"
        self.assertNotEqual(registry.catalog["objects"][0]["label"], "changed")
        self.assertEqual(catalog["execution_semantics"], "legacy-explicit-v1")

    def test_catalog_rejects_unavailable_modules_and_contradictory_ports(self):
        for mutation in (lambda c: c["objects"][0]["initializer"].update(module="unknown.module@1.0.0"),
                         lambda c: c["entries"].pop(),
                         lambda c: c["entries"][1]["ports"]["outputs"]["position"].update(quantity="force"),
                         lambda c: c.update(catalog_version="9.0.0")):
            catalog = default_registry().catalog
            mutation(catalog)
            with self.assertRaises(ProtocolError):
                validate_catalog(catalog)


class GeometryTests(unittest.TestCase):
    def project(self, keep_behavior=False):
        project = json.loads(EXAMPLE_PROJECT.read_text(encoding="utf-8"))
        if not keep_behavior:
            project["graph"]["nodes"] = []
            project["graph"]["edges"] = []
            project["run"]["channels"] = {}
        return reader(project)

    def test_capsule_and_sphere_limits_without_state_mutation(self):
        project = self.project()
        geometry = project["groups"]["group_1"]["initial_geometry"]
        for i, item in enumerate(geometry):
            item.update(length_um=1 if i == 0 else 3, diameter_um=1)
        before = deepcopy(project)
        simulation = simulation_from_project(project)
        simulation.step(.1)
        self.assertEqual(project, before)
        self.assertFalse(simulation._geometry_nodes)
        self.assertFalse(simulation._pose_nodes)
        outputs = simulation.outputs["geometry"]
        self.assertAlmostEqual(outputs["surface_area"][0], math.pi)
        self.assertAlmostEqual(outputs["volume"][0], math.pi / 6)
        self.assertAlmostEqual(outputs["surface_area"][1], 3 * math.pi)
        self.assertAlmostEqual(outputs["volume"][1], 2 * math.pi / 3)
        np.testing.assert_array_equal(outputs["position"], project["groups"]["group_1"]["positions_um"])

    def test_readouts_refresh_with_growth_and_division_without_changing_legacy_result(self):
        original = json.loads(EXAMPLE_PROJECT.read_text(encoding="utf-8"))
        legacy = simulation_from_project(original)
        simulation = simulation_from_project(reader(deepcopy(original)))
        for _ in range(8):
            expected = legacy.step(.5).cell_frame
            actual = simulation.step(.5).cell_frame
            self.assertEqual(actual["events"], expected["events"])
            for cell, old in zip(actual["cells"], expected["cells"], strict=True):
                self.assertEqual(cell["position_um"], old["position_um"])
                self.assertEqual(cell["geometry"], old["geometry"])
                g = cell["geometry"]
                self.assertAlmostEqual(cell["channels"]["geometry.surface_area"], math.pi * g["length_um"] * g["diameter_um"])
                for key, value in old["channels"].items():
                    self.assertEqual(cell["channels"][key], value)

    def test_missing_geometry_is_not_invented(self):
        project = self.project()
        project["groups"]["group_1"]["initial_geometry"][0] = None
        with self.assertRaisesRegex(SimulationError, "geometry.missing") as caught:
            simulation_from_project(project)
        self.assertEqual(caught.exception.path, "/groups/group_1/initial_geometry/0")
        self.assertIn(project["groups"]["group_1"]["ids"][0], str(caught.exception))


class RegistryReadoutFixtureTests(unittest.TestCase):
    def project(self):
        return json.loads((ROOT / "examples/runtime/registry_readout.project.json").read_text(encoding="utf-8"))

    def test_fixture_runs_geometry_position_to_existing_sampler(self):
        project = self.project()
        before = deepcopy(project)
        simulation = simulation_from_project(project)
        self.assertFalse(simulation._geometry_nodes)
        self.assertFalse(simulation._pose_nodes)
        for _ in range(3):
            frame = simulation.step(.25).cell_frame
            cell = frame["cells"][0]
            self.assertEqual(cell["channels"]["sampler.local_concentration"], 5)
            self.assertEqual(cell["position_um"], [5, 2.5, .5])
            self.assertAlmostEqual(cell["channels"]["geometry.surface_area"], math.pi * .8 * 2)
            self.assertAlmostEqual(cell["channels"]["geometry.volume"], math.pi * .8**2 * (2 - .8 / 3) / 4)
        self.assertEqual(project, before)

    def test_same_units_do_not_allow_different_quantities(self):
        project = self.project()
        manifests = deepcopy(default_registry().manifests)
        sampler = next(m for m in manifests if m["id"] == "field.sample_box_support" and m["version"] == "2.0.0")
        sampler["inputs"]["position"]["quantity"] = "displacement"
        with self.assertRaises(ProtocolError) as caught:
            validate_graph(project["graph"], manifests)
        self.assertEqual(caught.exception.code, "edge.type")
        self.assertEqual(caught.exception.path, "/edges/2")

    def test_registered_sampler_matches_declared_overlap_equation(self):
        simulation = simulation_from_project(self.project())
        sampler = simulation.registry.get("field.sample_box_support", "2.0.0")
        node = next(node for node in simulation.plan.nodes if node.id == "sampler")
        # The 2 um support centered at x=5 overlaps two 5 um voxels equally.
        # Moving to x=5.5 changes the fractions to 1/4 and 3/4.
        for x, expected in ((5, 5), (5.5, 6.5)):
            result = sampler.initialize(simulation.world, node, {
                "concentration": np.array([[[2., 8.]]]),
                "position": np.array([[x, 2.5, .5]]),
            })
            self.assertAlmostEqual(result.outputs["local_concentration"][0], expected)

    def test_invalid_positions_and_capsules_have_exact_project_paths(self):
        cases = [
            (lambda g: g["positions_um"][0].__setitem__(0, 10), "cell.position", "positions_um/0"),
            (lambda g: g["positions_um"][0].__setitem__(0, -0.1), "cell.position", "positions_um/0"),
            (lambda g: g["initial_geometry"][0].update(length_um=.4), "cell.geometry", "initial_geometry/0"),
            (lambda g: g["initial_geometry"][0].update(diameter_um=1), "cell.geometry", "initial_geometry/0"),
        ]
        for mutate, code, suffix in cases:
            with self.subTest(code=code, suffix=suffix):
                project = self.project()
                mutate(project["groups"]["group_1"])
                with self.assertRaises(SimulationError) as caught:
                    simulation_from_project(project)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.path, f"/groups/group_1/{suffix}")
                self.assertIn("cell_0", str(caught.exception))

    def test_extra_invalid_capsule_reports_geometry_not_index_error(self):
        project = self.project()
        geometry = project["groups"]["group_1"]["initial_geometry"]
        geometry.append({**geometry[0], "length_um": .4})
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(project)
        self.assertEqual(caught.exception.code, "cell.geometry")
        self.assertEqual(caught.exception.path, "/groups/group_1/initial_geometry/1")

    def test_registered_scientific_explanations_resolve_to_source_and_tests(self):
        registry = default_registry()
        for key in ("geometry.capsule_readout@1.0.0", "field.sample_box_support@2.0.0"):
            entry = next(e for e in registry.catalog["entries"] if e["key"] == key)
            module_id, version = key.split("@")
            implementation = registry.get(module_id, version)
            math_info = entry["mathematics"]
            self.assertEqual(math_info["implementation"],
                             f"{type(implementation).__module__}.{type(implementation).__name__}")
            self.assertEqual(math_info["verification"]["status"], "tested")
            self.assertTrue(math_info["equations"])
            for reference in math_info["verification"]["tests"]:
                self.assertTrue((ROOT / reference.split("::")[0]).is_file(), reference)

    def test_invalid_graph_connections_parameters_and_versions_are_located(self):
        cases = [
            (lambda p: p["graph"]["nodes"][4]["owner"].update(id="group_2"), "edge.population", "/edges/2"),
            (lambda p: p["graph"]["edges"].pop(), "edge.required", "/nodes/4/inputs/position"),
            (lambda p: p["graph"]["nodes"][4]["parameters"].pop("support_x_um"), "parameter.set", "/nodes/4/parameters"),
            (lambda p: p["graph"]["nodes"][4]["parameters"]["support_x_um"].update(value=-1), "parameter.range", "/nodes/4/parameters/support_x_um"),
            (lambda p: p["graph"]["nodes"][1].update(module_version="99.0.0"), "module.unknown", "/nodes/1"),
        ]
        for mutate, code, path in cases:
            with self.subTest(code=code):
                project = self.project()
                mutate(project)
                with self.assertRaises(ProtocolError) as caught:
                    simulation_from_project(project)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.path, path)

    def test_reader_preserves_every_legacy_frame_field(self):
        original = json.loads(EXAMPLE_PROJECT.read_text(encoding="utf-8"))
        legacy = simulation_from_project(original)
        with_reader = simulation_from_project(reader(deepcopy(original)))
        for _ in range(8):
            expected = legacy.step(.5)
            actual = with_reader.step(.5)
            frame = deepcopy(actual.cell_frame)
            for cell in frame["cells"]:
                cell["channels"] = {k: v for k, v in cell["channels"].items() if not k.startswith("geometry.")}
            self.assertEqual(frame, expected.cell_frame)
            self.assertEqual(set(actual.concentration_fields), set(expected.concentration_fields))
            for species in expected.concentration_fields:
                np.testing.assert_array_equal(actual.concentration_fields[species], expected.concentration_fields[species])


if __name__ == "__main__":
    unittest.main()
