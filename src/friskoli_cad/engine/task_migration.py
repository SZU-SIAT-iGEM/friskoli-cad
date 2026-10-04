"""Preview explicit conservative grid/domain migration without changing source state."""
from copy import deepcopy
from dataclasses import asdict
import math
import numpy as np
from friskoli_cad.project import simulation_from_project
from .task_checkpoint import adapters
from .checkpoint_tools import _hash


MAPPING = {'fields':'conservative-volume', 'cells':'identity', 'module_state':'identity'}
DOMAIN_MAPPING = {**MAPPING, 'domain':'physical-coordinates-zero-fill', 'sources':'identity'}


def scientific_identity(project):
    document = deepcopy(project)
    document.pop('domain'); document.pop('system_limits',None)
    return document


def remap_physical(values, source, target):
    """Integrate voxel averages in fixed physical coordinates, with zero outside."""
    values = np.asarray(values,dtype=float)
    if values.shape[:3] != source.shape or not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError('Conservative fields need finite nonnegative values on the source grid')
    for axis,(length,new_length,count) in enumerate(zip(source.extent_um[::-1],target.extent_um[::-1],source.shape)):
        if new_length < length:
            cropped=(np.arange(1,count+1)*(length/count)) > new_length
            if np.any(np.moveaxis(values,axis,0)[cropped] != 0):
                raise ValueError('Domain crop would discard nonzero inventory; an explicit outflow ledger mapper is required')
    if np.allclose([source.dx_um,source.dy_um,source.dz_um],[target.dx_um,target.dy_um,target.dz_um],rtol=0,atol=0):
        result=np.zeros(target.shape+values.shape[3:],dtype=float)
        overlap=tuple(slice(0,min(a,b)) for a,b in zip(source.shape,target.shape))
        result[overlap]=values[overlap]
        return result
    result = values
    for axis,(length,new_length,count) in enumerate(zip(source.extent_um[::-1],target.extent_um[::-1],target.shape)):
        moved=np.moveaxis(result,axis,0); old_count=moved.shape[0]
        if length == new_length and old_count == count:
            continue
        width=length/old_count; new_width=new_length/count
        remapped=np.zeros((count,)+moved.shape[1:],dtype=float)
        # Accumulate only local overlaps. Subtracting global prefix integrals
        # would erase a small voxel following a much larger unrelated voxel.
        for target_index in range(count):
            left=target_index*new_width; right=min((target_index+1)*new_width,length)
            if left >= right:
                continue
            first=max(0,min(old_count-1,math.floor(left/width)))
            stop=min(old_count,math.ceil(right/width))
            for source_index in range(first,stop):
                overlap=max(0.,min(right,(source_index+1)*width)-max(left,source_index*width))
                if overlap:
                    remapped[target_index] += moved[source_index]*(overlap/new_width)
        result=np.moveaxis(remapped,0,axis)
    return result


