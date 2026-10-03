"""Preview a committed same-domain migration without changing the source state."""
from copy import deepcopy
from dataclasses import asdict
import math
import numpy as np
from friskoli_cad.project import simulation_from_project
from .task_checkpoint import adapters
from .spatial_checkpoint import _hash
from .regrid import remap_concentration


MAPPING = {'fields':'conservative-volume', 'cells':'identity', 'module_state':'identity'}


def map_field_value(value, spec, source_grid, grid, label):
    """Map only explicitly understood physical quantities, including voxel amounts."""
    unit, quantity = spec.get('unit'), spec.get('quantity')
    if spec.get('shape') != 'field.scalar':
        raise ValueError(f'No registered migration mapper for {label}: {spec.get("shape")}')
    amount = unit == 'molecule' and quantity in (None, 'amount')
    concentration = unit == 'uM' and quantity in (None, 'concentration')
    if not (amount or concentration):
        raise ValueError(f'No conservative quantity/unit mapper for {label}: {quantity}/{unit}')
    values = np.asarray(value, dtype=float)
    result = remap_concentration(values, source_grid, grid)
    if amount:
        result *= grid.molecules_per_uM_voxel / source_grid.molecules_per_uM_voxel
    before = math.fsum(values.ravel()) * (1 if amount else source_grid.molecules_per_uM_voxel)
    after = math.fsum(result.ravel()) * (1 if amount else grid.molecules_per_uM_voxel)
    if not math.isclose(before, after, rel_tol=1e-11, abs_tol=1e-9):
        raise ValueError(f'Migration inventory conservation check failed: {label}')
    return result


def migrate_simulation(sim, target_project, mapping):
    """Return an independently validated candidate and explicit stock audit.

    Identity module-state mapping only permits unchanged module identity,
    parameters, graph and initial scientific state. Diffusivity can change as
    an environment property; fields are transferred as cell-volume averages.
    """
    if mapping != MAPPING:
        raise ValueError('A complete supported fields/cells/module_state mapping is required')
    modular = sim.project.get('execution_profile') == 'modular-spatial-v1'
    target_project = deepcopy(target_project)
    before, after = deepcopy(sim.project), deepcopy(target_project)
    before.pop('domain'); after.pop('domain')
    for document in (before, after):
        for node in document['graph']['nodes']:
            if node['module_id'] in ('field.diffusive_local', 'field.ideal_local_reservoir'):
                node['parameters'].pop('diffusivity_um2_s', None)
                node['parameters'].pop('diffusion_um2_s', None)
    if before != after:
        raise ValueError('Identity module-state mapping requires unchanged graph, stocks, IDs and schedules')
    target = simulation_from_project(target_project, seed=sim.seed, field_backend=sim.field_backend)
    source_grid, grid = sim.world.grid, target.world.grid
    if source_grid.geometry != grid.geometry or not np.allclose(source_grid.extent_um,grid.extent_um,rtol=1e-12,atol=1e-12):
        raise ValueError('Changing the physical domain needs an explicit domain/cell/source mapping; same-domain mapping cannot be used')
    export, restore = adapters(sim.project)
    payload = export(sim,binary=True)
    payload['project_sha256'] = _hash(target_project)
    payload['local_fields']['grid'] = asdict(grid)
    payload['local_fields']['diffusivities_um2_s'] = dict(target.fields.diffusivities_um2_s)
    if modular:
        derived_fields, obstacles, _ = target._geometry_effects(target.fields, [], sim.geometry_state)
        blocked = derived_fields.blocked
    else:
        obstacles, blocked = target._geometry_for(sim.materials)
    payload['local_fields']['blocked'] = np.asarray(blocked).reshape(-1)
    audits = {}
    mapped = {}
    for species, values in sim.fields.concentrations_uM.items():
        result = remap_concentration(np.asarray(values).reshape(source_grid.shape),source_grid,grid)
        if np.any(result.ravel()[np.asarray(blocked).ravel()] != 0):
            raise ValueError('Conservative mapping would put inventory inside an obstacle; a different explicit mapping is required')
        initial = math.fsum(np.asarray(values).ravel()) * source_grid.molecules_per_uM_voxel
        final = math.fsum(result.ravel()) * grid.molecules_per_uM_voxel
        if not math.isclose(initial,final,rel_tol=1e-11,abs_tol=1e-9):
            raise ValueError('Migration inventory conservation check failed')
        audits[species] = {'before_molecules':initial,'after_molecules':final,'difference_molecules':final-initial}
        mapped[species] = result
    payload['local_fields']['concentrations_uM'] = {key:value.ravel() for key,value in mapped.items()}
    for node in sim.plan.nodes:
        manifest = sim.registry.get(node.module_id,node.module_version).manifest
        for attribute, ports in (('outputs',manifest['outputs']),('state',manifest.get('state',{}))):
            for port, value in list(payload[attribute][node.id].items()):
                spec = ports.get(port,{}) if isinstance(ports,dict) else {}
                shape = spec.get('shape', '')
                if modular and attribute == 'state':
                    policy = spec.get('on_migration')
                    expected = 'conservative_regrid' if shape.startswith('field.') else 'copy'
                    if policy != expected:
                        raise ValueError(f'No registered state migration mapper: {node.id}.{port} ({policy}); expected {expected}')
                if shape.startswith('field.'):
                    # Historical profiles explicitly support their known concentration adapter.
                    if not modular and (node.module_id not in ('field.diffusive_local', 'field.ideal_local_reservoir') or port != 'concentration'):
                        raise ValueError(f'No historical field migration adapter: {node.id}.{port}')
                    payload[attribute][node.id][port] = map_field_value(value, spec, source_grid, grid, f'{node.id}.{port}')
                elif np.asarray(value).shape == source_grid.shape and source_grid.shape != grid.shape:
                    raise ValueError(f'Undeclared field state mapping: {node.id}.{port}')
    payload['payload_sha256'] = _hash({k:v for k,v in payload.items() if k!='payload_sha256'})
    try:
        candidate = restore(target_project,payload)
    except ValueError as error:
        if sim.frame_index == 0:
            raise ValueError('Migration at initialization must match target initialization; execute at least one step and pause before conservative regrid') from error
        raise
    if candidate.streams.to_dict() != sim.streams.to_dict():
        raise ValueError('Migration changed RNG state')
    return candidate, {'kind':'same-domain-conservative', 'mapping':mapping, 'step_index':sim.frame_index,
        'source_grid':asdict(source_grid),'target_grid':asdict(grid),'inventories':audits,
        'preserved':['time','cell IDs','RNG','module state','source stocks','future schedules']}
