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
