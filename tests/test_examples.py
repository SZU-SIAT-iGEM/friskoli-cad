"""Executable tutorials exercise the public extension and Design workflows."""
from copy import deepcopy
from pathlib import Path
import runpy

import numpy as np

from friskoli_cad.design_delivery import import_design_package
from friskoli_cad.engine.modular_checkpoint import export_checkpoint, restore_checkpoint
from friskoli_cad.engine.presets import make_example
from friskoli_cad.engine.profiles import registry_for_project
from friskoli_cad.packages import PackageStore, digest, inspect_package
from friskoli_cad.project import simulation_from_project

ROOT = Path(__file__).parents[1]


def test_distributed_module_tutorial_runs_and_restores_without_core_edits(tmp_path, monkeypatch):
    builder = runpy.run_path(str(ROOT / 'examples/modules/constant_bias/build_package.py'))
    archive = builder['build_package'](tmp_path / 'tutorial.zip').read_bytes()
    assert inspect_package(archive)[0]['executes_on_install'] is False
    store = PackageStore(tmp_path / 'packages')
    monkeypatch.setenv('FRISKOLI_PACKAGE_DIR', str(store.root))
    store.install(archive, expected_sha256=digest(archive))
    project = make_example()
    project['dependency_lock'] = store.resolve({'tutorial': '1.0.0'})
    node = next(n for n in project['graph']['nodes'] if n['id'] == 'motor_signal')
    node['module_id'] = 'tutorial.constant_bias'
    node['parameters']['bias']['value'] = .75
    registry = registry_for_project(project)
    entry = next(e for e in registry.catalog['entries'] if e['key'] == 'tutorial.constant_bias@1.0.0')
    assert entry['mathematics']['kind'] == 'equations'
    original = deepcopy(project)
    simulation = simulation_from_project(project, registry)
    simulation.step(.01)
    np.testing.assert_array_equal(simulation.outputs['motor_signal']['motor_bias'], [.75] * 8)
    restored = restore_checkpoint(project, export_checkpoint(simulation), registry)
    restored.step(.01)
    simulation.step(.01)
    assert export_checkpoint(restored) == export_checkpoint(simulation)
    assert project == original


def test_design_tutorial_runs_every_seed_evaluates_and_reopens_native_package(tmp_path):
    tutorial = runpy.run_path(str(ROOT / 'examples/design/compare_speeds.py'))
    payload, evaluation = tutorial['run_study'](tmp_path / 'study')
    assert len(payload['runs']) == 6
    assert all(r['status'] == 'completed' and r['completeness'] == 'complete' for r in payload['runs'])
    assert evaluation['status'] == 'no_recommendation', evaluation
    assert evaluation['recommended_candidate_id'] is None
    assert evaluation['reasons'] == ['no_eligible_candidate']
    faster = next(c for c in evaluation['candidates'] if c['candidate_id'] == 'first-design:candidate-002')
    assert faster['objective']['n'] == 2
    assert faster['control_comparison']['minimum'] < 0 < faster['control_comparison']['maximum']
    assert import_design_package((tmp_path / 'study/study.friskoli').read_bytes()) == payload
    assert (tmp_path / 'study/report.csv').read_text(encoding='utf-8').count('summary') >= 3