def validate_domain_objects(sim,target_grid):
    """Built-in physical supports keep their coordinates and cannot be cropped."""
    from .science_extensions import modular_registry
    builtins={(m['id'],m['version']) for m in modular_registry().manifests}
    extent=np.asarray(target_grid.extent_um)
    shrink=extent < np.asarray(sim.world.grid.extent_um)
    def bounds(lower,upper,label):
        lower,upper=np.asarray(lower,float),np.asarray(upper,float)
        if lower.shape != (3,) or upper.shape != (3,) or np.any(lower[shrink]<0) or np.any(upper[shrink]>extent[shrink]):
            raise ValueError(f'Domain crop would truncate physical support {label}; source/object identity mapping cannot relocate it')
    for node in sim.plan.nodes:
        if (node.module_id,node.module_version) not in builtins:
            raise ValueError(f'No physical-domain mapper contract is registered for custom module {node.id}')
        p={k:v.value for k,v in node.parameters.items()}
        if 'lower_um' in p: bounds(p['lower_um'],p['upper_um'],node.id)
        if 'lower_x_um' in p: bounds([p['lower_'+a+'_um'] for a in 'xyz'],[p['upper_'+a+'_um'] for a in 'xyz'],node.id)
        if 'center_x_um' in p:
            center=np.asarray([p['center_'+a+'_um'] for a in 'xyz']); radius=p.get('radius_um',0.)
            bounds(center-radius,center+radius,node.id)
        if 'release_position_um' in p: bounds(p['release_position_um'],p['release_position_um'],node.id)
        if 'vertices_xyz' in p:
            vertices=np.asarray(p['vertices_xyz'])*p.get('scale_um',1.)
            bounds(vertices.min(axis=0),vertices.max(axis=0),node.id)
    def record_positions(value):
        if isinstance(value,dict):
            if 'position_um' in value: bounds(value['position_um'],value['position_um'],'saved material/death record')
            for child in value.values(): record_positions(child)
        elif isinstance(value,(list,tuple)):
            for child in value: record_positions(child)
    record_positions(sim.dead_material)


def map_field_value(value, spec, source_grid, grid, label):
    """Map only explicitly understood physical quantities, including voxel amounts."""
    unit, quantity = spec.get('unit'), spec.get('quantity')
    if spec.get('dtype','float64') not in ('float32','float64'):
        raise ValueError(f'Conservative regrid needs a floating field dtype: {label}')
    if spec.get('shape') not in ('field.scalar','field.vector','field.tensor'):
        raise ValueError(f'No registered migration mapper for {label}: {spec.get("shape")}')
    amount = unit == 'molecule' and quantity in (None, 'amount')
    concentration = unit == 'uM' and quantity in (None, 'concentration')
    if not (amount or concentration):
        raise ValueError(f'No conservative quantity/unit mapper for {label}: {quantity}/{unit}')
    values = np.asarray(value, dtype=float)
    expected_tail = () if spec['shape']=='field.scalar' else tuple(spec.get('tensor_shape',[3])) if spec['shape']=='field.vector' else tuple(spec.get('tensor_shape',()))
    if (not expected_tail and spec['shape']=='field.tensor') or values.shape != source_grid.shape + expected_tail:
        raise ValueError(f'Unresolved or incompatible tensor dimensions for {label}')
    result = remap_physical(values, source_grid, grid)
    if amount:
        result *= grid.molecules_per_uM_voxel / source_grid.molecules_per_uM_voxel
    with np.errstate(over='ignore',under='ignore'):
        typed_result = result.astype(spec.get('dtype','float64'))
    if not np.isfinite(typed_result).all() or np.any((result > 0) & (typed_result == 0)):
        raise ValueError(f'Declared field dtype cannot preserve mapped inventory: {label}')
    result = typed_result
    before = math.fsum(values.ravel()) * (1 if amount else source_grid.molecules_per_uM_voxel)
    after = math.fsum(result.ravel()) * (1 if amount else grid.molecules_per_uM_voxel)
    if not math.isclose(before, after, rel_tol=1e-11, abs_tol=1e-9):
        raise ValueError(f'Migration would discard inventory for {label}; nonzero domain outflow needs an explicit ledger mapper')
    return result


