export function registerModules(payload) {
  if (payload?.protocol_version !== '0.1.0' || !Array.isArray(payload.modules)) {
    throw new Error('Invalid module catalog');
  }
  const modules = new Map();
  for (const manifest of payload.modules) {
    if (!manifest?.id || !manifest?.version || !manifest?.scope || !manifest?.inputs ||
        !manifest?.outputs || !manifest?.parameters) throw new Error('Invalid module manifest');
    const key = `${manifest.id}@${manifest.version}`;
    if (modules.has(key)) throw new Error(`Duplicate module ${key}`);
    modules.set(key, manifest);
  }
  return modules;
}

export function resolveGraph(graph, modules, {allowUnknown = false} = {}) {
  if (graph?.protocol_version !== '0.1.0' || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    throw new Error('Invalid behavior graph');
  }
  const nodes = graph.nodes.map(node => {
    const manifest = allowUnknown ? readableManifest(node, graph, modules) : modules.get(`${node.module_id}@${node.module_version}`);
    if (!manifest) throw new Error(`Module unavailable: ${node.module_id}@${node.module_version}`);
    return { ...node, manifest };
  });
  return { nodes, edges: graph.edges };
}

export function nextHit(ids, previousId) {
  if (!ids.length) return null;
  const position = ids.indexOf(previousId);
  return ids[(position + 1) % ids.length];
}

// Registry versioning does not change the executable module manifest version.
export function registerCatalog(payload) {
  const known = (payload?.catalog_version === '0.1.0' && payload.execution_semantics === 'legacy-explicit-v1') ||
    (payload?.catalog_version === '0.2.0' && payload.execution_semantics === 'conservative-pts-bulk-v1');
  if (!known ||
      !Array.isArray(payload.entries) || !Array.isArray(payload.objects)) throw new Error('Unsupported registry catalog');
  const modules = registerModules({protocol_version:'0.1.0', modules:payload.modules});
  const seen = new Set();
  for (const entry of payload.entries) {
    if (!modules.has(entry.key) || seen.has(entry.key)) throw new Error('Invalid registry reference');
    seen.add(entry.key);
    modules.set(entry.key, {...modules.get(entry.key), declaration: structuredClone(entry)});
  }
  if (seen.size !== modules.size) throw new Error('Incomplete registry catalog');
  const objects = new Map();
  for (const object of payload.objects) {
    if (!object.id || objects.has(object.id) || !Array.isArray(object.properties) ||
        !Array.isArray(object.initializer?.data_modules)) throw new Error('Invalid object declaration');
    for (const key of [object.initializer.module,...object.initializer.data_modules]) {
      if (!modules.has(key)) throw new Error('Missing object initializer module');
    }
    objects.set(object.id, structuredClone(object));
  }
  return {modules, objects};
}

export function unavailableModules(graph, modules) {
  return graph.nodes.filter(node => !modules.has(`${node.module_id}@${node.module_version}`));
}

export function readableManifest(node, graph, modules) {
  const installed = modules.get(`${node.module_id}@${node.module_version}`);
  if (installed) return installed;
  const ports = side => Object.fromEntries(graph.edges.filter(edge => edge[side].node === node.id)
    .map(edge => [edge[side].port, {shape:'unknown',quantity:'unknown',unit:'?'}]));
  return {id:node.module_id,version:node.module_version,scope:node.owner.kind,phase:0,
    unavailable:true,description:'Module unavailable. Original node, parameters and edges are preserved. Running requires this exact module version.',
    inputs:ports('to'),outputs:ports('from'),parameters:{},state:{},initial_outputs:[]};
}
