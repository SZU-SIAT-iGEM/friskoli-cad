from copy import deepcopy
import json

import pytest

from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.engine.chemotaxis_checkpoint import restore_checkpoint, _hash
from friskoli_cad.engine.runtime import SimulationError
from friskoli_cad.project import simulation_from_project


def seal(payload):
    payload['payload_sha256'] = _hash({k: v for k, v in payload.items() if k != 'payload_sha256'})
    return payload


def test_fifty_steps_equal_twenty_json_restore_thirty():
    project = make_example('chemotaxis-pts-b')
    whole = simulation_from_project(project)
    for _ in range(20):
        whole.step(.1)
    restored = restore_checkpoint(project, json.loads(json.dumps(whole.checkpoint())))
    for _ in range(30):
        whole.step(.1)
        restored.step(.1)
        assert restored.current.cell_frame == whole.current.cell_frame
    assert restored.checkpoint() == whole.checkpoint()


@pytest.mark.parametrize('corruption', ['clock', 'rng', 'supply', 'unknown', 'world', 'uptake', 'metric', 'observation'])
def test_coherent_checksum_cannot_hide_invalid_complete_state(corruption):
    project = make_example('chemotaxis-mcp')
    sim = simulation_from_project(project)
    sim.step(.2)
    original = sim.checkpoint()
    payload = deepcopy(original)
    if corruption == 'clock':
        payload['walks']['cell-000']['phase'] = 'tumble'
    elif corruption == 'rng':
        payload['random_streams']['streams'] = []
    elif corruption == 'supply':
        payload['supply_totals']['nutrient'] += 1
    elif corruption == 'unknown':
        payload['extra'] = 1
    elif corruption == 'world':
        payload['world']['cells']['geometry'][0]['length_um'] *= 2
    elif corruption == 'uptake':
        payload['uptake_totals']['accepted_uptake'] += 1e6
    elif corruption == 'metric':
        payload['metrics']['by_group']['cells']['live_count'] = 10
    else:
        payload['observation_state']['cohort']['cell-000']['residence_s'] = 1e6
    with pytest.raises(SimulationError):
        restore_checkpoint(project, seal(payload))
    assert sim.checkpoint() == original


def test_frame_zero_checkpoint_has_no_invented_clock_or_history():
    project = make_example('chemotaxis-lifecycle')
    sim = simulation_from_project(project)
    payload = json.loads(json.dumps(sim.checkpoint()))
    assert restore_checkpoint(project, payload).checkpoint() == payload


def death_checkpoint_with_draw(draw):
    project = make_example('chemotaxis-lifecycle')
    nodes = {node['id']: node for node in project['graph']['nodes']}
    for name, value in {'initial_health': 0., 'repair_per_min': 0., 'death_max_per_min': 600.}.items():
        nodes['health']['parameters'][name]['value'] = value
    original = simulation_from_project(project)
    original.step(.1)
    payload = original.checkpoint()
    assert payload['dead_material']
    cell_id, entry = next(iter(payload['dead_material'].items()))
    probability = entry['death_rule']['probability']
    assert 0 < probability < 1
    value = entry['death_rule']['random_draw'] if draw == 'original' else probability if draw == 'probability' else draw
    entry['death_rule']['random_draw'] = value
    detail = next(item for item in payload['lifecycle_details']['deaths'] if item['cell_id'] == cell_id)
    detail['random_draw'] = value
    return original, seal(payload)


def test_actual_death_draw_restores_and_survives_checkpoint_file(tmp_path):
    from friskoli_cad.engine.checkpoint_io import load_checkpoint, save_checkpoint

    original, payload = death_checkpoint_with_draw('original')
    restored = restore_checkpoint(original.project, payload)
    assert restored.checkpoint() == payload
    target = tmp_path / 'actual-death-draw.json'
    save_checkpoint(restored, target)
    from_file = load_checkpoint(target)
    assert from_file.checkpoint() == payload
    assert restored.step(.1).cell_frame == original.step(.1).cell_frame
    assert restored.current.metrics == original.current.metrics
    from_file.step(.1)
    assert from_file.checkpoint() == restored.checkpoint()


@pytest.mark.parametrize('draw', [0., -.01, 1., 'probability'])
def test_invalid_death_draw_is_rejected_even_with_consistent_details_and_checksum(draw):
    original, payload = death_checkpoint_with_draw(draw)
    with pytest.raises(SimulationError, match='death draw|biological death trigger'):
        restore_checkpoint(original.project, payload)
