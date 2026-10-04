// Adapters are editor capabilities, not a fixed list of biological modules.
import { addNode, removeNode, setParameter } from './graph-edit.mjs';
import { scatterBlock } from './population.mjs';

export function availablePlaceables(modules, capabilities = null, objects = new Map(), project = null) {
  return [...objects.values()].map(object => {
    const supported = (object.kind === 'population' && object.initializer.adapter === 'population.block@1') || isEnvironmentObject(object);
    const present = [object.initializer.module, ...object.initializer.data_modules].every(key => modules.has(key));
    const profile = project?.execution_profile ?? 'modular-spatial-v1';
    const enabled = !capabilities || (capabilities.placeable_profiles?.[profile] ?
      capabilities.placeable_profiles[profile].includes(object.initializer.module) :
      profile === 'modular-spatial-v1' && capabilities.placeables?.some(p => p.kind === object.kind && p.module === object.initializer.module));
    const exhausted = ['local_source','degradable_box'].includes(object.kind) && project && !Object.keys(project.species).length;
    const required = (object.initializer.requirements ?? []).every(requirement => modules.has(requirement.default_module) ||
      project && roleProviders(project,modules,requirement).length);
    return {...object, hint:object.description, module:object.initializer.module, tool:supported ? (object.kind === 'population' ? 'population' : 'environment') : null,
      status:!supported ? 'unsupported' : !present || !enabled || !required ? 'missing' : exhausted ? 'noSourceSpecies' : 'ready'};
  });
}

// Only these exact contracts have a spatial editor adapter. A new declaration alone
// never makes an unknown module executable or gives it invented geometry.
export function isEnvironmentObject(object) {
  return object?.initializer?.adapter === 'environment.node@1' &&
    ((object.kind === 'obstacle_box' && object.initializer.module === 'space.axis_aligned_obstacle@1.0.0') ||
     (object.kind === 'degradable_box' && object.initializer.module === 'material.degradable_box@1.0.0') ||
     (object.kind === 'local_source' && object.initializer.module === 'source.finite_local@1.0.0'));
}

export function availableSourceSpecies(project) {
  return Object.keys(project.species);
}

export function roleProviders(project,modules,requirement,excludedId=null) {
  return project.graph.nodes.filter(node => node.id !== excludedId && node.owner.kind === requirement.scope &&
    modules.get(`${node.module_id}@${node.module_version}`)?.declaration?.provides_roles?.includes(requirement.role));
}

export function applyRegisteredDefaults(node,manifest) {
  for (const [name,value] of Object.entries(manifest.declaration?.default_parameters ?? {})) {
    const error = setParameter(node,manifest,name,value); if (error) throw new Error(error);
    node.parameters[name].provenance = {kind:'example',reference:'constructed registry defaults; not biological calibration'};
  }
  return node;
}

export function requiredRoleRemovalProblem(project,id,objects,modules) {
  for (const node of project.graph.nodes) {
    if (node.id === id) continue;
    const object = [...objects.values()].find(item => item.initializer.module === `${node.module_id}@${node.module_version}`);
    for (const requirement of object?.initializer.requirements ?? []) {
      if (roleProviders(project,modules,requirement).some(provider => provider.id === id) &&
          !roleProviders(project,modules,requirement,id).length) return requirement.role;
    }
  }
  return null;
}

function segmentBoxDistanceSquared(a,b,lower,upper) {
  const direction = b.map((v,i) => v-a[i]), breaks = [0,1];
  for (let i=0;i<3;i++) if (direction[i]) for (const bound of [lower[i],upper[i]]) {
    const t = (bound-a[i])/direction[i]; if (t>0 && t<1) breaks.push(t);
  }
  breaks.sort((a,b) => a-b);
  const distance = t => a.reduce((sum,v,i) => { const p = v+t*direction[i]; return sum+Math.max(lower[i]-p,0,p-upper[i])**2; },0);
  let best = Infinity;
  for (let j=0;j<breaks.length-1;j++) {
    const lo = breaks[j], hi = breaks[j+1], mid = (lo+hi)/2;
    let linear=0,quadratic=0;
    for (let i=0;i<3;i++) {
      const p = a[i]+mid*direction[i], bound = p < lower[i] ? lower[i] : p > upper[i] ? upper[i] : null;
      if (bound === null) continue;
      linear += direction[i]*(a[i]-bound); quadratic += direction[i]**2;
    }
    best = Math.min(best,distance(lo),distance(hi),distance(quadratic ? Math.max(lo,Math.min(hi,-linear/quadratic)) : mid));
  }
  return best;
}

