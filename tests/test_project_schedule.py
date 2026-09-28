from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from friskoli_cad.engine import SimulationError
from friskoli_cad.project import ControlSchedule, simulation_from_project
from friskoli_cad.protocol import ProtocolError


PROJECT_PATH = Path(__file__).resolve().parents[1] / "examples" / "runtime" / "scheduled_inputs.project.json"


def project_document(geometry: str = "thin_layer") -> dict:
    document = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    if geometry == "volume":
        document["domain"] = {
            "geometry": "volume", "counts_xyz": [4, 2, 3], "spacing_um_xyz": [5, 5, 5]
        }
        document["groups"]["group_1"]["positions_um"][0][2] = 7.5
    return document


def registered_only_oxygen(document: dict) -> dict:
    document = deepcopy(document)
    document["controls"].pop("oxygen_feed")
    inactive_nodes = {"oxygen_field", "oxygen_input"}
    document["graph"]["nodes"] = [
        node for node in document["graph"]["nodes"] if node["id"] not in inactive_nodes
    ]
    document["graph"]["edges"] = [
        edge for edge in document["graph"]["edges"]
        if edge["from"]["node"] not in inactive_nodes
        and edge["to"]["node"] not in inactive_nodes
    ]
    return document


class ProjectScheduleTests(unittest.TestCase):
    def test_registered_only_oxygen_does_not_enter_runtime_or_change_results(self):
        document = registered_only_oxygen(project_document())
        without_oxygen = deepcopy(document)
        without_oxygen["species"].pop("oxygen")
        registered = simulation_from_project(document)
        omitted = simulation_from_project(without_oxygen)
        self.assertIn("oxygen", document["species"])
        self.assertEqual(set(registered.world.species_initial_uM), {"substrate"})
        self.assertEqual(set(registered.current.concentration_fields), {"substrate"})
        self.assertNotIn("oxygen_field", registered.current.environment_fields)
        self.assertEqual(
            tuple(node.id for node in registered.plan.nodes),
            tuple(node.id for node in omitted.plan.nodes),
        )
        for _ in range(4):
            with_registered = registered.step(0.25)
            without_registered = omitted.step(0.25)
            np.testing.assert_array_equal(
                with_registered.concentration_fields["substrate"],
                without_registered.concentration_fields["substrate"],
            )
            self.assertEqual(with_registered.cell_frame, without_registered.cell_frame)

    def test_versioned_capsule_geometry_is_known_only_when_declared(self):
        legacy = project_document()
        self.assertEqual(simulation_from_project(legacy).world.groups["group_1"].geometry, (None,))
        upgraded = deepcopy(legacy)
        upgraded["project_version"] = "0.2.0"
        upgraded["groups"]["group_1"]["initial_geometry"] = [{
            "shape": "capsule", "length_um": 2.0, "diameter_um": 0.8,
            "provenance": {"kind": "example", "reference": "illustrative dimensions only"},
        }]
        known = simulation_from_project(upgraded)
        capsule = known.world.groups["group_1"].geometry[0]
        self.assertEqual((capsule.length_um, capsule.diameter_um), (2.0, 0.8))
        old_step = simulation_from_project(legacy).step(0.25)
        new_step = known.step(0.25)
        np.testing.assert_array_equal(
            old_step.concentration_fields["substrate"], new_step.concentration_fields["substrate"]
        )
        self.assertEqual(known.world.groups["group_1"].geometry[0], capsule)
        unknown = deepcopy(upgraded)
        unknown["groups"]["group_1"]["initial_geometry"] = [None]
        self.assertEqual(simulation_from_project(unknown).world.groups["group_1"].geometry, (None,))
        mixed = deepcopy(upgraded)
        group = mixed["groups"]["group_1"]
        group["ids"].append("cell_1")
        group["positions_um"].append([7.5, 2.5, 0.5])
        group["orientation_xyzw"].append([0, 0, 0, 1])
        group["initial_geometry"].append(None)
        self.assertEqual(
            tuple(item is not None for item in simulation_from_project(mixed).world.groups["group_1"].geometry),
            (True, False),
        )
        with self.assertRaises(ProtocolError) as caught:
            wrong_version = deepcopy(upgraded)
            wrong_version["project_version"] = "0.1.0"
            simulation_from_project(wrong_version)
        self.assertEqual(caught.exception.code, "project.schema")

    def test_thin_layer_rejects_capsules_that_do_not_fit(self):
        document = project_document()
        document["project_version"] = "0.2.0"
        geometry = {
            "shape": "capsule", "length_um": 2.0, "diameter_um": 0.8,
            "provenance": {"kind": "example", "reference": "geometry check"},
        }
        document["groups"]["group_1"]["initial_geometry"] = [geometry]
        simulation_from_project(document)
        too_wide = deepcopy(document)
        too_wide["groups"]["group_1"]["initial_geometry"][0]["diameter_um"] = 1.0
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(too_wide)
        self.assertEqual(caught.exception.code, "cell.geometry")
        pitched = deepcopy(document)
        pitched["groups"]["group_1"]["orientation_xyzw"] = [[0, 2**-0.5, 0, 2**-0.5]]
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(pitched)
        self.assertEqual(caught.exception.code, "cell.geometry")
        off_center = deepcopy(document)
        off_center["groups"]["group_1"]["positions_um"] = [[2.5, 2.5, 0.3]]
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(off_center)
        self.assertEqual(caught.exception.code, "cell.geometry")
        malformed = deepcopy(document)
        malformed["groups"]["group_1"]["initial_geometry"][0]["length_um"] = 0.7
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(malformed)
        self.assertEqual(caught.exception.code, "cell.geometry")
        volume = project_document("volume")
        volume["project_version"] = "0.2.0"
        volume["groups"]["group_1"]["initial_geometry"] = [geometry]
        simulation_from_project(volume)

    def test_rate_does_not_change_before_a_nearby_boundary(self):
        schedule = ControlSchedule("substrate", ((0, 0), (0.5, 0.2), (1.0, 0)), 1.5)
        self.assertEqual(schedule.rate_and_next_change(0.5 - 5e-13), (0, 0.5))
        self.assertEqual(schedule.rate_and_next_change(1.5 - 5e-13), (0, 1.5))

    def test_two_species_and_repeating_inputs_have_separate_material_accounts(self):
        for geometry in ("thin_layer", "volume"):
            with self.subTest(geometry=geometry):
                simulation = simulation_from_project(project_document(geometry))
                copy = simulation_from_project(project_document(geometry))
                initial = {
                    species: field.sum() * simulation.world.grid.molecules_per_uM_voxel
                    for species, field in simulation.current.concentration_fields.items()
                }
                for index in range(9):
                    snapshot = simulation.step(0.25)
                    reproduced = copy.step(0.25)
                    for species in ("substrate", "oxygen"):
                        np.testing.assert_array_equal(
                            snapshot.concentration_fields[species],
                            reproduced.concentration_fields[species],
                        )
                        field_node = f"{species}_field"
                        external = snapshot.environment_fields[field_node]["cumulative_external"].values.sum()
                        remaining = (
                            snapshot.concentration_fields[species].sum()
                            * simulation.world.grid.molecules_per_uM_voxel
                        )
                        uptake = (
                            snapshot.cell_frame["cells"][0]["channels"]["uptake.cumulative"]
                            if species == "substrate" else 0
                        )
                        self.assertAlmostEqual(initial[species] + external - uptake, remaining, delta=1e-6)
                    substrate_rate = snapshot.environment_fields["substrate_input"]["external_rate"].values
                    oxygen_rate = snapshot.environment_fields["oxygen_input"]["external_rate"].values
                    if index == 2:  # [0.5, 0.75): the first substrate pulse is applied.
                        np.testing.assert_array_equal(substrate_rate, 0.2)
                        np.testing.assert_array_equal(oxygen_rate, 0)
                    if index == 4:  # [1.0, 1.25): oxygen receives a separate input.
                        np.testing.assert_array_equal(substrate_rate, 0)
                        np.testing.assert_array_equal(oxygen_rate, 0.1)
                    if index == 8:  # [2.0, 2.25): the substrate program has repeated.
                        np.testing.assert_array_equal(substrate_rate, 0.2)

    def test_change_inside_step_is_rejected_without_advancing(self):
        simulation = simulation_from_project(project_document())
        simulation.step(0.25)
        before = simulation.current.concentration_fields["substrate"].copy()
        with self.assertRaises(SimulationError) as caught:
            simulation.step(0.55)
        self.assertEqual(caught.exception.code, "schedule.boundary")
        self.assertEqual(simulation.time_s, 0.25)
        self.assertEqual(simulation.frame_index, 1)
        np.testing.assert_array_equal(simulation.current.concentration_fields["substrate"], before)
        simulation.step(0.25)
        self.assertEqual(simulation.time_s, 0.5)

    def test_decimal_steps_land_on_a_change_without_false_rejection(self):
        simulation = simulation_from_project(project_document())
        for _ in range(6):
            snapshot = simulation.step(0.1)
        self.assertAlmostEqual(snapshot.cell_frame["time_s"], 0.6)
        np.testing.assert_array_equal(
            snapshot.environment_fields["substrate_input"]["external_rate"].values,
            np.full(simulation.world.grid.shape, 0.2),
        )

    def test_zero_program_and_negative_external_rate(self):
        document = project_document()
        for control in document["controls"].values():
            control["changes"] = [control["changes"][0]]
            control.pop("repeat_period_s", None)
        simulation = simulation_from_project(document)
        simulation.step(0.25)
        self.assertEqual(
            simulation.current.environment_fields["oxygen_field"]["cumulative_external"].values.sum(), 0
        )

        removed = deepcopy(document)
        removed["controls"]["oxygen_feed"]["changes"][0]["rate_uM_s"] = -0.1
        simulation = simulation_from_project(removed)
        for _ in range(4):
            snapshot = simulation.step(0.25)
        np.testing.assert_allclose(snapshot.concentration_fields["oxygen"], 4.9)
        self.assertLess(
            snapshot.environment_fields["oxygen_field"]["cumulative_external"].values.sum(), 0
        )

    def test_over_removal_rejects_the_whole_step(self):
        document = project_document()
        document["controls"]["oxygen_feed"]["changes"] = [{
            "time_s": 0, "rate_uM_s": -100,
            "provenance": {"kind": "example", "reference": "over-removal check"},
        }]
        simulation = simulation_from_project(document)
        with self.assertRaises(SimulationError) as caught:
            simulation.step(0.25)
        self.assertEqual(caught.exception.code, "field.depleted")
        self.assertEqual(simulation.time_s, 0)
        np.testing.assert_array_equal(simulation.current.concentration_fields["oxygen"], 5)

    def test_project_rejects_undeclared_species_units_and_unsorted_changes(self):
        for change, expected in (
            (lambda doc: doc["controls"]["oxygen_feed"].update({"species": "ghost"}), "project.control"),
            (lambda doc: doc["controls"]["oxygen_feed"].update({"rate_unit": "mM/s"}), "project.schema"),
            (lambda doc: doc["controls"]["oxygen_feed"]["changes"][1].update({"time_s": 0}), "project.control_time"),
            (lambda doc: doc["graph"]["nodes"][0]["parameters"]["species"].update({"value": "ghost"}), "edge.species"),
        ):
            with self.subTest(expected=expected):
                document = project_document()
                change(document)
                with self.assertRaises(ProtocolError) as caught:
                    simulation_from_project(document)
                self.assertEqual(caught.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
