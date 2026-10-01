"""Cross-layer scientific delivery: schemas, editable recipes and real workers."""
from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path
import time

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from friskoli_cad.engine.chemotaxis_modules import chemotaxis_registry
from friskoli_cad.engine.chemotaxis_templates import EXAMPLES, make_example
from friskoli_cad.engine.profiles import CHEMOTAXIS_PROFILE
from friskoli_cad.project import simulation_from_project, validate_project
from friskoli_cad.protocol.task_validation import _validator, canonical_bytes, canonical_loads
from friskoli_cad.tasks import TaskService
from friskoli_cad.tasks.metadata import compiled_plan


def terminal(service, run_id):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        task = service.get(run_id)
        if task['status'] in ('completed', 'failed', 'cancelled', 'interrupted'):
            return task
        time.sleep(.01)
    raise AssertionError('Scientific task did not finish')


def submission(service, project, *, every=1, steps=4, dt=.05):
    return {'task_contract_version': '0.4.0', 'request_id': 'n3-scientific', 'edit_revision': 'n3:1',
        'project': project, 'version_lock': service.version_lock(project),
        'execution': {'semantics': CHEMOTAXIS_PROFILE, 'backend': 'numpy-cpu', 'dt_s': dt, 'steps': steps, 'seed': 17},
        'output_plan': {'frame_every_steps': every, 'observables': list(project['run']['channels']), 'include_fields': True}}


@pytest.mark.parametrize('example', list(EXAMPLES))
def test_every_bundled_template_is_a_real_typed_graph_and_matches_factory(example):
    registry = chemotaxis_registry()
    doc = make_example(example, registry=registry)
    saved = json.loads(files('friskoli_cad').joinpath('examples', example.replace('-', '_') + '.project.json').read_text(encoding='utf-8'))
    assert doc == saved
    assert doc == json.loads((Path(__file__).parents[1] / 'examples/runtime' / (example.replace('-', '_') + '.project.json')).read_text(encoding='utf-8'))
    validate_project(doc, registry.manifests, registry=registry)
    frozen = compiled_plan(doc, registry)
    _validator('CompiledPlan', '0.4.0').validate(frozen)
    sim = simulation_from_project(doc, registry)
    assert sim.schedule == frozen['schedule']
    assert {f'{n["module_id"]}@{n["module_version"]}' for n in doc['graph']['nodes']} == set(
        next(t for t in registry.catalog['templates'] if t['id'] == example)['module_keys'])
    assert sim.current.metrics['by_group']['cells']['initial_count'] == 8


def test_workspace_version_explicitly_supports_scientific_and_prior_projects():
    schemas = [json.loads(p.read_text(encoding='utf-8')) for p in files('friskoli_cad.protocol').joinpath('schemas').iterdir() if p.name.endswith('.json')]
    resources = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    schema = next(s for s in schemas if s['$id'] == 'urn:friskoli:workspace:0.4.0')
    validator = Draft202012Validator(schema, registry=resources)
    for name in ('workspace_3d', 'pts_bulk', 'spatial_baseline', 'chemotaxis_mcp'):
        doc = json.loads(files('friskoli_cad').joinpath('examples', name + '.project.json').read_text(encoding='utf-8'))
        workspace = {'workspace_format_version': '0.4.0', 'project': doc, 'population_blocks': [],
            'graph_layout': {}, 'run_settings': {'dt_s': .05, 'steps': 200, 'seed': 9, 'frame_every_steps': 3}}
        validator.validate(workspace)


@pytest.mark.parametrize('example', ['chemotaxis-pts-a', 'chemotaxis-mcp', 'chemotaxis-materials', 'chemotaxis-lifecycle'])
def test_scientific_worker_emits_real_metrics_fields_and_frozen_plan(tmp_path, example):
    project = make_example(example)
    with TaskService(tmp_path / 'tasks') as service:
        _validator('TaskCapabilities', '0.4.0').validate(service.capabilities(CHEMOTAXIS_PROFILE))
        body = canonical_loads(canonical_bytes(submission(service, project, every=3)))
        if example == 'chemotaxis-materials':
            enzyme = next(n for n in body['project']['graph']['nodes'] if n['id'] == 'surface_enzyme')
            assert type(enzyme['parameters']['enzyme_copies']['value']) is int
        task, created = service.submit(body, 'scientific')
        assert created
        task = terminal(service, task['run_id'])
        assert task['status'] == 'completed', task['issues']
        manifest = service.manifest(task['run_id'])
        _validator('Manifest', '0.4.0').validate(manifest)
        simulation = simulation_from_project(project, seed=17)
        reference = [simulation.current] + [simulation.step(.05) for _ in range(4)]
        assert service.input(task['run_id']) == body
        for item, expected_index in zip(manifest['chunks'], (0, 3, 4), strict=True):
            chunk = canonical_loads(service.chunk(task['run_id'], item['chunk_id']))
            _validator('ChunkBody', '0.4.0').validate(chunk)
            envelope = chunk['frames'][0]
            assert envelope['step_index'] == expected_index
            assert envelope['metrics'] == reference[expected_index].metrics
            assert envelope['lifecycle_details'] == reference[expected_index].lifecycle_details
            assert envelope['object_states'] == dict(reference[expected_index].object_states)
            assert envelope['concentrations'] == {k: {'unit': 'uM', 'values_zyx': v.tolist()} for k, v in reference[expected_index].concentration_fields.items()}


