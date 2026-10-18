"""Run a small Design study with real tasks, matched seeds and portable results."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from friskoli_cad.design import generate_design
from friskoli_cad.design_delivery import export_design_package, design_report_csv, design_report_html
from friskoli_cad.design_evaluation import evaluate_design
from friskoli_cad.engine.presets import make_example
from friskoli_cad.run_delivery import collect_task_export
from friskoli_cad.tasks import TaskService


def study_input():
    return {
        'project': make_example('modular-foundation'),
        'settings': {'dt_s': .01, 'steps': 12, 'frame_every_steps': 3, 'include_fields': False},
        'brief': {
            'brief_version': '0.3.0', 'id': 'first-design', 'name': 'Compare swimming speeds',
            'goal': {'metric': 'mean_displacement_um', 'direction': 'maximize', 'group_id': 'cells'},
            'chassis': {'name': 'Modular foundation', 'provenance': 'Constructed teaching example'},
            'variables': [{'node_id': 'motility', 'parameter': 'speed_um_s', 'values': [5., 10.]}],
            'constraints': [], 'result_constraints': [],
            'selection_policy': {'min_repeats': 2, 'min_control_improvement': 0.},
            'seeds': [0, 1], 'max_runs': 6,
        },
    }


def run_study(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source = study_input()
    design = generate_design(source['project'], source['settings'], source['brief'])
    workspace = {
        'workspace_format_version': '0.7.0', 'project': source['project'],
        'population_blocks': [], 'graph_layout': {}, 'run_settings': design['settings'],
        'design': design, 'design_brief': design['brief'],
    }
    payload = {'package_version': '0.1.0', 'design': design, 'workspace': workspace, 'registry': None, 'runs': []}
    with TaskService(destination / 'tasks') as service:
        for candidate in design['candidates']:
            for seed in design['brief']['seeds']:
                project = deepcopy(candidate['project'])
                project['random_seed'] = seed
                execution = {'semantics': 'modular-spatial-v1', 'backend': 'numpy-cpu',
                             'seed': seed, 'dt_s': .01, 'steps': 12}
                output = {'frame_every_steps': 3, 'include_fields': False,
                          'observables': list(project['run']['channels'])}
                submission = {
                    'task_contract_version': '0.6.0', 'request_id': f'{candidate["id"]}-{seed}',
                    'edit_revision': '1', 'project': project, 'version_lock': service.version_lock(project),
                    'execution': execution, 'output_plan': output,
                }
                task, _ = service.submit(submission, submission['request_id'])
                deadline = time.monotonic() + service.limits.wall_time_s + 15
                while task['status'] not in ('completed', 'failed', 'cancelled', 'interrupted'):
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f'Task did not finish: {task["run_id"]}')
                    time.sleep(.02)
                    task = service.get(task['run_id'])
                if task['status'] != 'completed':
                    raise RuntimeError(f'Task {task["run_id"]}: {task["issues"]}')
                record = collect_task_export(service, task['run_id'], inline_arrays=True)
                record.update(id=task['run_id'], project=deepcopy(record['submission']['project']),
                              settings={**execution, **output},
                              design_ref={'design_id': design['id'], 'candidate_id': candidate['id'], 'candidate_name': candidate['name']})
                payload['runs'].append(record)
                print(f'{candidate["name"]}, seed {seed}: completed', flush=True)
    evaluation = evaluate_design(payload)
    (destination / 'study.friskoli').write_bytes(export_design_package(payload))
    (destination / 'report.html').write_text(design_report_html(payload), encoding='utf-8')
    (destination / 'report.csv').write_text(design_report_csv(payload), encoding='utf-8')
    (destination / 'evaluation.json').write_text(json.dumps(evaluation, indent=2) + '\n', encoding='utf-8')
    return payload, evaluation


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('runs/first-design'))
    args = parser.parse_args()
    _, result = run_study(args.out)
    print(f'Evaluation: {result["status"]}; candidate: {result["recommended_candidate_id"]}')
    print(f'Open {args.out / "study.friskoli"} in Friskoli-CAD to inspect the frozen design and runs.')
