"""The reusable study preserves matched inputs, measurements and numerical settings."""
import csv
from copy import deepcopy
from pathlib import Path
import runpy

import pytest

from friskoli_cad.design import DesignError, generate_design
from friskoli_cad.engine.presets import make_example

SCRIPT = runpy.run_path(str(Path(__file__).parents[1] / 'research/pts_assay.py'))


def test_short_study_writes_reproducible_pairs_and_sensitivity_inputs(tmp_path):
    records = SCRIPT['run_study'](tmp_path / 'assay', duration=.04, seeds=(1,), mechanisms=('a',), plot=False)
    assert len(records) == 6
    assert all(record['series'][-1]['time_s'] == pytest.approx(.04) for record in records)
    off, on = records[:2]
    assert on['project']['groups'] == off['project']['groups']
    assert on['project']['domain'] == off['project']['domain']
    assert on['configuration_sha256'] != off['configuration_sha256']
    assert all(record['metrics']['contact_cell_seconds'] >= 0 for record in records)
    assert (tmp_path / 'assay/manifest.json').is_file()
    with (tmp_path / 'assay/paired-effects.csv').open(encoding='utf-8', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert float(row['cumulative_degradation_molecules']) == pytest.approx(
        on['metrics']['cumulative_degradation_molecules'] - off['metrics']['cumulative_degradation_molecules'])
    with pytest.raises(ValueError, match='empty output'):
        SCRIPT['run_study'](tmp_path / 'assay', plot=False)


def test_parameter_import_preserves_source_and_checks_units():
    project = make_example('center-pts-a-assay-strong')
    row = {'node_id': 'motility', 'parameter': 'speed_um_s', 'value': 12., 'unit': 'um/s',
           'source': 'Provided measurement identifier', 'condition': 'Specified medium and temperature'}
    original = deepcopy(project)
    SCRIPT['apply_parameters'](project, [row])
    target = next(node for node in project['graph']['nodes'] if node['id'] == 'motility')['parameters']['speed_um_s']
    assert target['value'] == 12.
    assert row['source'] in target['provenance']['reference']
    assert row['condition'] in target['provenance']['reference']
    assert project['groups'] == original['groups']
    with pytest.raises(ValueError, match='Unit differs'):
        SCRIPT['apply_parameters'](project, [{**row, 'unit': 'mm/s'}])


def test_design_can_target_declared_material_contact():
    tutorial = runpy.run_path(str(Path(__file__).parents[1] / 'examples/design/compare_speeds.py'))
    source = tutorial['study_input']()
    source['brief']['goal']['metric'] = 'contact_cell_seconds'
    with pytest.raises(DesignError, match='contact_range_um'):
        generate_design(**source)
    source['project'] = make_example('center-pts-a-assay-strong')
    design = generate_design(**source)
    assert design['brief']['goal']['metric'] == 'contact_cell_seconds'
    assert design['budget']['candidate_count'] == 3


def test_contact_result_constraint_requires_declared_contact_range():
    tutorial = runpy.run_path(str(Path(__file__).parents[1] / 'examples/design/compare_speeds.py'))
    source = tutorial['study_input']()
    source['brief']['result_constraints'] = [{'id': 'touch', 'kind': 'hard', 'metric': 'contact_fraction',
                                               'group_id': 'cells', 'operator': '>=', 'value': 0.}]
    with pytest.raises(DesignError, match='contact_range_um'):
        generate_design(**source)
