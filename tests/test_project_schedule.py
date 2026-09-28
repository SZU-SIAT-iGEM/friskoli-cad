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


class ProjectScheduleTests(unittest.TestCase):
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
