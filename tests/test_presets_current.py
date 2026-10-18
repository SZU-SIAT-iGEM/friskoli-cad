"""Shipped PTS presets are reproducible from code, and the EI coupling is a fixed relay property."""
import importlib.util
from pathlib import Path

import pytest

from friskoli_cad.engine.presets import (EXAMPLES, REFERENCE_STRAIN, SIGNAL_CONSTANTS, ei_coupling_constant,
                                         make_example, pre_adapted_signal_state)

ROOT = Path(__file__).parents[1]


def load_builder():
    spec = importlib.util.spec_from_file_location('build_presets', ROOT / 'tools' / 'build_presets.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_generated_preset_matches_its_builder():
    builder = load_builder()
    assert builder.main(['--check']) == 0
    assert set(builder.builders()) <= set(EXAMPLES)


def test_ei_coupling_does_not_depend_on_expression_level():
    reference = ei_coupling_constant()
    for copies in (50., 100., 200., 1000.):
        derived = pre_adapted_signal_state(SIGNAL_CONSTANTS, .15, copies, 2., 1.)
        assert derived['ei_dephos_per_molecule'][1] == reference
    # Half dephosphorylation sits at the reference strain's half-saturated flux.
    half_flux = REFERENCE_STRAIN['pts_copies'] * REFERENCE_STRAIN['turnover_s'] / 2
    assert reference * half_flux == pytest.approx(SIGNAL_CONSTANTS['ei_rephos_s'])


@pytest.mark.parametrize('name', ['center-pts-a-encounter', 'center-pts-a-encounter-control'])
def test_encounter_presets_hold_a_maintained_surface_source(name):
    project = make_example(name)
    nodes = {n['id']: n for n in project['graph']['nodes']}
    source = nodes['surface_source']['parameters']
    assert source['target_um']['value'] == 1. and source['rate_s']['value'] > 0
    assert len(project['groups']['cells']['ids']) == 64
    assert ('constant_motor' in nodes) == name.endswith('-control')
