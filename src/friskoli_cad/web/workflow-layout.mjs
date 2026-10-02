// Layout is editor metadata. It never rewrites edges or their time semantics.
export function arrangeGraph(graph, modules, previous, geometry) {
  const nodes = [...graph.nodes], byId = new Map(nodes.map(n => [n.id, n]));
  const incoming = new Map(nodes.map(n => [n.id, []])), outgoing = new Map(nodes.map(n => [n.id, []]));
  for (const e of graph.edges) {
    if (e.timing !== 'same_step' || !byId.has(e.from.node) || !byId.has(e.to.node)) continue;
    incoming.get(e.to.node).push(e.from.node); outgoing.get(e.from.node).push(e.to.node);
  }
  const stable = (a, b) => `${byId.get(a).owner?.kind}/${byId.get(a).owner?.id}/${a}`.localeCompare(`${byId.get(b).owner?.kind}/${byId.get(b).owner?.id}/${b}`);
  const rank = new Map(nodes.map(n => [n.id, 0])), degree = new Map(nodes.map(n => [n.id, incoming.get(n.id).length]));
  const queue = nodes.filter(n => !degree.get(n.id)).map(n => n.id).sort(stable), visited = new Set();
  while (queue.length) {
    const id = queue.shift(); visited.add(id);
    for (const next of outgoing.get(id)) {
      rank.set(next, Math.max(rank.get(next), rank.get(id) + 1)); degree.set(next, degree.get(next) - 1);
      if (!degree.get(next)) { queue.push(next); queue.sort(stable); }
    }
  }
  // An invalid same-step cycle remains editable and diagnosable; no edge is removed to lay it out.
  for (const n of nodes) if (!visited.has(n.id)) rank.set(n.id, 0);
  const columns = [];
  for (const n of nodes) (columns[rank.get(n.id)] ??= []).push(n.id);
  for (const column of columns) column?.sort(stable);
  for (let sweep = 0; sweep < 6; sweep++) {
    const indices = [...columns.keys()]; if (sweep % 2) indices.reverse();
    const order = new Map(columns.flatMap(col => (col ?? []).map((id, i) => [id, i])));
    for (const c of indices) {
      const neighbors = sweep % 2 ? outgoing : incoming;
      const score = id => { const ns = neighbors.get(id).filter(x => rank.get(x) !== c); return ns.length ? ns.reduce((s, x) => s + order.get(x), 0) / ns.length : order.get(id); };
      columns[c]?.sort((a,b) => score(a) - score(b) || stable(a,b));
      columns[c]?.forEach((id,i) => order.set(id,i));
    }
  }
  const result = {};
  columns.forEach((column, c) => {
    let y = 70;
    for (const id of column ?? []) {
      const n = byId.get(id), manifest = modules.get(`${n.module_id}@${n.module_version}`);
      const collapsed = previous[id]?.collapsed ?? manifest?.declaration?.category === 'data';
      result[id] = {...previous[id], x:70 + c * 322, y, collapsed};
      y += (manifest ? geometry(manifest, collapsed, true).height : 180) + 42;
    }
  });
  return result;
}

// One accessible marker per visible bin; every frame remains reachable with the slider.
export function timelineMarkers(snapshots, current, maximum = 72) {
  if (!snapshots.length) return [];
  const width = Math.max(1, Math.ceil(snapshots.length / Math.max(1, maximum))), markers = [];
  for (let start = 0; start < snapshots.length; start += width) {
    const end = Math.min(snapshots.length, start + width);
    const event = snapshots.slice(start, end).findIndex(s => s.frame.events.length > 0);
    const index = current >= start && current < end ? current : event >= 0 ? start + event : start;
    markers.push({index, start, end:end - 1, events:snapshots.slice(start,end).reduce((sum,s) => sum+s.frame.events.length,0)});
  }
  return markers;
}
