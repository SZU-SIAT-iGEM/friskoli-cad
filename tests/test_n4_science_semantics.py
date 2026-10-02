"""Causal controls for nutrient/sensory separation and stochastic ownership."""
from copy import deepcopy

import numpy as np
import pytest

from friskoli_cad.engine.chemotaxis_templates import make_example, migrate_mcp_parameters
from friskoli_cad.project import simulation_from_project


def test_mcp_nutrient_transport_does_not_become_a_pts_sensory_input():
    project = make_example('chemotaxis-mcp')
    no_transport = deepcopy(project)
    next(n for n in no_transport['graph']['nodes'] if n['id'] == 'uptake_request')['parameters']['turnover_s']['value'] = 0.
    fed, unfed = simulation_from_project(project), simulation_from_project(no_transport)
    for _ in range(20):
        fed.step(.1)
        unfed.step(.1)
        for key in ('activity', 'adaptation', 'chey_p', 'motor_bias'):
            np.testing.assert_array_equal(fed.outputs['motor_signal'][key], unfed.outputs['motor_signal'][key])
        np.testing.assert_array_equal(fed.world.groups['cells'].positions_um, unfed.world.groups['cells'].positions_um)
        np.testing.assert_array_equal(fed.current.concentration_fields['ligand'], unfed.current.concentration_fields['ligand'])
    assert fed.supply_totals['nutrient'] > 0
    assert unfed.supply_totals['nutrient'] == 0
    assert fed.supply_totals['nutrient'] == pytest.approx(fed.uptake_totals['accepted_uptake'])


def test_motion_is_stochastic_but_unrelated_draws_do_not_change_its_streams():
    project = make_example('chemotaxis-control')
    reference = simulation_from_project(project, seed=42)
    perturbed = simulation_from_project(project, seed=42)
    different_seed = simulation_from_project(project, seed=43)
    for _ in range(20):
        perturbed.streams.stream('unrelated', 'cells', 'cell-000', 'diagnostic').random_raw()
        for sim in (reference, perturbed, different_seed):
            sim.step(.1)
        np.testing.assert_array_equal(reference.world.groups['cells'].positions_um, perturbed.world.groups['cells'].positions_um)
        np.testing.assert_array_equal(reference.outputs['motor_signal']['motor_bias'], different_seed.outputs['motor_signal']['motor_bias'])
    assert not np.array_equal(reference.world.groups['cells'].positions_um, different_seed.world.groups['cells'].positions_um)
    assert {entry['key'][3] for entry in reference.streams.to_dict()['streams']} == {'run_hazard', 'tumble_direction'}


def test_exhausted_pts_field_has_no_uptake_or_implicit_starvation_death():
    project = make_example('chemotaxis-pts-a')
    project['species']['nutrient']['initial_concentration']['value'] = 0.
    field = next(n for n in project['graph']['nodes'] if n['id'] == 'nutrient_field')
    for axis in 'xyz':
        field['parameters'][f'gradient_{axis}_um_per_um']['value'] = 0.
    sim = simulation_from_project(project)
    initial_ei = sim.outputs['pts_signal']['ei_fraction'].copy()
    for _ in range(10):
        sim.step(.1)
        assert not np.any(sim.outputs['accepted_uptake']['accepted_flux'])
        assert not np.any(sim.current.concentration_fields['nutrient'])
    assert np.all(sim.outputs['pts_signal']['ei_fraction'] < initial_ei)
    assert len(sim.world.groups['cells'].ids) == 8
    assert sim.uptake_totals['accepted_uptake'] == 0


def test_mcp_v1_migration_is_explicit_loss_reported_and_trajectory_equivalent():
    from friskoli_cad.engine.chemotaxis_modules import chemotaxis_registry
    from friskoli_cad.engine.chemotaxis_checkpoint import restore_checkpoint

    registry = chemotaxis_registry()
    current = make_example('chemotaxis-mcp', registry=registry)
    legacy = deepcopy(current)
    node = next(n for n in legacy['graph']['nodes'] if n['id'] == 'motor_signal')
    assert node['module_version'] == '2.0.0'
    assert not any(key.startswith('ei_') for key in node['parameters'])
    node['module_version'] = '1.0.0'
    old_module = registry.get('signal.mcp_adaptation', '1.0.0')
    for key, schema in old_module.manifest['parameters'].items():
        if key.startswith('ei_'):
            node['parameters'][key] = {'value': old_module.default_parameters[key], 'unit': schema['unit'],
                'provenance': {'kind': 'example', 'reference': 'legacy MCP fixture'}}
    original = deepcopy(legacy)
    migrated, changes = migrate_mcp_parameters(legacy)
    assert legacy == original
    assert migrated == current
    assert set(changes[0]['removed_parameters']) == {'ei_total_um', 'ei_dephos_per_molecule', 'ei_rephos_s', 'ei_chea_inhibition_um'}
    assert migrate_mcp_parameters(migrated) == (migrated, [])
    a, b = simulation_from_project(legacy), simulation_from_project(migrated)
    for _ in range(6):
        a.step(.1)
        b.step(.1)
        assert a.current.cell_frame == b.current.cell_frame
        assert a.streams.to_dict() == b.streams.to_dict()
        for key in a.outputs['motor_signal']:
            np.testing.assert_array_equal(a.outputs['motor_signal'][key], b.outputs['motor_signal'][key])
    for sim in (a, b):
        restored = restore_checkpoint(sim.project, sim.checkpoint())
        sim.step(.1)
        restored.step(.1)
        assert sim.checkpoint() == restored.checkpoint()


def test_mcp_v2_rejects_unused_ei_parameter_instead_of_silently_ignoring_it():
    from friskoli_cad.protocol import ProtocolError

    project = make_example('chemotaxis-mcp')
    node = next(n for n in project['graph']['nodes'] if n['id'] == 'motor_signal')
    node['parameters']['ei_total_um'] = {'value': 1., 'unit': 'uM',
        'provenance': {'kind': 'example', 'reference': 'invalid MCP v2 EI input'}}
    with pytest.raises(ProtocolError):
        simulation_from_project(project)
