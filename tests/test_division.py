from __future__ import annotations

import json
import math
import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from friskoli_cad.engine import (
    CapsuleGeometry, CellGroup, GridDomain, ModuleRegistry, Simulation, SimulationError, World,
    default_registry,
)
from friskoli_cad.engine.modules import LengthAdder, LinearElongation
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import validate_frame_sequence


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "runtime"


def load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def capsule_volume(length: float, diameter: float) -> float:
    return math.pi * (diameter / 2) ** 2 * (length - diameter / 3)


def add_growth_and_adder(graph: dict, *, rate: float = 0.5, increment: float = 1.0) -> dict:
    document = load("adder_division.project.json")
    updated = deepcopy(graph)
    growth, adder = deepcopy(document["graph"]["nodes"])
    growth["parameters"]["elongation_rate"]["value"] = rate
    adder["parameters"]["added_length_um"]["value"] = increment
    updated["nodes"].extend((growth, adder))
    updated["edges"].append(deepcopy(document["graph"]["edges"][0]))
    return updated


class DivisionTests(unittest.TestCase):
    def test_adder_creates_daughters_and_rebases_their_length_history(self):
        document = load("adder_division.project.json")
        simulation = simulation_from_project(document)
        frames = [simulation.current.cell_frame]
        for _ in range(8):
            frames.append(simulation.step(0.5).cell_frame)
        validate_frame_sequence(frames, document["run"])
        self.assertEqual([len(frame["cells"]) for frame in frames], [1, 1, 1, 1, 2, 2, 2, 2, 4])
        self.assertEqual(frames[4]["events"], [{
            "type": "division", "time_s": 2.0, "parent_id": "cell_0", "child_id": "cell_0~1",
        }])
        self.assertEqual(
            [cell["id"] for cell in frames[4]["cells"]], ["cell_0", "cell_0~1"],
        )
        self.assertEqual(
            [cell["channels"]["adder.added_length"] for cell in frames[4]["cells"]], [0, 0],
        )
        self.assertEqual(
            [cell["channels"]["adder.added_length"] for cell in frames[5]["cells"]], [0.25, 0.25],
        )
        self.assertEqual(len(frames[8]["events"]), 2)
        self.assertEqual(len(set(cell["id"] for cell in frames[8]["cells"])), 4)
        self.assertEqual(set(simulation.current.concentration_fields), set())

        daughter = frames[4]["cells"][0]["geometry"]
        self.assertAlmostEqual(
            2 * capsule_volume(daughter["length_um"], daughter["diameter_um"]),
            capsule_volume(3.0, 0.8), places=12,
        )
        centers = [cell["position_um"][0] for cell in frames[4]["cells"]]
        self.assertAlmostEqual(centers[1] - centers[0], daughter["length_um"])

    def test_adder_tracks_added_length_not_absolute_birth_size(self):
        document = load("adder_division.project.json")
        group = document["groups"]["group_1"]
        group["ids"].append("large_at_birth")
        group["positions_um"].append([15, 2.5, 0.5])
        group["orientation_xyzw"].append([0, 0, 0, 1])
        geometry = deepcopy(group["initial_geometry"][0])
        geometry["length_um"] = 3.0
        group["initial_geometry"].append(geometry)
        simulation = simulation_from_project(document)
        for _ in range(3):
            self.assertEqual(simulation.step(0.5).cell_frame["events"], [])
        frame = simulation.step(0.5).cell_frame
        self.assertEqual(len(frame["events"]), 2)
        self.assertEqual([event["parent_id"] for event in frame["events"]], ["cell_0", "large_at_birth"])
        self.assertEqual(len(frame["cells"]), 4)

    def test_daughter_spacing_also_follows_heading_in_3d(self):
        document = load("adder_division.project.json")
        document["domain"] = {
            "geometry": "volume", "counts_xyz": [1, 1, 4], "spacing_um_xyz": [5, 5, 5],
        }
        group = document["groups"]["group_1"]
        group["positions_um"] = [[2.5, 2.5, 10]]
        group["orientation_xyzw"] = [[0, -math.sqrt(0.5), 0, math.sqrt(0.5)]]
        simulation = simulation_from_project(document)
        for _ in range(4):
            frame = simulation.step(0.5).cell_frame
        cells = frame["cells"]
        self.assertEqual(len(cells), 2)
        self.assertAlmostEqual(cells[1]["position_um"][2] - cells[0]["position_um"][2],
                               cells[0]["geometry"]["length_um"])
        self.assertAlmostEqual(cells[0]["position_um"][0], 2.5)
        self.assertAlmostEqual(cells[1]["position_um"][0], 2.5)

    def test_impossible_daughter_geometry_and_outside_position_roll_back(self):
        short = load("adder_division.project.json")
        short["groups"]["group_1"]["initial_geometry"][0]["length_um"] = 1.0
        short["graph"]["nodes"][1]["parameters"]["added_length_um"]["value"] = 0.25
        simulation = simulation_from_project(short)
        with self.assertRaises(SimulationError) as caught:
            simulation.step(0.5)
        self.assertEqual(caught.exception.code, "division.geometry")
        self.assertEqual((simulation.time_s, simulation.frame_index), (0, 0))
        self.assertEqual(simulation.world.groups["group_1"].geometry[0], CapsuleGeometry(1.0, 0.8))
        self.assertEqual(simulation._next_cell_serial, 1)

        outside = load("adder_division.project.json")
        outside["groups"]["group_1"]["positions_um"][0][0] = 0.4
        outside["graph"]["nodes"][1]["parameters"]["added_length_um"]["value"] = 0.25
        simulation = simulation_from_project(outside)
        with self.assertRaises(SimulationError) as caught:
            simulation.step(0.5)
        self.assertEqual(caught.exception.code, "cell.position")
        self.assertEqual((simulation.time_s, simulation.frame_index), (0, 0))

    def test_uptake_history_splits_and_field_balance_survives_division(self):
        graph = add_growth_and_adder(load("uptake.graph.json"))
        run = load("uptake.run.json")
        group = CellGroup(
            "group_1", ("cell_0",), np.array([[10, 2.5, 0.5]]),
            np.array([[0, 0, 0, 1]]), (CapsuleGeometry(2.0, 0.8),),
        )
        world = World(GridDomain.thin_layer(4, 1, 5, 5, 1), {group.id: group})
        simulation = Simulation(world, graph, run, default_registry())
        initial_molecules = simulation.current.concentration_fields["substrate"].sum() * world.grid.molecules_per_uM_voxel
        for _ in range(4):
            frame = simulation.step(0.5).cell_frame
        self.assertEqual(len(frame["cells"]), 2)
        cumulative = [cell["channels"]["uptake.cumulative"] for cell in frame["cells"]]
        self.assertAlmostEqual(cumulative[0], cumulative[1])
        np.testing.assert_allclose(
            simulation.outputs["deposit"]["consumption_rate"].sum()
            * world.grid.molecules_per_uM_voxel,
            sum(cell["channels"]["uptake.flux"] for cell in frame["cells"]),
        )
        for _ in range(2):
            frame = simulation.step(0.5).cell_frame
        final_molecules = simulation.current.concentration_fields["substrate"].sum() * world.grid.molecules_per_uM_voxel
        self.assertAlmostEqual(
            initial_molecules - final_molecules,
            sum(cell["channels"]["uptake.cumulative"] for cell in frame["cells"]),
            places=8,
        )

    def test_motion_pose_is_rebased_after_division(self):
        graph = add_growth_and_adder(load("moving_uptake.graph.json"))
        graph["nodes"][2]["parameters"]["speed_um_s"]["value"] = 0.5
        run = load("uptake.run.json")
        run["graph_id"] = graph["id"]
        group = CellGroup(
            "group_1", ("cell_0",), np.array([[10, 2.5, 0.5]]),
            np.array([[0, 0, 0, 1]]), (CapsuleGeometry(2.0, 0.8),),
        )
        world = World(GridDomain.thin_layer(4, 1, 5, 5, 1), {group.id: group})
        simulation = Simulation(world, graph, run, default_registry())
        for _ in range(4):
            frame = simulation.step(0.5).cell_frame
        self.assertEqual(len(frame["events"]), 1)
        np.testing.assert_allclose(
            simulation.state["motion"]["position"], simulation.world.groups["group_1"].positions_um,
        )
        self.assertEqual(len(simulation.step(0.5).cell_frame["cells"]), 2)

    def test_division_at_input_boundary_keeps_completed_interval_rate(self):
        document = load("scheduled_inputs.project.json")
        document["project_version"] = "0.2.0"
        document["groups"]["group_1"]["positions_um"][0][0] = 10
        document["groups"]["group_1"]["initial_geometry"] = deepcopy(
            load("adder_division.project.json")["groups"]["group_1"]["initial_geometry"]
        )
        document["graph"] = add_growth_and_adder(document["graph"], increment=0.25)
        simulation = simulation_from_project(document)
        divided = simulation.step(0.5)
        self.assertEqual(len(divided.cell_frame["events"]), 1)
        self.assertTrue(np.all(simulation.outputs["substrate_input"]["external_rate"] == 0))
        self.assertTrue(np.all(
            divided.environment_fields["substrate_field"]["external_flux"].values == 0
        ))
        following = simulation.step(0.5)
        self.assertTrue(np.all(simulation.outputs["substrate_input"]["external_rate"] == 0.2))
        self.assertTrue(np.all(
            following.environment_fields["substrate_field"]["external_flux"].values > 0
        ))

    def test_zero_threshold_and_duplicate_division_provider_fail(self):
        zero = load("adder_division.project.json")
        zero["graph"]["nodes"][1]["parameters"]["added_length_um"]["value"] = 0
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(zero)
        self.assertEqual(caught.exception.code, "division.threshold")
        duplicate = load("adder_division.project.json")
        second = deepcopy(duplicate["graph"]["nodes"][1])
        second["id"] = "second_adder"
        duplicate["graph"]["nodes"].append(second)
        edge = deepcopy(duplicate["graph"]["edges"][0])
        edge["id"] = "growth_to_second_adder"
        edge["to"]["node"] = "second_adder"
        duplicate["graph"]["edges"].append(edge)
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(duplicate)
        self.assertEqual(caught.exception.code, "division.provider")

    def test_division_requires_refresh_from_every_participating_module(self):
        growth = LinearElongation()

        class GrowthWithoutRefresh:
            manifest = growth.manifest
            initialize = growth.initialize
            advance = growth.advance

        registry = ModuleRegistry([GrowthWithoutRefresh(), LengthAdder()])
        with self.assertRaises(SimulationError) as caught:
            simulation_from_project(load("adder_division.project.json"), registry)
        self.assertEqual(caught.exception.code, "division.refresh")


if __name__ == "__main__":
    unittest.main()