function obstaclePlacementProblem(project,lower,upper) {
  for (const node of project.graph.nodes.filter(node => ['space.axis_aligned_obstacle','material.degradable_box'].includes(node.module_id))) {
    if ([...'xyz'].every((axis,i) => lower[i] <= node.parameters[`upper_${axis}_um`]?.value && upper[i] >= node.parameters[`lower_${axis}_um`]?.value)) return 'obstaclePlacementOverlap';
  }
  for (const group of Object.values(project.groups)) for (let i=0;i<group.ids.length;i++) {
    const geometry = group.initial_geometry?.[i];
    if (!geometry) continue;
    const [x,y,z,w] = group.orientation_xyzw[i], norm = x*x+y*y+z*z+w*w;
    if (!norm) return 'invalidCellOrientation';
    const direction = [(w*w+x*x-y*y-z*z)/norm,2*(x*y+w*z)/norm,2*(x*z-w*y)/norm];
    const center = group.positions_um[i], half = (geometry.length_um-geometry.diameter_um)/2;
    const a = center.map((v,j) => v-half*direction[j]), b = center.map((v,j) => v+half*direction[j]);
    if (segmentBoxDistanceSquared(a,b,lower,upper) <= (geometry.diameter_um/2)**2) return 'obstaclePlacementOverlap';
  }
  for (const node of project.graph.nodes.filter(node => node.module_id === 'source.finite_local')) {
    const center = [...'xyz'].map(axis => node.parameters[`center_${axis}_um`]?.value);
    if (segmentBoxDistanceSquared(center,center,lower,upper) <= node.parameters.radius_um?.value**2) return 'obstaclePlacementOverlap';
  }
  return null;
}

export function environmentObjects(project, objects, modules, objectStates = null) {
  return project.graph.nodes.flatMap(node => {
    const key = `${node.module_id}@${node.module_version}`;
    const declaration = [...objects.values()].find(object => isEnvironmentObject(object) && object.initializer.module === key);
    if (!declaration || !modules.has(key)) return [];
    const value = name => node.parameters[name]?.value;
    const geometry = declaration.kind !== 'local_source' ? {
      lower: [...'xyz'].map(axis => value(`lower_${axis}_um`)),
      upper: [...'xyz'].map(axis => value(`upper_${axis}_um`))
    } : {center:[...'xyz'].map(axis => value(`center_${axis}_um`)), radius:value('radius_um')};
    const observed = objectStates?.[node.id];
    const remaining = observed?.object_type === declaration.id && Number.isFinite(observed.remaining_molecules) && observed.remaining_molecules >= 0 ? observed.remaining_molecules : null;
    return [{id:node.id, node, declaration, kind:declaration.kind, remaining_molecules:remaining, ...geometry}];
  });
}

// Solid boxes occupy whole voxels. A source radius is release support, which may
// extend beyond the domain; only its center must remain inside the field grid.
export function environmentTransformGeometry(object, domain, transform) {
  const vector = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite);
  const spacing = domain?.spacing_um_xyz, counts = domain?.counts_xyz;
  if (!vector(spacing) || spacing.some(v => v <= 0) || !vector(counts) || counts.some(v => !Number.isSafeInteger(v) || v < 1) ||
      !vector(transform?.center)) throw new Error('environmentTransformOutside');
  const extent = counts.map((n,i) => n*spacing[i]);
  if (!extent.every(Number.isFinite)) throw new Error('environmentTransformOutside');
  const thin = domain.geometry === 'thin_layer', clamp = (v,lo,hi) => Math.max(lo,Math.min(hi,v));
  if (object.kind === 'local_source') {
    if (!Number.isFinite(transform.radius) || transform.radius <= 0 || !vector(object.center)) throw new Error('environmentTransformOutside');
    const center = transform.center.map((value,i) => thin && i === 2 ? object.center[i] :
      clamp(value,0,extent[i] - Math.max(Number.EPSILON*extent[i]*2,Number.MIN_VALUE)));
    if (center.some((value,i) => value < 0 || value >= extent[i])) throw new Error('environmentTransformOutside');
    return {center,radius:transform.radius};
  }
  if (!['obstacle_box','degradable_box'].includes(object.kind) || !vector(transform.size) || transform.size.some(v => v <= 0) ||
      !vector(object.lower) || !vector(object.upper)) throw new Error('environmentTransformOutside');
  const lower=[], upper=[];
  for (let i=0;i<3;i++) {
    if (thin && i === 2) {lower[i]=object.lower[i];upper[i]=object.upper[i];}
    else {
      const cells=clamp(Math.round(transform.size[i]/spacing[i]),1,counts[i]);
      const first=clamp(Math.round(transform.center[i]/spacing[i]-cells/2),0,counts[i]-cells);
      lower[i]=first*spacing[i];upper[i]=(first+cells)*spacing[i];
    }
    if (lower[i] < 0 || upper[i] > extent[i] || lower[i] >= upper[i]) throw new Error('environmentTransformOutside');
  }
  return {lower,upper,center:lower.map((v,i)=>(v+upper[i])/2),size:lower.map((v,i)=>upper[i]-v)};
}

