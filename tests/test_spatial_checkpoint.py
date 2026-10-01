"""JSON checkpoint replay and rejection of mismatched spatial state."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

import numpy as np
import rfc8785

from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.runtime import SimulationError
from friskoli_cad.engine.spatial_checkpoint import export_checkpoint, restore_checkpoint


EXAMPLE = Path(__file__).resolve().parents[1] / "src/friskoli_cad/examples/spatial_baseline.project.json"


def reseal(payload):
    payload["payload_sha256"] = hashlib.sha256(rfc8785.dumps(
        {k: v for k, v in payload.items() if k != "payload_sha256"})).hexdigest()
    return payload


class SpatialCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.project = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.sim = simulation_from_project(self.project)
        self.sim.step(.1)
        self.payload = export_checkpoint(self.sim)

    def changed(self, mutate):
        payload = deepcopy(self.payload)
        mutate(payload)
        return reseal(payload)

    def test_json_save_restore_next_steps_exactly_match(self):
        encoded = json.dumps(self.payload, allow_nan=False)
        restored = restore_checkpoint(self.project, json.loads(encoded))
        self.assertEqual(export_checkpoint(restored), self.payload)
        for dt in (.13, .2, .07):
            self.sim.step(dt)
            restored.step(dt)
            self.assertEqual(self.sim.current.cell_frame, restored.current.cell_frame)
            self.assertEqual(export_checkpoint(self.sim), export_checkpoint(restored))
        self.assertEqual(restored.frame_validator.next_index, restored.frame_index + 1)
        self.assertEqual(restored.frame_validator.previous_time, restored.time_s)
        self.assertEqual(restored.frame_validator.alive, self.sim.frame_validator.alive)
        self.assertEqual(restored.frame_validator.seen, self.sim.frame_validator.seen)

    def test_frame_zero_roundtrip_and_factory_wrappers(self):
        sim = simulation_from_project(self.project)
        restored = type(sim).from_checkpoint(self.project, sim.checkpoint())
        self.assertEqual(sim.checkpoint(), restored.checkpoint())
        sim.step(.05)
        restored.step(.05)
        self.assertEqual(sim.checkpoint(), restored.checkpoint())

    def test_explicit_execution_seed_restores_without_rewriting_project(self):
        original = deepcopy(self.project)
        sim = simulation_from_project(self.project, seed=0)
        sim.step(.13)
        restored = type(sim).from_checkpoint(self.project, sim.checkpoint())
        self.assertEqual(restored.seed, 0)
        self.assertEqual(self.project, original)
        self.assertNotEqual(restored.seed, self.project['random_seed'])
        for dt in (.17, .2):
            sim.step(dt)
            restored.step(dt)
            self.assertEqual(sim.checkpoint(), restored.checkpoint())

    def test_bad_hash_project_or_implementation_lock_rejected(self):
        bad = deepcopy(self.payload)
        bad["time_s"] = 2.
        with self.assertRaisesRegex(SimulationError, "payload hash"):
            restore_checkpoint(self.project, bad)
        for mutate in (
            lambda p: p.__setitem__("project_sha256", "0" * 64),
            lambda p: p["implementation_lock"].__setitem__("numpy_version", "0"),
            lambda p: p["implementation_lock"].__setitem__("source_sha256", {}),
        ):
            with self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_clock_seed_and_revision_must_agree(self):
        for mutate in (
            lambda p: p.__setitem__("time_s", 0.),
            lambda p: p.__setitem__("frame_index", -1),
            lambda p: p.__setitem__("frame_index", True),
            lambda p: p.__setitem__("seed", p["seed"] + 1),
            lambda p: p["local_fields"].__setitem__("revision", 0),
        ):
            with self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_world_ids_shapes_and_geometry_cannot_be_replaced(self):
        gid = next(g for g, v in self.payload["world"].items() if v["ids"])
        for mutate in (
            lambda p: p["world"][gid]["ids"].__setitem__(0, "unknown"),
            lambda p: p["world"][gid]["positions_um"][0].pop(),
            lambda p: p["world"][gid]["positions_um"][0].__setitem__(0, -100.),
            lambda p: p["current_frame"]["cells"][0]["geometry"].__setitem__("length_um", 500.),
            lambda p: p["walks"].pop(next(iter(p["walks"]))),
        ):
            with self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_field_configuration_stock_upper_bound_and_shape_are_locked(self):
        species = next(iter(self.payload["local_fields"]["concentrations_uM"]))
        for mutate in (
            lambda p: p["local_fields"]["diffusivities_um2_s"].__setitem__(species, 500.),
            lambda p: p["local_fields"]["concentrations_uM"][species].pop(),
            lambda p: p["local_fields"]["blocked"].__setitem__(0, not p["local_fields"]["blocked"][0]),
            lambda p: p["local_fields"]["sources"][0].__setitem__("remaining_molecules", 1e30),
            lambda p: p["local_fields"]["sources"][0].__setitem__("radius_um", 100.),
        ):
            with self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_outputs_memory_and_rng_must_match_declared_shapes_and_namespaces(self):
        node = next(k for k, v in self.payload["outputs"].items() if "heading" in v)
        field_node = next(k for k, v in self.payload["outputs"].items() if "concentration" in v and np.asarray(v["concentration"]).ndim == 3)
        for mutate in (
            lambda p: p["outputs"][node]["heading"].append([1., 0., 0.]),
            lambda p: p["state"][node]["remaining_wait"].__setitem__(0, 123.),
            lambda p: p["outputs"][field_node]["concentration"][0][0].__setitem__(0, 99.),
            lambda p: p["random_streams"].__setitem__("run_seed", "999999999"),
            lambda p: p["random_streams"]["streams"][0]["key"].__setitem__(2, "foreign-cell"),
            lambda p: p["random_streams"]["streams"][0]["state"]["state"].__setitem__("inc", "0" * 31 + "1"),
        ):
            with self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_nonfinite_or_non_json_objects_are_not_deserialized(self):
        for payload in (b"not an object", "pickle", {"version": "foreign"}):
            with self.assertRaises(SimulationError):
                restore_checkpoint(self.project, payload)
        bad = deepcopy(self.payload)
        bad["time_s"] = float("nan")
        with self.assertRaises(SimulationError):
            restore_checkpoint(self.project, bad)

    def test_restore_does_not_mutate_project_or_checkpoint(self):
        before_project, before_payload = deepcopy(self.project), deepcopy(self.payload)
        restored = restore_checkpoint(self.project, self.payload)
        restored.step(.2)
        self.assertEqual(self.project, before_project)
        self.assertEqual(self.payload, before_payload)

    def test_material_stock_config_and_visible_inventory_are_validated(self):
        for mutate in (
            lambda p: p['materials']['material'].__setitem__('remaining_molecules', 1e30),
            lambda p: p['materials']['material']['lower_um'].__setitem__(0, 0.),
            lambda p: p['object_states']['material'].__setitem__('remaining_molecules', 123.),
            lambda p: p['material_ledger']['material'].__setitem__('after', [0.]),
        ):
            with self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_exhaustion_checkpoint_restores_dynamic_mask_and_next_step_exactly(self):
        project = deepcopy(self.project)
        for node in project['graph']['nodes']:
            if node['id'] == 'material':
                node['parameters']['initial_molecules']['value'] = 3.
            elif node['module_id'] == 'motion.unbiased_run_tumble':
                node['parameters']['speed_um_s']['value'] = 0.
                node['parameters']['tumble_rate_s']['value'] = 0.
        sim = simulation_from_project(project)
        old_mask = sim.fields.blocked
        sim.step(.1)
        self.assertEqual(sim.materials['material'].remaining_molecules, 0.)
        self.assertNotEqual(old_mask, sim.fields.blocked)
        restored = restore_checkpoint(project, json.loads(json.dumps(sim.checkpoint())))
        self.assertEqual(sim.fields.blocked, restored.fields.blocked)
        self.assertEqual(sim.obstacles, restored.obstacles)
        self.assertEqual(sim.current.object_states, restored.current.object_states)
        sim.step(.2); restored.step(.2)
        self.assertEqual(sim.checkpoint(), restored.checkpoint())

    def test_adapter_qualified_name_is_part_of_implementation_lock(self):
        bad = self.changed(lambda p: p['implementation_lock']['registered_adapter_sha256'][
            'reaction.contact_degradation@1.0.0'].__setitem__('qualname', 'other_function_in_same_file'))
        with self.assertRaisesRegex(SimulationError, 'implementation lock'):
            restore_checkpoint(self.project, bad)

    def test_original_v2_shape_remains_readable(self):
        legacy = deepcopy(self.payload)
        legacy.pop('frame_validator')
        legacy.pop('motion_contacts')
        restored = restore_checkpoint(self.project, reseal(legacy))
        self.assertEqual(restored.checkpoint(), self.payload)
        restored.step(.17)
        self.sim.step(.17)
        self.assertEqual(restored.checkpoint(), self.sim.checkpoint())

    def test_missing_unknown_and_coerced_fields_are_rejected(self):
        for mutate in (
            lambda p: p.__setitem__('unknown', 1),
            lambda p: p.pop('frame_validator'),
            lambda p: p.pop('motion_contacts'),
            lambda p: p.pop('ledger'),
            lambda p: p['local_fields'].__setitem__('unknown', 1),
            lambda p: p['walks'][next(iter(p['walks']))].__setitem__('unknown', 1),
            lambda p: p['walks'][next(iter(p['walks']))]['heading'].__setitem__(0, True),
            lambda p: p['random_streams'].__setitem__('unknown', 1),
            lambda p: p['random_streams']['streams'][0].__setitem__('unknown', 1),
            lambda p: p['random_streams']['streams'][0]['state'].__setitem__('unknown', 1),
            lambda p: p['random_streams']['streams'][0]['state']['state'].__setitem__('unknown', 1),
            lambda p: p['current_frame'].__setitem__('frame_index', True),
            lambda p: p['frame_validator'].__setitem__('previous_time', 0),
            lambda p: p['frame_validator'].__setitem__('next_index', 1),
            lambda p: p['frame_validator']['seen'].append('foreign'),
            lambda p: p['frame_validator'].__setitem__('alive', {}),
            lambda p: p['frame_validator'].__setitem__('unknown', 1),
        ):
            with self.subTest(mutate=mutate), self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_ledger_report_must_match_its_inventory_and_exact_balance(self):
        for mutate in (
            lambda p: p['material_ledger']['material'].__setitem__('total_before', 19999.),
            lambda p: p['material_ledger']['material'].__setitem__('accepted_by_reservoir', [5.]),
            lambda p: p['material_ledger']['material'].__setitem__('conservation_residual_exact', ['1/100']),
            lambda p: p['material_ledger']['material'].__setitem__('total_conservation_residual_exact', '1/100'),
            lambda p: p['material_ledger']['material'].__setitem__('conservation_bound', [1.]),
            lambda p: p['material_ledger']['material'].__setitem__('before', 'x'),
            lambda p: p['ledger']['nutrient'].__setitem__('field_after_molecules', 203.),
            lambda p: p['ledger']['nutrient'].__setitem__('source_after_molecules', 19799.),
            lambda p: p['ledger']['nutrient'].__setitem__('field_before_molecules', 1.),
        ):
            with self.subTest(mutate=mutate), self.assertRaises(SimulationError):
                restore_checkpoint(self.project, self.changed(mutate))

    def test_collision_diagnostics_roundtrip_and_reject_foreign_entities(self):
        project = deepcopy(self.project)
        for node in project['graph']['nodes']:
            if node['module_id'] == 'motion.unbiased_run_tumble':
                node['parameters']['speed_um_s']['value'] = 1000.
                node['parameters']['tumble_rate_s']['value'] = 0.
        sim = simulation_from_project(project)
        sim.step(.1)
        self.assertTrue(sim.motion_contacts)
        payload = json.loads(json.dumps(sim.checkpoint()))
        restored = restore_checkpoint(project, payload)
        self.assertEqual(restored.motion_contacts, sim.motion_contacts)
        self.assertEqual(restored.checkpoint(), payload)
        for name, value in (('cell_ids', ['foreign']), ('kind', 'unknown'),
                            ('reason', 'initial_overlap'), ('target_id', 'foreign')):
            broken = deepcopy(payload)
            broken['motion_contacts'][0][name] = value
            with self.subTest(name=name), self.assertRaises(SimulationError):
                restore_checkpoint(project, reseal(broken))


if __name__ == "__main__":
    unittest.main()
