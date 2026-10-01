"""Open-loop sign assays isolate sensory coupling from random population drift."""
import math

import numpy as np
import pytest

from friskoli_cad.engine.chemotaxis_templates import make_example
from friskoli_cad.engine.pts_runtime import parameters
from friskoli_cad.project import simulation_from_project
from friskoli_cad.science import pts


@pytest.mark.parametrize('model', ['pts-b', 'mcp'])
def test_full_graph_up_and_down_gradient_motor_sign(model):
    project = make_example('chemotaxis-' + model)
    nodes = {n['id']: n for n in project['graph']['nodes']}
    group = project['groups']['cells']
    group['ids'] = group['ids'][:2]
    group['positions_um'] = [[60., 40., 1.], [60., 60., 1.]]
    group['orientation_xyzw'] = [[0., 0., 0., 1.], [0., 0., 1., 0.]]
    group['initial_geometry'] = group['initial_geometry'][:2]
    # This is an open-loop signaling assay: opposite trajectories are imposed
    # by suppressing turns, while the complete sensory graph still runs.
    for key in ('minimum_tumble_rate_s', 'maximum_tumble_rate_s'):
        nodes['motility']['parameters'][key]['value'] = 0.
    if model == 'pts-b':
        initial = simulation_from_project(project)
        flux = initial.outputs['uptake_request']['requested_flux']
        steady = pts.steady_state_signal(flux, parameters(initial.plan.by_id['pts_signal'], pts.SignalParameters))
        nodes['pts_signal']['parameters']['initial_ei_fraction']['value'] = float(steady.ei_fraction[0])
        nodes['pts_signal']['parameters']['initial_chey_p_um']['value'] = float(steady.chey_p_uM[0])
        nodes['motor_signal']['parameters']['initial_memory_um']['value'] = float(steady.chey_p_uM[0])
    sim = simulation_from_project(project)
    initial_bias = sim.outputs['motor_signal']['motor_bias'].copy()
    ligand_before = math.fsum(sim.fields.concentrations_uM['ligand']) if model == 'mcp' else None
    for _ in range(60):
        sim.step(.05)
    bias = sim.outputs['motor_signal']['motor_bias']
    np.testing.assert_allclose(sim.world.groups['cells'].positions_um[:, 0], [75, 45], atol=1e-12)
    assert bias[0] < initial_bias[0] < bias[1]
    assert np.all(sim.outputs['accepted_uptake']['accepted_flux'] > 0)
    if model == 'mcp':
        assert math.fsum(sim.fields.concentrations_uM['ligand']) == pytest.approx(ligand_before, rel=2e-13)
        assert sim.supply_totals['nutrient'] > 0