export function transformEnvironmentObject(project, id, objects, modules, transform) {
  const object=environmentObjects(project,objects,modules).find(item=>item.id===id);
  if (!object) throw new Error('Unsupported object initializer');
  const geometry=environmentTransformGeometry(object,project.domain,transform);
  // Exclude the edited object when applying the same collision policy as placement.
  const others={...project,graph:{...project.graph,nodes:project.graph.nodes.filter(node=>node.id!==id)}};
  if (object.kind !== 'local_source') {
    const problem=obstaclePlacementProblem(others,geometry.lower,geometry.upper);
    if (problem) throw new Error(problem);
  } else {
    for (const obstacle of environmentObjects(others,objects,modules).filter(item=>item.kind!=='local_source')) {
      if (segmentBoxDistanceSquared(geometry.center,geometry.center,obstacle.lower,obstacle.upper) <= geometry.radius**2)
        throw new Error('sourcePlacementOverlap');
    }
  }
  const node=structuredClone(object.node), manifest=modules.get(object.declaration.initializer.module);
  const values=object.kind === 'local_source' ? {radius_um:geometry.radius,
    ...Object.fromEntries([...'xyz'].map((axis,i)=>[`center_${axis}_um`,geometry.center[i]]))} :
    Object.fromEntries([...'xyz'].flatMap((axis,i)=>[[`lower_${axis}_um`,geometry.lower[i]],[`upper_${axis}_um`,geometry.upper[i]]]));
  for (const [name,value] of Object.entries(values)) {
    if (node.parameters[name]?.value === value) continue;
    const problem=setParameter(node,manifest,name,value);if(problem)throw new Error(problem);
  }
  object.node.parameters=node.parameters;
  return geometry;
}

export function initializeEnvironmentObject(project, object, modules, point, speciesId = null) {
  if (!isEnvironmentObject(object)) throw new Error('Unsupported object initializer');
  const keys = [object.initializer.module, ...object.initializer.data_modules];
  if (keys.some(key => !modules.has(key))) throw new Error('Missing object initializer module');
  if (!Array.isArray(point) || point.length !== 3 || !point.every(Number.isFinite)) throw new Error('Invalid placement');
  const next = structuredClone(project), graph = next.graph;
  const manifest = modules.get(object.initializer.module);
  const species = Object.keys(next.species);
  if (['local_source','degradable_box'].includes(object.kind) && !species.length) throw new Error('noSourceSpecies');
  if (['local_source','degradable_box'].includes(object.kind)) {
    speciesId ??= species.length === 1 ? species[0] : null;
    if (!species.includes(speciesId)) throw new Error('chooseSourceSpecies');
  }
  const node = addNode(graph, manifest, {kind:manifest.scope,id:'domain'}, species);
  // Physical inventories and solids are owned per object, not by the shared domain.
  node.owner.id = node.id;
  const write = (target, spec, name, value) => {
    const error = setParameter(target, spec, name, value); if (error) throw new Error(error);
  };
  const constructed = (target,spec,name,value) => {
    write(target,spec,name,value);
    target.parameters[name].provenance = {kind:'example',reference:'constructed spatial editor defaults; not biological calibration'};
  };
  if (manifest.parameters.species && speciesId) write(node,manifest,'species',speciesId);
  const spacing = next.domain.spacing_um_xyz, counts = next.domain.counts_xyz;
  if (object.kind !== 'local_source') {
    for (let i = 0; i < 3; i++) {
      const index = Math.max(0, Math.min(counts[i]-1, Math.floor(point[i]/spacing[i])));
      write(node,manifest,`lower_${'xyz'[i]}_um`,index*spacing[i]);
      write(node,manifest,`upper_${'xyz'[i]}_um`,(index+1)*spacing[i]);
    }
    const problem = obstaclePlacementProblem(project,[...'xyz'].map(axis => node.parameters[`lower_${axis}_um`].value),[...'xyz'].map(axis => node.parameters[`upper_${axis}_um`].value));
    if (problem) throw new Error(problem);
    if (object.kind === 'degradable_box') constructed(node,manifest,'initial_molecules',100000);
  } else {
    const center = point.map((v,i) => (Math.max(0,Math.min(counts[i]-1,Math.floor(v/spacing[i])))+.5)*spacing[i]);
    const radius = Math.min(...spacing)*.5;
    for (const obstacle of graph.nodes.filter(item => ['space.axis_aligned_obstacle','material.degradable_box'].includes(item.module_id))) {
      const lower = [...'xyz'].map(axis => obstacle.parameters[`lower_${axis}_um`]?.value);
      const upper = [...'xyz'].map(axis => obstacle.parameters[`upper_${axis}_um`]?.value);
      if (segmentBoxDistanceSquared(center,center,lower,upper) <= radius**2) throw new Error('sourcePlacementOverlap');
    }
    for (let i = 0; i < 3; i++) write(node,manifest,`center_${'xyz'[i]}_um`,center[i]);
    write(node,manifest,'radius_um',radius);
    constructed(node,manifest,'initial_molecules',100000);
    constructed(node,manifest,'release_rate',1000);
  }
  for (const key of object.initializer.data_modules) {
    if (!['field.diffusive_local@1.0.0','field.diffusive_local@2.0.0'].includes(key)) throw new Error('Unsupported source data module');
    const spec = modules.get(key);
    if (!graph.nodes.some(item => item.module_id === spec.id && item.parameters.species?.value === speciesId)) {
      const field = addNode(graph,spec,{kind:spec.scope,id:'domain'},[speciesId]);
      applyRegisteredDefaults(field,spec);
      write(field,spec,'species',speciesId);
      if (!field.parameters.diffusivity_um2_s) constructed(field,spec,'diffusivity_um2_s',10);
      if (spec.parameters.initial_concentration && next.species[speciesId]?.initial_concentration) field.parameters.initial_concentration=structuredClone(next.species[speciesId].initial_concentration);
    }
  }
  for (const requirement of object.initializer.requirements ?? []) {
    if (roleProviders(next,modules,requirement).length) continue;
    const spec = modules.get(requirement.default_module);
    if (!spec || spec.scope !== requirement.scope || !spec.declaration?.provides_roles?.includes(requirement.role)) throw new Error('Unavailable required role: '+requirement.role);
    const provider = addNode(graph,spec,{kind:requirement.scope,id:'domain'},species);
    applyRegisteredDefaults(provider,spec);
  }
  Object.assign(project,next);
  return node.id;
}

