"""Only released contracts execute; every official preset is a real graph."""
from copy import deepcopy
import json
import numpy as np
import pytest
from friskoli_cad.engine.presets import EXAMPLES, make_example, build_center_project, build_chip_project
from friskoli_cad.engine.profiles import registry_for_profile
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.protocol.task_validation import TaskValidationError, validate_submission, _validator
from friskoli_cad.tasks import TaskService, TaskError
from friskoli_cad.tasks.metadata import compiled_plan


@pytest.mark.parametrize('name', list(EXAMPLES))
def test_every_official_preset_validates_and_executes(name):
    sim = simulation_from_project(make_example(name))
    assert sim.current.metrics
    sim.step(.1)
    assert sim.frame_index == 1
    assert sim.current.metrics['by_group']['cells']['live_count'] > 0
    assert all(np.isfinite(a).all() and (a >= 0).all() for a in sim.current.concentration_fields.values())


@pytest.mark.parametrize('version,profile', [('0.1.0',None),('0.3.0','conservative-pts-bulk-v1'),
    ('0.4.0','spatial-unbiased-v1'),('0.5.0','chemotaxis-spatial-v1')])
def test_old_projects_and_registries_are_explicitly_rejected(version,profile):
    project = make_example(); project['project_version'] = version
    if profile is None: project.pop('execution_profile')
    else: project['execution_profile'] = profile
    with pytest.raises(ProtocolError,match='Only Project 0.6.0'): simulation_from_project(project)
    with pytest.raises(ProtocolError,match='Only modular-spatial-v1'): registry_for_profile(profile)


def test_single_capability_and_current_schema_validate_actual_plan(tmp_path):
    with TaskService(tmp_path/'service') as service:
        capability = service.capabilities()
        assert capability['task_contract_versions'] == ['0.6.0']
        assert capability['execution']['semantics'] == 'modular-spatial-v1'
        _validator('TaskCapabilities').validate(capability)
        p = make_example()
        plan = compiled_plan(p,registry_for_profile())
        _validator('CompiledPlan').validate(plan)
        with pytest.raises(TaskError): service.capabilities('chemotaxis-spatial-v1')
        with pytest.raises(TaskError): service.capabilities(contract_version='0.5.0')
        with pytest.raises(TaskValidationError,match='Unsupported task contract version'):
            validate_submission({'task_contract_version':'0.5.0'})


@pytest.mark.parametrize('mechanism',['a','b'])
@pytest.mark.parametrize('scale',['small','medium'])
def test_feedback_control_preserves_environment_chemistry_and_population(mechanism,scale):
    active = build_center_project(mechanism,scale,seed=17)
    control = build_center_project(mechanism,scale,feedback=False,seed=17)
    for key in ('groups','species','domain','observation'): assert active[key] == control[key]
    assert control['graph']['nodes'][:-1] == active['graph']['nodes']
    unchanged = lambda p: [e for e in p['graph']['edges'] if e['to'] != {'node':'motility','port':'motor_bias'}]
    assert unchanged(active) == unchanged(control)
    assert control['graph']['nodes'][-1]['module_id'] == 'signal.constant_bias'


def test_chip_geometry_parameter_contract_is_not_silently_adjusted():
    p = build_chip_project('gradient',1)
    assert p['domain'] == {'geometry':'volume','counts_xyz':[20,40,10],'spacing_um_xyz':[20.,20.,20.]}
    assert len(p['groups']['cells']['ids']) == 200
    with pytest.raises(ValueError,match='divide'): build_chip_project('gradient',1,{'grid_spacing_um':19.})
    with pytest.raises(ValueError,match='condition'): build_chip_project('other',1)


@pytest.mark.parametrize('condition', ['gradient', 'zero'])
@pytest.mark.parametrize('source', ['builder', 'packaged_example'])
def test_chip_field_and_observations_follow_horizontal_short_axis(condition, source):
    project = (build_chip_project(condition, 1) if source == 'builder'
               else make_example('chip-mcp-' + condition))
    sim = simulation_from_project(project)
    # The 400 um short horizontal side is X; voxel centers are 10, 30, ..., 390 um.
    initial = np.asarray(sim.current.concentration_fields['ligand']).reshape(10, 40, 20)
    profile = np.arange(5., 200., 10.) if condition == 'gradient' else np.full(20, 100.)
    np.testing.assert_allclose(initial, np.broadcast_to(profile, initial.shape), atol=1e-12)
    observation = project['observation']
    assert observation['axis'] == 0
    assert observation['region_lower_um'] == [200., 0., 0.]
    assert observation['region_upper_um'] == [400., 800., 200.]
    # Both finite exchange slabs cover Y/Z and are adjacent to the X walls.
    nodes = {node['id']: node for node in project['graph']['nodes']}
    assert nodes['low_side']['parameters']['upper_um']['value'] == [20., 800., 200.]
    assert nodes['high_side']['parameters']['lower_um']['value'] == [380., 0., 0.]
    sim.step(.1)
    field = np.asarray(sim.current.concentration_fields['ligand']).reshape(10, 40, 20)
    np.testing.assert_allclose(field, np.broadcast_to(field[0, 0], field.shape), atol=1e-12)
    positions = np.array([cell['position_um'] for cell in sim.current.cell_frame['cells']])
    metrics = sim.current.metrics['by_group']['cells']
    assert metrics['mean_position_um'] == pytest.approx(positions[:, 0].mean(), abs=1e-12)
    assert metrics['region_fraction'] == pytest.approx(np.mean(positions[:, 0] >= 200.))
