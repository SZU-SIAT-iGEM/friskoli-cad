"""Register a custom implementation alongside built-ins and run an ordinary project."""
import json
from pathlib import Path
import argparse

from module import create
from friskoli_cad.engine.module_registry import ModuleRegistry
from friskoli_cad.engine.science_extensions import modular_registry
from friskoli_cad.engine.presets import make_example
from friskoli_cad.project import simulation_from_project


def example_project():
    project = make_example('modular-foundation')
    node = next(node for node in project['graph']['nodes'] if node['id'] == 'motor_signal')
    node['module_id'] = 'tutorial.constant_bias'
    node['parameters']['bias']['value'] = .75
    node['parameters']['bias']['provenance'] = {'kind': 'example', 'reference': 'Constant-bias tutorial'}
    return project


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('runs/tutorial/project.json'))
    args = parser.parse_args()
    builtins = modular_registry()
    registry = ModuleRegistry([builtins.get(m['id'], m['version']) for m in builtins.manifests] + [create()])
    project = example_project()
    simulation = simulation_from_project(project, registry)
    simulation.step(.01)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(project, indent=2) + '\n', encoding='utf-8')
    print(f'Computed step {simulation.frame_index}; motor biases: {simulation.outputs["motor_signal"]["motor_bias"].tolist()}')
    print(f'Project: {args.out}. Install and lock the tutorial package before opening it in the service.')
