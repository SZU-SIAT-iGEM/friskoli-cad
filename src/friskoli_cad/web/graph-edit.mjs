// Behavior-graph edits. Checks mirror protocol/validation.py so the editor can
// flag problems immediately; the backend compiler remains the authority.
const key = node => `${node.module_id}@${node.module_version}`;
const portType = port => `${port.shape}|${port.quantity}|${port.unit}`;
const speciesOf = (node, port) => port.species_parameter ? node.parameters[port.species_parameter]?.value ?? null : null;
const sameOwner = (a, b) => a.owner.kind === b.owner.kind && a.owner.id === b.owner.id;
const USER = { kind: 'user', reference: 'set in Friskoli-CAD' };

function uniqueId(taken, base) {
  let index = 1;
  while (taken.has(`${base}_${index}`)) index += 1;
  return `${base}_${index}`;
}

export function defaultParameters(manifest, species = []) {
  const parameters = {};
  for (const [name, definition] of Object.entries(manifest.parameters)) {
    if (definition.type === 'number' || definition.type === 'integer') {
      let value = definition.minimum ?? 0;
      if (definition.maximum !== undefined) value = Math.min(value, definition.maximum);
      if (definition.type === 'integer') value = Math.ceil(value);
      parameters[name] = { value, unit: definition.unit, provenance: { ...USER } };
    } else if (definition.type === 'boolean') parameters[name] = { value: false, provenance: { ...USER } };
    else parameters[name] = { value: species[0] ?? name, provenance: { ...USER } };
  }
  return parameters;
}

export function addNode(graph, manifest, owner, species = []) {
  if (owner?.kind !== manifest.scope || !owner.id) throw new Error('node.scope');
  const id = uniqueId(new Set(graph.nodes.map(node => node.id)), manifest.id.split('.').at(-1));
  const node = { id, module_id: manifest.id, module_version: manifest.version, owner: { ...owner },
    parameters: defaultParameters(manifest, species) };
  graph.nodes.push(node);
  return node;
}

export function removeNode(graph, id) {
  const index = graph.nodes.findIndex(node => node.id === id);
  if (index < 0) return false;
  graph.nodes.splice(index, 1);
  graph.edges = graph.edges.filter(edge => edge.from.node !== id && edge.to.node !== id);
  return true;
}

function reaches(graph, start, goal) {
  const seen = new Set();
  const stack = [start];
  while (stack.length) {
    const id = stack.pop();
    if (id === goal) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    for (const edge of graph.edges) if (edge.timing === 'same_step' && edge.from.node === id) stack.push(edge.to.node);
  }
  return false;
}

// Returns a validation.py error code for a proposed edge, or null when it is acceptable.
export function connectionProblem(graph, modules, from, to, timing) {
  const src = graph.nodes.find(node => node.id === from.node);
  const dst = graph.nodes.find(node => node.id === to.node);
  if (!src || !dst) return 'edge.node';
  const srcManifest = modules.get(key(src)), dstManifest = modules.get(key(dst));
  const output = srcManifest?.outputs[from.port], input = dstManifest?.inputs[to.port];
  if (!output || !input) return 'edge.port';
  if (portType(output) !== portType(input)) return 'edge.type';
  if (speciesOf(src, output) !== speciesOf(dst, input)) return 'edge.species';
  if (output.shape.startsWith('cell.') && !sameOwner(src, dst)) return 'edge.population';
  if (graph.edges.some(edge => edge.to.node === to.node && edge.to.port === to.port)) return 'edge.multiple_inputs';
  if (timing === 'same_step') {
    if (srcManifest.phase > dstManifest.phase) return 'edge.phase';
    if (src.id === dst.id || reaches(graph, dst.id, src.id)) return 'edge.cycle';
  } else if (timing === 'previous_step') {
    if (!srcManifest.initial_outputs?.includes(from.port)) return 'edge.initial';
  } else return 'edge.timing';
  return null;
}

// Picks same_step when legal, otherwise previous_step, so a drag can connect backwards.
export function preferredTiming(graph, modules, from, to) {
  if (!connectionProblem(graph, modules, from, to, 'same_step')) return 'same_step';
  if (!connectionProblem(graph, modules, from, to, 'previous_step')) return 'previous_step';
  return null;
}

export function connect(graph, modules, from, to, timing = preferredTiming(graph, modules, from, to)) {
  const problem = timing ? connectionProblem(graph, modules, from, to, timing) : 'edge.type';
  if (problem) throw new Error(problem);
  const id = uniqueId(new Set(graph.edges.map(edge => edge.id)), `${from.node}_to_${to.node}`);
  const edge = { id, from: { ...from }, to: { ...to }, timing };
  graph.edges.push(edge);
  return edge;
}

export function disconnect(graph, edgeId) {
  const before = graph.edges.length;
  graph.edges = graph.edges.filter(edge => edge.id !== edgeId);
  return graph.edges.length !== before;
}

// Mirrors _parameter_value: returns an error code or null, and writes only valid values.
export function setParameter(node, manifest, name, raw) {
  const definition = manifest.parameters[name];
  if (!definition) return 'parameter.set';
  let value = raw;
  if (definition.type === 'number' || definition.type === 'integer') {
    value = typeof raw === 'number' ? raw : Number(String(raw).trim());
    if (String(raw).trim() === '' || !Number.isFinite(value)) return 'parameter.type';
    if (definition.type === 'integer' && !Number.isInteger(value)) return 'parameter.type';
    if (definition.minimum !== undefined && value < definition.minimum) return 'parameter.range';
    if (definition.maximum !== undefined && value > definition.maximum) return 'parameter.range';
  } else if (definition.type === 'boolean') value = raw === true || raw === 'true';
  else if (typeof raw !== 'string' || !raw.trim()) return 'parameter.type';
  const entry = { value, provenance: { ...USER } };
  if (definition.type === 'number' || definition.type === 'integer') entry.unit = definition.unit;
  node.parameters[name] = entry;
  return null;
}

// Required inputs without a provider, as [{ node, port }] (edge.required in validation.py).
export function missingInputs(graph, modules) {
  const fed = new Set(graph.edges.map(edge => `${edge.to.node}\u0000${edge.to.port}`));
  const missing = [];
  for (const node of graph.nodes) {
    const manifest = modules.get(key(node));
    if (!manifest) continue;
    for (const [port, definition] of Object.entries(manifest.inputs)) {
      if (!definition.optional && !fed.has(`${node.id}\u0000${port}`)) missing.push({ node: node.id, port });
    }
  }
  return missing;
}