// Remove the source's generated field only if no other node uses it. Shared graph
// dependencies remain visible so the compiler can report missing connections.
export function deleteEnvironmentObject(project, id) {
  const node = project.graph.nodes.find(item => item.id === id);
  if (!node) return false;
  const species = node.parameters.species?.value;
  const remainingSource = project.graph.nodes.some(item => item.id !== id && ['source.finite_local','material.degradable_box'].includes(item.module_id) && item.parameters.species?.value === species);
  const dependents = !remainingSource && ['source.finite_local','material.degradable_box'].includes(node.module_id) ? project.graph.nodes
    .filter(item => item.module_id === 'field.diffusive_local' && item.parameters.species?.value === species &&
      !project.graph.edges.some(edge => edge.from.node === item.id) && !Object.values(project.run.channels).some(channel => channel.node === item.id))
    .map(item => item.id) : [];
  const removed = new Set([id,...dependents]);
  for (const target of removed) removeNode(project.graph,target);
  for (const [key,channel] of Object.entries(project.run.channels)) if (removed.has(channel.node)) delete project.run.channels[key];
  return true;
}

export function objectForBlock(block, objects) {
  return block.object_type ? objects.get(block.object_type) : [...objects.values()].find(object =>
    object.kind === 'population' && object.initializer.adapter === 'population.block@1');
}

// Initialization and added readers belong to one undoable document transaction.
// A binding prevents re-scatter from recreating readers deliberately deleted by the user.
export function initializeObject(project, block, object, modules, options={}) {
  if (!object || object.initializer.adapter !== 'population.block@1') throw new Error('Unsupported object initializer');
  const keys = [object.initializer.module, ...object.initializer.data_modules];
  for (const key of keys) {
    const manifest = modules.get(key);
    if (!manifest || manifest.scope !== 'population' || Object.keys(manifest.parameters).length ||
        Object.keys(manifest.inputs).length) throw new Error('Unsupported initializer contract: ' + key);
  }
  const nextProject = structuredClone(project), nextBlock = structuredClone(block);
  scatterBlock(nextProject, nextBlock, modules.get(object.initializer.module), options);
  if (!nextBlock.binding) {
    const data = [];
    for (const key of object.initializer.data_modules) {
      const manifest = modules.get(key);
      const node = nextProject.graph.nodes.find(n => n.owner.kind === 'population' && n.owner.id === block.id &&
        n.module_id === manifest.id && n.module_version === manifest.version) ??
        addNode(nextProject.graph, manifest, {kind:'population',id:block.id});
      data.push(node.id);
    }
    nextBlock.binding = {data_nodes:data};
  }
  nextBlock.object_type = object.id;
  Object.assign(project, nextProject); Object.assign(block, nextBlock);
  return project.groups[block.id];
}
