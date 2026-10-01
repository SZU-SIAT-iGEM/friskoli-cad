// Copy actual executable nodes and timed edges, preserving shared environment providers.
export function copyPopulationBranch(project, sourceId, targetId) {
  if (!project.groups[sourceId] || !project.groups[targetId] || sourceId === targetId) throw new Error('Invalid population branch');
  const source = project.graph.nodes.filter(n => n.owner.kind === 'population' && n.owner.id === sourceId);
  if (!source.length) throw new Error('No population branch to copy');
  const oldTarget = new Set(project.graph.nodes.filter(n => n.owner.kind === 'population' && n.owner.id === targetId).map(n=>n.id));
  const nodes = project.graph.nodes.filter(n=>!oldTarget.has(n.id));
  const edges = project.graph.edges.filter(e=>!oldTarget.has(e.from.node) && !oldTarget.has(e.to.node));
  const ids = new Set(nodes.map(n=>n.id)), mapping = new Map();
  for (const node of source) {
    let id = `${targetId}_${node.id}`, suffix=1;
    while (ids.has(id)) id = `${targetId}_${node.id}_${suffix++}`;
    ids.add(id); mapping.set(node.id,id);
    nodes.push({...structuredClone(node),id,owner:{kind:'population',id:targetId}});
  }
  const edgeIds = new Set(edges.map(e=>e.id));
  for (const edge of project.graph.edges) {
    if (!mapping.has(edge.to.node)) continue;
    let id = `${targetId}_${edge.id}`, suffix=1;
    while(edgeIds.has(id)) id = `${targetId}_${edge.id}_${suffix++}`;
    edgeIds.add(id);
    edges.push({...structuredClone(edge),id,from:{...edge.from,node:mapping.get(edge.from.node)??edge.from.node},to:{...edge.to,node:mapping.get(edge.to.node)}});
  }
  const channels = Object.fromEntries(Object.entries(project.run.channels).filter(([,c])=>!oldTarget.has(c.node)));
  for (const [id,channel] of Object.entries(project.run.channels)) if (mapping.has(channel.node)) {
    let name = `${targetId}_${id}`, suffix=1;
    while(Object.hasOwn(channels,name)) name = `${targetId}_${id}_${suffix++}`;
    channels[name] = {...structuredClone(channel),node:mapping.get(channel.node),group_id:targetId};
  }
  project.graph.nodes = nodes; project.graph.edges = edges; project.run.channels = channels;
  return [...mapping.values()];
}
