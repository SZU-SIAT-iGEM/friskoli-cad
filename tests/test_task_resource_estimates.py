"""Allocation-free budgets include declared module workspaces."""
from copy import deepcopy
from test_modular_science import project, add
from friskoli_cad.engine.science_extensions import modular_registry
from friskoli_cad.tasks.metadata import estimate


def test_mesh_workspace_increases_budget_before_any_mesh_voxel_allocation():
    p=project(); registry=modular_registry()
    request={'project':p,'task_contract_version':'0.6.0','execution':{'backend':'numpy-cpu','steps':1},
             'output_plan':{'frame_every_steps':1,'include_fields':False,'observables':[]}}
    baseline=estimate(request,registry)['memory_bytes']
    add(p,'geometry.triangle_mesh',{'vertices_xyz':[[1.,1.,.2],[2.,1.,.2],[1.,2.,.2],[1.,1.,1.]],
        'faces':[[0,2,1],[0,1,3],[0,3,2],[1,2,3]],'scale_um':1.})
    declaration=registry.get('geometry.triangle_mesh','1.0.0').execution_contract['workspace_bytes']
    assert declaration['fixed']>=128*1024*1024
    assert declaration['per_voxel']>=1024
    voxels=200
    with_mesh=estimate(request,registry)['memory_bytes']
    assert with_mesh-baseline >= declaration['fixed']+voxels*declaration['per_voxel']
    larger=deepcopy(request); larger['project']['domain']['counts_xyz']=[200,200,1]
    # This calculation never constructs the 40,000-voxel mesh mask or obstacles.
    large=estimate(larger,registry)['memory_bytes']
    assert large-with_mesh >= (40000-voxels)*declaration['per_voxel']
