const extent = domain => domain.counts_xyz.map((count, axis) => count * domain.spacing_um_xyz[axis]);
const number = value => Number(value.toFixed(4));
const hash = text => [...text].reduce((value, letter) => Math.imul(value ^ letter.charCodeAt(0), 16777619) >>> 0, 2166136261);

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
      dirty: false };
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
    count: 32, length: number(length), diameter: number(diameter), seed: hash(id), dirty: true };
}

export function scatterBlock(project, block, staticModule = null) {
  const domain = project.domain;
  const size = extent(domain);
  if (!Number.isInteger(block.count) || block.count < 1 || block.count > 2000 ||
      !Number.isInteger(block.seed) || block.length < block.diameter || block.diameter <= 0) {
    throw new Error('Invalid population count, seed, or capsule size');
  }
  for (let axis = 0; axis < 3; axis += 1) {
    const pad = axis === 2 && domain.geometry === 'thin_layer' ? block.diameter / 2 : block.length / 2;
    if (!Number.isFinite(block.center[axis]) || !Number.isFinite(block.size[axis]) ||
        block.size[axis] < pad * 2 || block.center[axis] - block.size[axis] / 2 < 0 ||
        block.center[axis] + block.size[axis] / 2 > size[axis]) {
      throw new Error('Population volume or capsule exceeds the domain');
    }
  }
  const draw = random(block.seed);
  const previous = project.groups[block.id];
  const otherCells = Object.entries(project.groups).reduce((total, [id, group]) =>
    total + (id === block.id ? 0 : group.ids.length), 0);
  if (otherCells + block.count > 2000) throw new Error('Local replay supports at most 2000 cells');
  const ids = Array.from({ length: block.count }, (_, index) => previous?.ids[index] ?? `${block.id}:cell_${index}`);
  const positions = [];
  const orientations = [];
  for (let index = 0; index < block.count; index += 1) {
    const point = block.center.map((center, axis) => {
      if (axis === 2 && domain.geometry === 'thin_layer') return size[2] / 2;
      const usable = block.size[axis] - block.length;
      return number(center - usable / 2 + draw() * usable);
    });
    positions.push(point);
    if (domain.geometry === 'thin_layer') {
      const angle = draw() * Math.PI;
      orientations.push([0, 0, number(Math.sin(angle)), number(Math.cos(angle))]);
    } else {
      const [u1, u2, u3] = [draw(), draw(), draw()];
      const a = Math.sqrt(1 - u1), b = Math.sqrt(u1);
      orientations.push([number(a * Math.sin(2 * Math.PI * u2)), number(a * Math.cos(2 * Math.PI * u2)),
        number(b * Math.sin(2 * Math.PI * u3)), number(b * Math.cos(2 * Math.PI * u3))]);
    }
  }
  project.groups[block.id] = { ids, positions_um: positions, orientation_xyzw: orientations,
    initial_geometry: ids.map(() => ({ shape: 'capsule', length_um: block.length,
      diameter_um: block.diameter, provenance: { kind: 'estimated', reference: 'user-defined population volume scatter' } })) };
  project.project_version = '0.2.0';
  if (!project.graph.nodes.some(node => node.owner.kind === 'population' && node.owner.id === block.id)) {
    if (staticModule?.id !== 'population.static' || staticModule.scope !== 'population') {
      throw new Error('Backend static population module is unavailable');
    }
    project.graph.nodes.push({ id: `${block.id}_static`, module_id: staticModule.id,
      module_version: staticModule.version, owner: { kind: 'population', id: block.id }, parameters: {} });
  }
  if (!project.run.groups.includes(block.id)) project.run.groups.push(block.id);
  block.dirty = false;
  return project.groups[block.id];
}
