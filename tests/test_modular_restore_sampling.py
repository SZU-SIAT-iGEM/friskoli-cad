from copy import deepcopy

import numpy as np
import pytest

from friskoli_cad.engine.science_adapters import sample_local
from friskoli_cad.engine.module_api import StepContext
from friskoli_cad.engine.modular_checkpoint import export_checkpoint, restore_checkpoint
from friskoli_cad.engine.spatial_checkpoint import _hash
from friskoli_cad.project import simulation_from_project
from test_modular_science import project


def sample(field, blocked, positions, spacing):
    context = StepContext(0., 0., 0, 'cells', tuple(f'c{i}' for i in range(len(positions))),
        {'positions_um': np.asarray(positions), 'spacing_xyz': spacing, 'blocked': np.asarray(blocked)},
        {'field': np.asarray(field, float)})
    return sample_local(context, True).outputs


def test_uniform_fluid_next_to_solid_has_no_false_gradient():
    output = sample([[[0.,5.,5.,5.]]], [[[True,False,False,False]]], [[3.,.5,.5],[7.,.5,.5]], (2.,1.,1.))
    np.testing.assert_array_equal(output['concentration'], [5.,5.])
    np.testing.assert_array_equal(output['gradient'], np.zeros((2,3)))
    with pytest.raises(ValueError, match='solid obstacle'):
        sample([[[0.,5.]]], [[[True,False]]], [[1.,.5,.5]], (2.,1.,1.))


def test_sample_gradient_units_and_reflective_boundary_match_shared_sampler():
    from friskoli_cad.engine.local_fields import make_local_field_state, FieldSpecies, sample_local_fields
    from friskoli_cad.engine.runtime import GridDomain
    from dataclasses import replace
    grid = GridDomain(nx=4, ny=3, nz=1, dx_um=2., dy_um=3., dz_um=1., geometry='thin_layer')
    z,y,x = np.indices(grid.shape); field = 2.*x + 6.*y + 1.
    positions = [[1.,1.5,.5],[3.,4.5,.5],[7.,7.5,.5]]
    state = make_local_field_state(grid, [FieldSpecies('s',0.,0.)])
    state = replace(state, concentrations_uM={'s': tuple(field.flat)})
    actual = sample(field, np.zeros(grid.shape,bool), positions, (2.,3.,1.))
    expected = sample_local_fields(state, positions)
    np.testing.assert_array_equal(actual['gradient'], expected.gradient_uM_um['s'])
    np.testing.assert_array_equal(actual['gradient'], [[.5,1.,0.],[1.,2.,0.],[.5,1.,0.]])


def test_checkpoint_above_legacy_voxel_budget_restores_and_rejects_hidden_source():
    p = project(); p['domain']['counts_xyz'] = [501,501,1]; p['domain']['spacing_um_xyz'] = [.4,.2,2.]
    for node in p['graph']['nodes']:
        if node['module_id'] == 'field.diffusive_local': node['parameters']['diffusivity_um2_s']['value'] = 0.
    sim = simulation_from_project(p, seed=17)
    sim.step(.01)
    assert sim.world.grid.voxel_count == 251001
    saved = export_checkpoint(sim, binary=True)
    restored = restore_checkpoint(p, saved)
    np.testing.assert_array_equal(restored.fields.concentrations_uM['nutrient'], sim.fields.concentrations_uM['nutrient'])
    assert restored.current.cell_frame == sim.current.cell_frame
    sim.step(.01); restored.step(.01)
    np.testing.assert_array_equal(restored.fields.concentrations_uM['nutrient'], sim.fields.concentrations_uM['nutrient'])
    assert restored.current.cell_frame == sim.current.cell_frame
    bad = deepcopy(saved); bad['local_fields']['sources'] = [{'id': 'unregistered-source'}]
    bad.pop('payload_sha256'); bad['payload_sha256'] = _hash(bad)
    with pytest.raises(Exception, match='registered module-owned sources'):
        restore_checkpoint(p, bad)
