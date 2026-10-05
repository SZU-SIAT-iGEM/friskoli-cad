"""Independent float64 transfer and analytical mechanism checks."""
from fractions import Fraction
from copy import deepcopy
import math

import numpy as np
import pytest

from friskoli_cad.engine.core import GridDomain
from friskoli_cad.engine.local_fields import FieldSpecies, make_local_field_state
from friskoli_cad.engine.module_api import Effect, thaw
from friskoli_cad.engine.presets import make_example
from friskoli_cad.engine.modular_checkpoint import export_checkpoint, restore_checkpoint
from friskoli_cad.project import simulation_from_project
from friskoli_cad.protocol import FrameSequenceValidator, ProtocolError
from friskoli_cad.science import chemotaxis


@pytest.mark.parametrize('before,requests', [
    (1., [1e-30, 3e-30]),
    (1.1748584437223487e-14, [8.710688988731363e-22]),
    (1.1748584437223487e-14, [2e-22, 6e-22]),
    (1e-15, [1., 2.]),
])
def test_modular_shared_acceptance_is_representable_field_debit(before, requests):
    sim = simulation_from_project(make_example())
    grid = GridDomain.thin_layer(1, 1, .5, .5, .5)
    fields = make_local_field_state(grid, [FieldSpecies('nutrient', before, 0.)])
    outputs, state, ledger = thaw(sim.outputs), thaw(sim.state), {}
    outputs['accepted_uptake']['cumulative_uptake'] = np.zeros(len(requests))
    effect = Effect('field.uptake', 'nutrient', {
        'positions_um': [[.25]*3 for _ in requests], 'requested_amount': requests})
    result = sim._field_effects(fields, [('accepted_uptake', effect)], outputs, state, 1., ledger, False)
    after = float(result.concentrations_uM['nutrient'][0])
    accepted = list(outputs['accepted_uptake']['accepted_amount'])
    debit = (Fraction(before) - Fraction(after)) * Fraction(grid.molecules_per_uM_voxel)
    total = sum(map(Fraction, accepted), Fraction())
    assert 0 <= after <= before
    assert 0 <= total <= sum(map(Fraction, requests), Fraction())
    assert abs(debit - total) <= Fraction(8*math.ulp(math.fsum(accepted)))
    if before == 1.: assert after == before and accepted == [0., 0.]
    if len(requests) == 2 and math.fsum(accepted):
        assert accepted[0]/accepted[1] == pytest.approx(requests[0]/requests[1], rel=3e-15)


def test_no_field_effect_preserves_every_float64_bit():
    sim = simulation_from_project(make_example())
    values = np.asarray(sim.fields.concentrations_uM['nutrient']).copy()
    result = sim._field_effects(sim.fields, [], thaw(sim.outputs), thaw(sim.state), .1, {}, False)
    np.testing.assert_array_equal(result.concentrations_uM['nutrient'], values)


def test_late_frame_failure_rolls_back_rng_ledger_and_observation(monkeypatch):
    p = make_example(); p['random_seed'] = 31
    sim = simulation_from_project(p); sim.step(.1)
    before = export_checkpoint(sim)
    original = FrameSequenceValidator.accept
    visited = []
    def reject(self, frame, **kwargs):
        visited.append(frame['frame_index'])
        assert frame['frame_index'] == 2
        raise ProtocolError('test.late_failure', '/', 'After physics, RNG and observations')
    monkeypatch.setattr(FrameSequenceValidator, 'accept', reject)
    with pytest.raises(ProtocolError, match='late_failure'): sim.step(.1)
    assert visited == [2]
    assert export_checkpoint(sim) == before
    monkeypatch.setattr(FrameSequenceValidator, 'accept', original)
    restored = restore_checkpoint(p, before)
    sim.step(.1); restored.step(.1)
    assert export_checkpoint(sim) == export_checkpoint(restored)


def test_mcp_backward_euler_satisfies_independently_written_equation():
    p = chemotaxis.MWCParameters(6.,18.,3000.,1.,0.,.3,1/3)
    ligand, old, dt = 100., 1.2, .1
    result = chemotaxis.advance_mcp_adaptation(ligand, old, dt, p)
    m = float(result.methylation)
    energy = 6*(-m + math.log((1+ligand/18)/(1+ligand/3000)))
    activity = 1/(1+math.exp(energy))
    assert abs(m-old-dt*.3*(1/3-activity)) < 3e-16
    assert float(result.activity) == pytest.approx(activity, rel=2e-14)
    initial = float(chemotaxis.mcp_adapted_methylation(ligand, p))
    assert float(chemotaxis.advance_mcp_adaptation(ligand, initial, dt, p).activity) == pytest.approx(1/3, abs=1e-15)


def test_finite_source_unrepresentable_stock_release_is_conserved():
    p = make_example()
    source = next(n for n in p['graph']['nodes'] if n['module_id']=='source.finite_local')
    source['parameters']['initial_molecules']['value'] = 1.
    source['parameters']['release_rate']['value'] = 1e-30
    sim = simulation_from_project(p); before = np.asarray(sim.fields.concentrations_uM['nutrient']).copy()
    sim.step(.1)
    assert sim.state[source['id']]['inventory'] == 1.
    assert sim.ledger['nutrient']['internal_net'] == 0.
    restore_checkpoint(p, export_checkpoint(sim))
