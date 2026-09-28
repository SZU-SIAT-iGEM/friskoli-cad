import { resolveGraph } from './catalog.mjs';

const el = (tag, className = '', value = '') => {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = value;
  return item;
};

export function renderWorkflow(container, graph, modules, selectedId, onSelect) {
  const resolved = resolveGraph(graph, modules);
  container.replaceChildren();
  const board = el('div', 'graph-board');
  const phases = [...new Set(resolved.nodes.map(node => node.manifest.phase))].sort((a, b) => a - b);
  const rows = new Map();
  const locations = new Map();
  for (const node of resolved.nodes) {
    const row = rows.get(node.manifest.phase) ?? 0;
    const x = 92 + phases.indexOf(node.manifest.phase) * 270;
    const y = 90 + row * 160;
    locations.set(node.id, { x, y });
    rows.set(node.manifest.phase, row + 1);
  }
  const width = Math.max(800, 170 + phases.length * 270);
  const height = Math.max(460, 160 + Math.max(0, ...rows.values()) * 160);
  board.style.width = `${width}px`;
  board.style.height = `${height}px`;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'graph-edges');
  svg.setAttribute('width', String(width));
  svg.setAttribute('height', String(height));
  for (const edge of resolved.edges) {
    const from = locations.get(edge.from.node);
    const to = locations.get(edge.to.node);
    if (!from || !to) continue;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    const x1 = from.x + 204, y1 = from.y + 62, x2 = to.x, y2 = to.y + 62;
    path.setAttribute('d', `M${x1},${y1} C${x1 + 65},${y1} ${x2 - 65},${y2} ${x2},${y2}`);
    path.setAttribute('class', edge.timing === 'previous_step' ? 'delayed' : '');
    svg.append(path);
  }
  board.append(svg);
  for (const node of resolved.nodes) {
    const { x, y } = locations.get(node.id);
    const card = el('button', `graph-node${node.id === selectedId ? ' selected' : ''}`);
    card.type = 'button';
    card.style.left = `${x}px`;
    card.style.top = `${y}px`;
    card.append(el('span', 'graph-scope', `${node.manifest.scope} · ${node.manifest.phase}`),
      el('strong', '', node.id), el('span', 'graph-module-name', node.module_id),
      el('span', 'graph-ports', `${Object.keys(node.manifest.inputs).join(', ') || '—'}  →  ${Object.keys(node.manifest.outputs).join(', ') || '—'}`));
    card.addEventListener('click', () => onSelect(node.id));
    board.append(card);
  }
  container.append(board);
  return resolved;
}
