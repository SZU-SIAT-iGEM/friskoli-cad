from copy import deepcopy
import math

import numpy as np
import pytest

from friskoli_cad.science.survival import advance_reserve, advance_starvation
from friskoli_cad.engine.chemotaxis_templates import make_example, FOUNDATIONS
from friskoli_cad.engine.chemotaxis_checkpoint import restore_checkpoint
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.protocol.task_validation import _validator


def setp(project, nid, key, value):
    next(n for n in project['graph']['nodes'] if n['id'] == nid)['parameters'][key]['value'] = value


def test_reserve_uses_only_accepted_amount_and_has_exact_exhaustion_time():
    result = advance_reserve([4., 0., 9.], [2., 2., 0.], 2., 2.)
    np.testing.assert_array_equal(result.reserve_molecules, [2., 0., 5.])
    np.testing.assert_array_equal(result.used_molecules, [4., 2., 4.])
    np.testing.assert_array_equal(result.unmet_duration_s, [0., 1., 0.])
    assert advance_reserve([0.], [0.], 10., 0.).unmet_duration_s[0] == 0


def test_tiny_accepted_flux_is_retained_in_compensated_reserve():
    from fractions import Fraction
    result = advance_reserve([100.], [1e-16], .1, 0.)
    assert result.reserve_molecules[0] == 100.
    assert result.correction_molecules[0] == 1e-16
    total = Fraction(float(result.reserve_molecules[0])) + Fraction(float(result.correction_molecules[0]))
    assert total == Fraction(100.) + Fraction(1e-16)
    consumed = advance_reserve(result.reserve_molecules, [0.], 1., 100., result.correction_molecules)
    assert consumed.reserve_molecules[0] == 1e-16
    assert consumed.used_molecules[0] == 100.


def test_starvation_grace_and_recovery_integrate_hazard_across_boundaries():
    args = dict(grace_s=2., recovery_rate=1., death_rate_per_min=60.)
    start = advance_starvation([0.], [2.], 2., **args)
    assert start.integrated_hazard[0] == 0
    long = advance_starvation(start.starvation_time_s, [3.], 3., **args)
    assert long.integrated_hazard[0] == 3
    # On refeeding, exposure 5 -> 1. It spends three seconds above grace.
    recovered = advance_starvation(long.starvation_time_s, [0.], 4., **args)
    assert recovered.starvation_time_s[0] == 1
    assert recovered.integrated_hazard[0] == 3
    divided = advance_starvation([2.], [1.], 1., **args)
    final = advance_starvation(divided.starvation_time_s, [2.], 2., **args)
    assert divided.integrated_hazard[0] + final.integrated_hazard[0] == long.integrated_hazard[0]
    with pytest.raises(ValueError):
        advance_starvation([0.], [0.], 1., **{**args, 'grace_s': 0.})


@pytest.mark.parametrize('name', FOUNDATIONS)
def test_complete_foundations_run_conserve_and_restore(name):
    project = make_example(name)
    sim = simulation_from_project(project)
    for _ in range(5):
        sim.step(.1)
    record = sim.physiology_ledger['nutrient']
    available = sum(sim.state['nutrient_reserve']['intracellular_molecules'])
    assert available + record['maintenance_consumed_molecules'] == pytest.approx(800 + sim.uptake_totals['accepted_uptake'])
    restored = restore_checkpoint(project, sim.checkpoint())
    for _ in range(3):
        sim.step(.1)
        restored.step(.1)
        assert sim.checkpoint() == restored.checkpoint()


def starvation_case():
    project = make_example('foundation-control')
    setp(project, 'nutrient_source', 'release_rate', 0.)
    setp(project, 'motility', 'speed_um_s', 0.)
    setp(project, 'nutrient_reserve', 'initial_molecules', 2.)
    setp(project, 'nutrient_reserve', 'maintenance_molecules_s', 2.)
    setp(project, 'survival', 'grace_s', 1.)
    setp(project, 'survival', 'death_rate_per_min', 1e6)
    return project


def test_zero_nutrient_has_reserve_and_grace_before_death_and_resumes_exactly():
    project = starvation_case()
    sim = simulation_from_project(project, seed=123)
    for _ in range(2):
        sim.step(1.)
        assert len(sim.world.groups['cells'].ids) == 8
        assert not sim.current.lifecycle_details['deaths']
        assert all(e['key'][3] != 'death' for e in sim.streams.to_dict()['streams'])
    restored = restore_checkpoint(project, sim.checkpoint())
    sim.step(1.)
    restored.step(1.)
    assert sim.checkpoint() == restored.checkpoint()
    assert not sim.world.groups['cells'].ids
    assert len(sim.current.lifecycle_details['deaths']) == 8
    for detail in sim.current.lifecycle_details['deaths']:
        assert detail['module_id'] == 'life.starvation_hazard'
        assert detail['policy'] == 'reserve_starvation'
        assert detail['random_draw'] < detail['probability']
    _validator('LifecycleDetails', '0.4.0').validate(sim.current.lifecycle_details)
    restored = restore_checkpoint(project, sim.checkpoint())
    sim.step(.1)
    restored.step(.1)
    assert sim.checkpoint() == restored.checkpoint()
    assert sim.physiology_ledger['nutrient']['maintenance_consumed_molecules'] == 16


