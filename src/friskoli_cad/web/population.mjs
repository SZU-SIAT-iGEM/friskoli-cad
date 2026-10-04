const extent = domain => domain.counts_xyz.map((count, axis) => count * domain.spacing_um_xyz[axis]);
const number = value => Number(value.toFixed(4));
const hash = text => [...text].reduce((value, letter) => Math.imul(value ^ letter.charCodeAt(0), 16777619) >>> 0, 2166136261);

export function rotationQuaternion(rotation = [0, 0, 0]) {
  const [x, y, z] = rotation.map(d => d * Math.PI / 360);
  const [a, b, c] = [Math.cos(x), Math.cos(y), Math.cos(z)];
  const [d, e, f] = [Math.sin(x), Math.sin(y), Math.sin(z)];
  return [d*b*c+a*e*f, a*e*c-d*b*f, a*b*f+d*e*c, a*b*c-d*e*f];
}
export function multiplyQuaternion([x,y,z,w], [a,b,c,d]) {
  return [w*a+x*d+y*c-z*b, w*b-x*c+y*d+z*a, w*c+x*b-y*a+z*d, w*d-x*a-y*b-z*c];
}
export function rotatePoint(point, q) {
  return multiplyQuaternion(multiplyQuaternion(q, [...point, 0]), [-q[0],-q[1],-q[2],q[3]]).slice(0,3);
}
export function checkBlock(domain, block) {
  const size = extent(domain), rotation = block.rotation ?? [0, 0, 0];
  if (![...block.center, ...block.size, ...rotation, block.length, block.diameter].every(Number.isFinite) ||
      block.size.some(v => v <= 0) || block.length < block.diameter || block.diameter <= 0) throw new Error('Invalid population geometry');
  if (domain.geometry === 'thin_layer' && (Math.abs(rotation[0]) > 1e-8 || Math.abs(rotation[1]) > 1e-8)) throw new Error('Thin-layer volumes only rotate around Z');
  const q = rotationQuaternion(rotation);
  for (let bits = 0; bits < 8; bits++) {
    const corner = rotatePoint(block.size.map((v, i) => (bits & (1 << i) ? 1 : -1) * v / 2), q);
    if (corner.some((v, i) => v + block.center[i] < -1e-7 || v + block.center[i] > size[i] + 1e-7)) throw new Error('transformOutside');
  }
  return true;
}

function random(seed) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = Math.imul(state ^ state >>> 15, 1 | state);
    value ^= value + Math.imul(value ^ value >>> 7, 61 | value);
    return ((value ^ value >>> 14) >>> 0) / 4294967296;
  };
}

export function blocksFromProject(project) {
  const size = extent(project.domain);
  return Object.entries(project.groups).map(([id, group]) => {
    const length = group.initial_geometry?.find(Boolean)?.length_um ?? Math.min(...project.domain.spacing_um_xyz) * .8;
    const diameter = group.initial_geometry?.find(Boolean)?.diameter_um ?? length * .45;
    const points = group.positions_um;
    const center = size.map((limit, axis) => points.length
      ? (Math.min(...points.map(point => point[axis])) + Math.max(...points.map(point => point[axis]))) / 2
      : limit / 2);
    const dimensions = size.map((limit, axis) => {
      if (axis === 2 && project.domain.geometry === 'thin_layer') return limit;
      const spread = points.length ? Math.max(...points.map(point => point[axis])) -
        Math.min(...points.map(point => point[axis])) : 0;
      return Math.min(limit, Math.max(length * 1.5, spread + length));
    });
    return { id, name: id, center: center.map(number), size: dimensions.map(number),
      count: group.ids.length, length: number(length), diameter: number(diameter), seed: hash(id),
      rotation: [0, 0, 0], dirty: false };
  });
}

export function createBlock(project, center, existingIds = []) {
  const size = extent(project.domain);
  let index = 1;
  while (project.groups[`population_${index}`] || existingIds.includes(`population_${index}`)) index += 1;
  const id = `population_${index}`;
  const length = Math.min(3, size[0] * .22, size[1] * .22);
  const diameter = Math.min(length * .4, size[2] * .45);
  const dimensions = [Math.min(size[0], Math.max(length * 2, size[0] * .28)),
    Math.min(size[1], Math.max(length * 2, size[1] * .28)),
    project.domain.geometry === 'thin_layer' ? size[2] : Math.min(size[2], Math.max(length * 1.5, size[2] * .55))];
  const clamped = center.map((value, axis) => Math.max(dimensions[axis] / 2,
    Math.min(size[axis] - dimensions[axis] / 2, value)));
  if (project.domain.geometry === 'thin_layer') clamped[2] = size[2] / 2;
  return { id, name: id, center: clamped.map(number), size: dimensions.map(number),
    count: 32, length: number(length), diameter: number(diameter), seed: hash(id), rotation: [0, 0, 0], dirty: true,
    hidden: false, locked: false };
}