def test_mcp_background_remains_uniform_and_supply_equals_accepted_uptake():
    sim = simulation_from_project(make_example('chemotaxis-mcp'))
    for _ in range(10): sim.step(.05)
    assert (sim.current.concentration_fields['nutrient'] == 1).all()
    assert sim.outputs['nutrient_field']['cumulative_supply'] > 0
    accepted = sum(sim.outputs['accepted_uptake']['cumulative_uptake'])
    assert sim.outputs['nutrient_field']['cumulative_supply'] == pytest.approx(accepted)


def test_material_builds_a_natural_gradient_in_the_same_field_used_for_uptake():
    peaks = []
    for diffusion in (1., 100.):
        project = make_example('chemotaxis-materials')
        nodes = {n['id']: n for n in project['graph']['nodes']}
        project['species']['nutrient']['initial_concentration']['value'] = 0.
        for axis in 'xyz':
            nodes['nutrient_field']['parameters'][f'gradient_{axis}_um_per_um']['value'] = 0.
        nodes['nutrient_field']['parameters']['diffusivity_um2_s']['value'] = diffusion
        nodes['attractant']['parameters']['release_rate']['value'] = 0.
        nodes['motility']['parameters']['speed_um_s']['value'] = 0.
        sim = simulation_from_project(project)
        for _ in range(20): sim.step(.1)
        field = sim.current.concentration_fields['nutrient']
        released = 10000. - sim.materials['substrate'].remaining_molecules
        accepted = sim.uptake_totals['accepted_uptake']
        retained = field.sum() * sim.world.grid.molecules_per_uM_voxel
        assert 0 < accepted < released
        assert released == pytest.approx(retained + accepted, rel=1e-11, abs=1e-8)
        assert field.max() > field.mean() > 0
        assert sim.outputs['nutrient_sample']['concentration'][3] > sim.outputs['nutrient_sample']['concentration'][0]
        peaks.append(field.max())
    # Same finite hydrolysis, geometry and uptake parameters. Faster diffusion
    # spreads the naturally accumulated field without a reserved gradient pool.
    assert peaks[0] > peaks[1]


def test_checkpoint_file_restores_new_scientific_state(tmp_path):
    from friskoli_cad.engine.checkpoint_io import save_checkpoint, load_checkpoint
    sim = simulation_from_project(make_example('chemotaxis-pts-b'))
    for _ in range(3): sim.step(.05)
    path = tmp_path / 'science.json'
    save_checkpoint(sim, path)
    assert json.loads(path.read_text(encoding='utf-8'))['checkpoint_file_version'] == '0.2.0'
    restored = load_checkpoint(path)
    for _ in range(3):
        assert sim.step(.05).cell_frame == restored.step(.05).cell_frame
        assert sim.current.metrics == restored.current.metrics


def test_preflight_supports_long_async_requests_without_allocating_output_history():
    from friskoli_cad.replay_service import prepare_project, ReplayRequestError
    doc = make_example('chemotaxis-pts-a')
    sim = prepare_project(doc, dt_s=.05, steps=200, validation_only=True)
    assert sim.time_s == 0
    with pytest.raises(ReplayRequestError): prepare_project(doc, dt_s=.05, steps=200)


@pytest.mark.parametrize('event', ['death', 'division'])
def test_sparse_worker_frames_preserve_real_lifecycle_events_and_death_rules(tmp_path, event):
    project = make_example('chemotaxis-lifecycle')
    nodes = {n['id']: n for n in project['graph']['nodes']}
    def setp(n, key, value): nodes[n]['parameters'][key]['value'] = value
    setp('motility', 'speed_um_s', 0.)
    if event == 'death':
        setp('health', 'initial_health', 0.)
        setp('health', 'repair_per_min', 0.)
        setp('health', 'death_max_per_min', 1e6)
    else:
        setp('health', 'death_max_per_min', 0.)
        setp('division', 'added_area_um2', 0.)
        setp('growth', 'max_growth_per_min', 0.)
    reference = simulation_from_project(project, seed=17)
    expected_events, expected_deaths = [], []
    for _ in range(3):
        frame = reference.step(.1)
        expected_events.extend(frame.cell_frame['events'])
        expected_deaths.extend(frame.lifecycle_details['deaths'])
    assert any(e['type'] == event for e in expected_events)
    with TaskService(tmp_path / 'tasks') as service:
        task, _ = service.submit(submission(service, project, every=3, steps=3, dt=.1), event)
        task = terminal(service, task['run_id'])
        assert task['status'] == 'completed', task['issues']
        manifest = service.manifest(task['run_id'])
        assert len(manifest['chunks']) == 2
        body = canonical_loads(service.chunk(task['run_id'], manifest['chunks'][-1]['chunk_id']))
        _validator('ChunkBody', '0.4.0').validate(body)
        frame = body['frames'][0]
        assert frame['frame']['events'] == expected_events
        assert frame['lifecycle_details']['deaths'] == expected_deaths
        assert frame['metrics'] == reference.current.metrics
        if event == 'death':
            assert len(expected_deaths) == 8
            assert all(d['node_id'] == 'health' and d['random_draw'] < d['probability'] for d in expected_deaths)