def migrate_simulation(sim, target_project, mapping):
    """Return an independently validated candidate and explicit stock audit.

    Identity module-state mapping only permits unchanged module identity,
    parameters, graph and initial scientific state. Diffusivity can change as
    an environment property; fields are transferred as cell-volume averages.
    """
    if mapping not in (MAPPING,DOMAIN_MAPPING):
        raise ValueError('Use the complete same-domain mapping or explicit physical-coordinates-zero-fill with identity sources')
    modular = sim.project.get('execution_profile') == 'modular-spatial-v1'
    target_project = deepcopy(target_project)
    if scientific_identity(sim.project) != scientific_identity(target_project):
        raise ValueError('Identity module-state mapping requires unchanged graph, stocks, IDs and schedules')
    target = simulation_from_project(target_project, seed=sim.seed, field_backend=sim.field_backend)
    source_grid, grid = sim.world.grid, target.world.grid
    changed_domain = not np.allclose(source_grid.extent_um,grid.extent_um,rtol=1e-12,atol=1e-12)
    if source_grid.geometry != grid.geometry:
        raise ValueError('Geometry-mode changes require an unsupported coordinate mapper')
    if changed_domain and (mapping != DOMAIN_MAPPING or not modular):
        raise ValueError('Physical-domain changes require Project 0.6 modular-spatial-v1 and the explicit physical-coordinates-zero-fill/identity-sources mapping')
    if changed_domain:
        validate_domain_objects(sim,grid)
    export, restore = adapters(sim.project)
    payload = export(sim,binary=True)
    payload['project_sha256'] = _hash(target_project)
    if changed_domain:
        payload['migration_origin_project'] = deepcopy(getattr(sim,'migration_origin_project',sim.project))
    payload['local_fields']['grid'] = asdict(grid)
    payload['local_fields']['diffusivities_um2_s'] = dict(target.fields.diffusivities_um2_s)
    if modular:
        derived_fields, obstacles, _ = target._geometry_effects(target.fields, [], sim.geometry_state)
        blocked = derived_fields.blocked
    else:
        obstacles, blocked = target._geometry_for(sim.materials)
    payload['local_fields']['blocked'] = np.asarray(blocked).reshape(-1)
    audits = {}
    component_audits = {}
    mapped = {}
    for species, values in sim.fields.concentrations_uM.items():
        result = remap_physical(np.asarray(values).reshape(source_grid.shape),source_grid,grid)
        if np.any(result.ravel()[np.asarray(blocked).ravel()] != 0):
            raise ValueError('Conservative mapping would put inventory inside an obstacle; a different explicit mapping is required')
        initial = math.fsum(np.asarray(values).ravel()) * source_grid.molecules_per_uM_voxel
        final = math.fsum(result.ravel()) * grid.molecules_per_uM_voxel
        if not math.isclose(initial,final,rel_tol=1e-11,abs_tol=1e-9):
            raise ValueError(f'Domain crop would discard {initial-final:.17g} molecules of {species}; an explicit outflow ledger mapper is required')
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
                    resolved = {**spec,'tensor_shape':[node.parameters[d.split(':',1)[1]].value if isinstance(d,str) else d for d in spec.get('tensor_shape',[3] if shape=='field.vector' else [])]}
                    mapped_value = map_field_value(value, resolved, source_grid, grid, f'{node.id}.{port}')
                    payload[attribute][node.id][port] = mapped_value
                    amount = spec['unit']=='molecule'
                    component_audits[f'{attribute}.{node.id}.{port}'] = {
                        'unit':'molecule','component_shape':list(np.asarray(value).shape[3:]),
                        'before':(np.sum(value,axis=(0,1,2))*(1 if amount else source_grid.molecules_per_uM_voxel)).tolist(),
                        'after':(np.sum(mapped_value,axis=(0,1,2))*(1 if amount else grid.molecules_per_uM_voxel)).tolist()}
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
    return candidate, {'kind':'physical-domain-conservative' if changed_domain else 'same-domain-conservative', 'mapping':mapping, 'step_index':sim.frame_index,
        'domain_rule':{'coordinates':'unchanged-physical-xyz','outside_source':'zero','crop':'reject-nonzero-inventory','external_transfer_molecules':0},
        'source_grid':asdict(source_grid),'target_grid':asdict(grid),'inventories':audits,'field_components':component_audits,
        'origin_project_sha256':_hash(getattr(candidate,'migration_origin_project',candidate.project)),
        'preserved':['time','cell IDs','physical coordinates','RNG','non-field module state','source stocks','future schedules']}