export function scatterBlock(project, block, staticModule = null, {allowShortfall=false}={}) {
  const domain = project.domain;
  const size = extent(domain);
  checkBlock(domain, block);
  if (!Number.isInteger(block.count) || block.count < 1 || block.count > 2000 ||
      !Number.isInteger(block.seed) || block.length < block.diameter || block.diameter <= 0) {
    throw new Error('Invalid population count, seed, or capsule size');
  }
  for (let axis = 0; axis < 3; axis += 1) {
    const pad = axis === 2 && domain.geometry === 'thin_layer' ? block.diameter / 2 : block.length / 2;
    if (!Number.isFinite(block.center[axis]) || !Number.isFinite(block.size[axis]) ||
        block.size[axis] < pad * 2) {
      throw new Error('Population volume or capsule exceeds the domain');
    }
  }
  const draw = random(block.seed);
  const previous = project.groups[block.id];
  const needsNode = !project.graph.nodes.some(node => node.owner.kind === 'population' && node.owner.id === block.id);
  if (needsNode && (!staticModule?.id || staticModule.scope !== 'population')) {
    throw new Error('Backend static population module is unavailable');
  }
  const otherCells = Object.entries(project.groups).reduce((total, [id, group]) =>
    total + (id === block.id ? 0 : group.ids.length), 0);
  if (otherCells + block.count > 2000) throw new Error('Local replay supports at most 2000 cells');
  const ids = Array.from({ length: block.count }, (_, index) => previous?.ids[index] ?? `${block.id}:cell_${index}`);
  const positions = [];
  const orientations = [];
  const spatial = project.execution_profile === 'modular-spatial-v1';
  // Conservative enclosing spheres keep scatter deterministic and collision-free.
  // Dense requests fail without mutating the project; no overlapping fallback is emitted.
  const occupied = spatial ? Object.entries(project.groups).flatMap(([id,group]) => id === block.id ? [] :
    group.positions_um.map((position,index) => ({position,radius:(group.initial_geometry?.[index]?.length_um ?? block.length)/2}))) : [];
  const solids = project.graph.nodes.filter(node => ['space.axis_aligned_obstacle','material.degradable_box'].includes(node.module_id))
    .map(node => ({lower:[...'xyz'].map(a=>node.parameters[`lower_${a}_um`]?.value),upper:[...'xyz'].map(a=>node.parameters[`upper_${a}_um`]?.value)}));
  let attempts = 0;
  for (let index = 0; index < block.count; index += 1) {
    if (++attempts > block.count*500) {if(allowShortfall&&positions.length){block.count=positions.length;ids.length=positions.length;break;}throw new Error('scatterPackingFailed');}
    const point = block.center.map((center, axis) => {
      if (axis === 2 && domain.geometry === 'thin_layer') return size[2] / 2;
      const usable = block.size[axis] - block.length;
      return number(center - usable / 2 + draw() * usable);
    });
    const q = rotationQuaternion(block.rotation);
    positions.push(rotatePoint(point.map((v, i) => v - block.center[i]), q).map((v, i) => v + block.center[i]));
    if (domain.geometry === 'thin_layer') {
      const angle = draw() * Math.PI;
      orientations.push([0, 0, number(Math.sin(angle)), number(Math.cos(angle))]);
    } else {
      const [u1, u2, u3] = [draw(), draw(), draw()];
      const a = Math.sqrt(1 - u1), b = Math.sqrt(u1);
      orientations.push([number(a * Math.sin(2 * Math.PI * u2)), number(a * Math.cos(2 * Math.PI * u2)),
        number(b * Math.sin(2 * Math.PI * u3)), number(b * Math.cos(2 * Math.PI * u3))]);
    }
    orientations[index] = multiplyQuaternion(q, orientations[index]);
    if (spatial) {
      const position = positions[index], radius = block.length/2;
      const overlap = occupied.some(cell => position.reduce((sum,v,i)=>sum+(v-cell.position[i])**2,0) < (radius+cell.radius)**2) ||
        solids.some(box => position.reduce((sum,v,i)=>sum+Math.max(box.lower[i]-v,0,v-box.upper[i])**2,0) < radius**2);
      if (overlap) { positions.pop(); orientations.pop(); index--; continue; }
      const norm = Math.hypot(...orientations[index]); orientations[index] = orientations[index].map(v=>v/norm);
      occupied.push({position,radius});
    }
  }
  project.groups[block.id] = { ids, positions_um: positions, orientation_xyzw: orientations,
    initial_geometry: ids.map(() => ({ shape: 'capsule', length_um: block.length,
      diameter_um: block.diameter, provenance: { kind: 'estimated', reference: 'user-defined population volume scatter' } })) };
  if (['0.1.0','0.2.0'].includes(project.project_version)) project.project_version = '0.2.0';
  if (needsNode) {
    let nodeId = `${block.id}_static`;
    while (project.graph.nodes.some(node => node.id === nodeId)) nodeId += '_1';
    project.graph.nodes.push({ id: nodeId, module_id: staticModule.id,
      module_version: staticModule.version, owner: { kind: 'population', id: block.id }, parameters: {} });
  }
  if (!project.run.groups.includes(block.id)) project.run.groups.push(block.id);
  block.dirty = false;
  return project.groups[block.id];
}