def test_reservoir_sustains_maintenance_without_using_starvation_rng():
    project = make_example('foundation-mcp')
    setp(project, 'nutrient_reserve', 'initial_molecules', 0.)
    setp(project, 'survival', 'grace_s', .01)
    setp(project, 'survival', 'death_rate_per_min', 1e6)
    sim = simulation_from_project(project)
    for _ in range(10):
        sim.step(.1)
        assert len(sim.world.groups['cells'].ids) == 8
        assert not np.any(sim.state['survival']['starvation_time_s'])
        assert np.all(sim.current.concentration_fields['nutrient'] == 1)
    assert sim.supply_totals['nutrient'] == pytest.approx(sim.uptake_totals['accepted_uptake'])
    assert all(e['key'][3] != 'death' for e in sim.streams.to_dict()['streams'])


def test_growth_and_reserve_cannot_double_consume_the_same_uptake():
    project = make_example('foundation-control')
    growth = deepcopy(next(n for n in make_example('chemotaxis-lifecycle')['graph']['nodes'] if n['id'] == 'growth'))
    project['graph']['nodes'].append(growth)
    project['graph']['edges'].append({'id': 'double_stock', 'from': {'node': 'accepted_uptake', 'port': 'accepted_amount'},
                                    'to': {'node': 'growth', 'port': 'accepted_amount'}, 'timing': 'same_step'})
    with pytest.raises(ProtocolError, match='Duplicate physiological'):
        simulation_from_project(project)


def test_starvation_death_uses_reproducible_independent_random_draws():
    project = starvation_case()
    setp(project, 'nutrient_reserve', 'initial_molecules', 0.)
    setp(project, 'survival', 'grace_s', .1)
    setp(project, 'survival', 'death_rate_per_min', 60.)
    sims = [simulation_from_project(project, seed=seed) for seed in (37, 37, 38)]
    for sim in sims:
        sim.step(1.)
    assert sims[0].checkpoint() == sims[1].checkpoint()
    assert sims[0].world.groups['cells'].ids != sims[2].world.groups['cells'].ids
    assert 0 < len(sims[0].world.groups['cells'].ids) < 8
    for sim in sims:
        death_streams = [s for s in sim.streams.to_dict()['streams'] if s['key'][3] == 'death']
        assert len(death_streams) == 8
        assert all(s['key'][0] == 'survival' for s in death_streams)


def test_foundation_worker_preserves_sparse_starvation_death_details(tmp_path):
    import time
    from friskoli_cad.tasks import TaskService
    from friskoli_cad.protocol.task_validation import canonical_loads
    project = starvation_case()
    with TaskService(tmp_path / 'tasks') as service:
        body = {'task_contract_version': '0.4.0', 'request_id': 'foundation-death', 'edit_revision': 'foundation:1',
            'project': project, 'version_lock': service.version_lock(project),
            'execution': {'semantics': 'chemotaxis-spatial-v1', 'backend': 'numpy-cpu', 'dt_s': 1., 'steps': 3, 'seed': 123},
            'output_plan': {'frame_every_steps': 3, 'observables': list(project['run']['channels']), 'include_fields': True}}
        task, _ = service.submit(body, 'foundation-death')
        deadline = time.monotonic() + 40
        while task['status'] not in ('completed', 'failed') and time.monotonic() < deadline:
            time.sleep(.02)
            task = service.get(task['run_id'])
        assert task['status'] == 'completed', task['issues']
        manifest = service.manifest(task['run_id'])
        chunk = canonical_loads(service.chunk(task['run_id'], manifest['chunks'][-1]['chunk_id']))
        _validator('ChunkBody', '0.4.0').validate(chunk)
        deaths = chunk['frames'][-1]['lifecycle_details']['deaths']
        assert len(deaths) == 8
        assert {d['policy'] for d in deaths} == {'reserve_starvation'}


def test_late_rejection_rolls_back_reserve_death_and_rng(monkeypatch):
    from friskoli_cad.engine.runtime import SimulationError
    project = starvation_case()
    setp(project, 'nutrient_reserve', 'initial_molecules', 0.)
    sim = simulation_from_project(project)
    sim.step(1.)
    before = sim.checkpoint()
    def reject(*args):
        raise SimulationError('test.late', 'Reject after physiology')
    with monkeypatch.context() as patch:
        patch.setattr(sim, '_validate_arrays', reject)
        with pytest.raises(SimulationError, match='test.late'):
            sim.step(1.)
    assert sim.checkpoint() == before
    sim.step(1.)
    assert len(sim.dead_material) == 8


def test_checkpoint_rejects_fake_compensated_reserve_even_with_valid_checksum():
    from friskoli_cad.engine.spatial_checkpoint import _hash
    from friskoli_cad.engine.runtime import SimulationError
    project = make_example('foundation-control')
    sim = simulation_from_project(project)
    sim.step(.1)
    payload = sim.checkpoint()
    payload['state']['nutrient_reserve']['reserve_correction_molecules'][0] = 1.
    payload['payload_sha256'] = _hash({k: v for k, v in payload.items() if k != 'payload_sha256'})
    with pytest.raises(SimulationError, match='compensated reserve'):
        restore_checkpoint(project, payload)
