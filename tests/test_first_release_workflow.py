"""Observable history, real Task 0.6 delivery and service-independent reading."""
from copy import deepcopy
import json
import time

import numpy as np
import pytest

from friskoli_cad.engine.science_extensions import make_modular_example
from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.modular_checkpoint import export_checkpoint, restore_checkpoint
from friskoli_cad.engine.task_checkpoint import load_task_checkpoint
from friskoli_cad.engine.observations import initial_observation, advance_observation, observation_metrics
from friskoli_cad.tasks import TaskService
from friskoli_cad.run_delivery import export_task_package, read_task_package, collect_task_export
from friskoli_cad.wiki_export import build_wiki


def submission(service, project, every=3, steps=12):
    return {'task_contract_version': '0.6.0', 'request_id': f'workflow-{every}', 'edit_revision': '1',
            'project': project, 'version_lock': service.version_lock(project),
            'execution': {'semantics': 'modular-spatial-v1', 'backend': 'numpy-cpu', 'seed': project['random_seed'], 'dt_s': .01, 'steps': steps},
            'output_plan': {'frame_every_steps': every, 'checkpoint_every_steps': 5,
                            'include_fields': True, 'include_final_fields': True,
                            'observables': list(project['run']['channels'])}}


def wait_task(service, run_id):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        task = service.get(run_id)
        if task['status'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            assert task['status'] == 'completed', task['issues']
            return task
        time.sleep(.02)
    raise AssertionError('Task exceeded workflow test deadline')


def test_observation_history_restore_and_failure_are_atomic():
    p = make_modular_example(); p['random_seed'] = 17
    continuous = simulation_from_project(p)
    for _ in range(5): continuous.step(.01)
    resumed = restore_checkpoint(p, export_checkpoint(continuous))
    for _ in range(7):
        continuous.step(.01); resumed.step(.01)
    assert export_checkpoint(continuous) == export_checkpoint(resumed)
    before = export_checkpoint(resumed)
    with pytest.raises(ValueError): resumed.step(float('nan'))
    assert export_checkpoint(resumed) == before


def test_drift_is_independent_linear_fit_and_arrival_uses_closed_region():
    p = make_modular_example()
    p['groups'] = {'cells': {'ids': ['a'], 'positions_um': [[100., 1., 1.]]}}
    p['observation'] = {'id': 'test', 'label': 'Closed region', 'axis': 0,
                        'region_lower_um': [100., 0., 0.], 'region_upper_um': [200., 100., 2.]}
    def frame(t):
        return {'time_s': t, 'frame_index': int(t), 'cells': [{'id': 'a', 'group_id': 'cells', 'position_um': [100. + .3*t, 1., 1.]}]}
    state = initial_observation(p, frame(0))
    assert observation_metrics(state, frame(0))['by_group']['cells']['ever_arrived_fraction'] == 1.
    for t in range(1, 122): state = advance_observation(state, frame(t))
    result = observation_metrics(state, frame(121))['by_group']['cells']
    independently_fitted = np.polyfit(np.arange(10, 121), 100. + .3*np.arange(10, 121), 1)[0]
    assert result['drift_um_s'] == pytest.approx(independently_fitted, abs=2e-13)
    assert result['mean_residence_s'] == 121.


def test_task_sampling_script_equality_arrays_and_offline_wiki(tmp_path):
    p = make_modular_example(); p['random_seed'] = 17
    direct = simulation_from_project(p)
    for _ in range(12): direct.step(.01)
    with TaskService(tmp_path / 'service') as service:
        results = []
        for every in (1, 7):
            task, _ = service.submit(submission(service, p, every), f'workflow-{every}')
            wait_task(service, task['run_id'])
            checkpoint, _ = service.artifact(task['run_id'], 'checkpoint')
            loaded = load_task_checkpoint(checkpoint, maximum=service.limits.estimated_memory_bytes)
            assert export_checkpoint(loaded) == export_checkpoint(direct)
            record = collect_task_export(service, task['run_id'], inline_arrays=True)
            assert record['replay']['snapshots'][-1]['metrics'] == direct.current.metrics
            results.append(record)
        archive = export_task_package(service, task['run_id'])
        (tmp_path / 'research.friskoli').write_bytes(archive)
    # The originating TaskService is closed before reading arrays or Wiki.
    payload = read_task_package(archive)
    assert payload['runs'][0]['replay']['snapshots'][-1]['metrics'] == direct.current.metrics
    expected = direct.current.concentration_fields
    actual = payload['runs'][0]['replay']['snapshots'][-1]['concentrations']
    for species, values in expected.items(): np.testing.assert_array_equal(actual[species]['values_zyx'], values)
    build_wiki(archive, tmp_path / 'wiki')
    assert json.loads((tmp_path / 'wiki/examples.json').read_text())['solver_included'] is False
    assert (tmp_path / 'wiki/run.friskoli').read_bytes() == archive
