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

export function resolveGraph(graph, modules) {
  if (graph?.protocol_version !== '0.1.0' || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    throw new Error('Invalid behavior graph');
  }
  const nodes = graph.nodes.map(node => {
    const manifest = modules.get(`${node.module_id}@${node.module_version}`);
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
