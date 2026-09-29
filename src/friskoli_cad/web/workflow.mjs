import { resolveGraph } from './catalog.mjs';

const SVG = 'http://www.w3.org/2000/svg';
export const NODE_WIDTH = 214;
const HEAD = 66, ROW = 22, GRID = 14;
const snap = value => Math.max(0, Math.round(value / GRID) * GRID);
const key = node => `${node.module_id}@${node.module_version}`;
const el = (tag, className = '', value = '') => {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = value;
  return item;
};
const svgPath = (d, className) => {
  const path = document.createElementNS(SVG, 'path');
  path.setAttribute('d', d);
  path.setAttribute('class', className);
  return path;
};

export function nodeHeight(manifest, collapsed = false) {
  return HEAD + Math.max(1, collapsed ? 1 : Object.keys(manifest.inputs).length,
    collapsed ? 1 : Object.keys(manifest.outputs).length) * ROW + 10;
}

// Keeps saved positions and places new nodes in their phase column below existing cards.
export function autoLayout(graph, modules, layout = {}) {
  const next = {};
  const phases = [...new Set(graph.nodes.map(node => modules.get(key(node))?.phase ?? 0))].sort((a, b) => a - b);
  for (const node of graph.nodes) if (layout[node.id]) next[node.id] = { ...layout[node.id] };
  for (const node of graph.nodes) {
    if (next[node.id]) continue;
    const manifest = modules.get(key(node));
    const x = 70 + phases.indexOf(manifest?.phase ?? 0) * 280;
    const height = manifest ? nodeHeight(manifest) : 120;
    let y = 70;
    const clash = () => Object.entries(next).some(([id, p]) => {
      const other = graph.nodes.find(item => item.id === id);
      const h = modules.get(other ? key(other) : '') ? nodeHeight(modules.get(key(other))) : 120;
      return Math.abs(p.x - x) < NODE_WIDTH + 20 && y < p.y + h + 24 && p.y < y + height + 24;
    });
    while (clash()) y += GRID * 2;
    next[node.id] = { x, y, ...(manifest?.declaration?.category === 'data' ? {collapsed:true} : {}) };
  }
  return next;
}

const curve = ([x1, y1], [x2, y2]) => {
  const bend = Math.max(40, Math.abs(x2 - x1) / 2);
  return `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`;
};

// Node graph editor: drag cards to move, drag from an output dot to an input dot to connect,
// click an edge to select it. All graph changes go through callbacks so the store keeps undo.
export class GraphEditor {
  constructor(container, callbacks) {
    this.container = container;
    this.callbacks = callbacks;
    this.layout = {};
    this.zoom = 1;
    this.pointers = new Map();
    container.addEventListener('wheel', event => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault(); this.zoomBy(Math.exp(-event.deltaY * .005), [event.clientX,event.clientY]);
    }, {passive:false});
    container.addEventListener('pointerdown', event => {
      this.pointers.set(event.pointerId,[event.clientX,event.clientY]);
      if (event.target.closest('.graph-node,.edge-hit')) return;
      container.setPointerCapture(event.pointerId); this.panStart=[event.clientX,event.clientY,container.scrollLeft,container.scrollTop];
    });
    container.addEventListener('pointermove', event => {
      if (!this.pointers.has(event.pointerId)) return;
      const old=[...this.pointers.values()];this.pointers.set(event.pointerId,[event.clientX,event.clientY]);
      if(this.pointers.size===2){const next=[...this.pointers.values()];const distance=p=>Math.hypot(p[0][0]-p[1][0],p[0][1]-p[1][1]);
        if(distance(old)>0)this.zoomBy(distance(next)/distance(old),[(next[0][0]+next[1][0])/2,(next[0][1]+next[1][1])/2]);this.panStart=null;
      }else if(this.panStart){container.scrollLeft=this.panStart[2]-(event.clientX-this.panStart[0]);container.scrollTop=this.panStart[3]-(event.clientY-this.panStart[1]);}
    });
    const end=event=>{this.pointers.delete(event.pointerId);this.panStart=null;};
    container.addEventListener('pointerup',end);container.addEventListener('pointercancel',end);
  }

  zoomBy(factor, point = null) {
    if(!this.board)return;
    const rect=this.container.getBoundingClientRect(), x=point?point[0]-rect.left:rect.width/2,y=point?point[1]-rect.top:rect.height/2;
    const local=[(this.container.scrollLeft+x)/this.zoom,(this.container.scrollTop+y)/this.zoom];
    this.zoom=Math.max(.25,Math.min(2.5,this.zoom*factor));this.applyZoom();
    this.container.scrollLeft=local[0]*this.zoom-x;this.container.scrollTop=local[1]*this.zoom-y;
  }
  applyZoom(){if(!this.board)return;this.board.style.transform=`scale(${this.zoom})`;this.wrapper.style.width=`${this.width*this.zoom}px`;this.wrapper.style.height=`${this.height*this.zoom}px`;}
  fit(){if(!this.board)return;this.zoom=Math.max(.25,Math.min(1,this.container.clientWidth/this.width,this.container.clientHeight/this.height));this.applyZoom();this.container.scrollLeft=0;this.container.scrollTop=0;}

  portPoint(nodeId, side, name) {
    const node = this.resolved.nodes.find(item => item.id === nodeId);
    const position = this.layout[nodeId];
    if (!node || !position) return null;
    const index = this.layout[nodeId]?.collapsed ? 0 : Object.keys(node.manifest[side]).indexOf(name);
    if (index < 0) return null;
    return [position.x + (side === 'outputs' ? NODE_WIDTH : 0), position.y + HEAD + index * ROW + ROW / 2];
  }

  local(event) {
    const rect = this.board.getBoundingClientRect();
    return [(event.clientX - rect.left)/this.zoom, (event.clientY - rect.top)/this.zoom];
  }

  render(graph, modules, layout, selection = null, missing = []) {
    this.graph = graph;
    this.resolved = resolveGraph(graph, modules, {allowUnknown:true});
    this.layout = layout;
    this.selection = selection;
    this.missing = new Set(missing.map(item => `${item.node}\u0000${item.port}`));
    const scroll = [this.container.scrollLeft, this.container.scrollTop];
    this.container.replaceChildren();
    const board = this.board = el('div', 'graph-board');
    let width = Math.max(800, this.container.clientWidth), height = Math.max(460, this.container.clientHeight);
    for (const node of this.resolved.nodes) {
      const { x, y } = layout[node.id];
      width = Math.max(width, x + NODE_WIDTH + 160);
      height = Math.max(height, y + nodeHeight(node.manifest, layout[node.id]?.collapsed) + 160);
    }
    board.style.width = `${width}px`;
    board.style.height = `${height}px`;
    this.svg = document.createElementNS(SVG, 'svg');
    this.svg.setAttribute('class', 'graph-edges');
    this.svg.setAttribute('width', String(width));
    this.svg.setAttribute('height', String(height));
    board.append(this.svg);
    this.drawEdges();
    for (const node of this.resolved.nodes) board.append(this.card(node));
    board.addEventListener('pointerdown', event => {
      if (event.target === board || event.target === this.svg) this.callbacks.select(null);
    });
    this.width=width;this.height=height;this.wrapper=el('div','graph-wrapper');this.wrapper.append(board);
    this.container.append(this.wrapper);this.applyZoom();
    [this.container.scrollLeft, this.container.scrollTop] = scroll;
  }

  drawEdges() {
    this.svg.replaceChildren();
    for (const edge of this.graph.edges) {
      const a = this.portPoint(edge.from.node, 'outputs', edge.from.port);
      const b = this.portPoint(edge.to.node, 'inputs', edge.to.port);
      if (!a || !b) continue;
      const selected = this.selection?.kind === 'edge' && this.selection.id === edge.id;
      const d = curve(a, b);
      const hit = svgPath(d, 'edge-hit');
      hit.addEventListener('pointerdown', event => {
        event.stopPropagation();
        this.callbacks.select({ kind: 'edge', id: edge.id });
      });
      hit.addEventListener('contextmenu', event => {
        event.preventDefault();
        this.callbacks.context({ kind: 'edge', id: edge.id }, event.clientX, event.clientY);
      });
      const title = document.createElementNS(SVG, 'title');
      title.textContent = `${edge.from.node}.${edge.from.port} → ${edge.to.node}.${edge.to.port} · ${edge.timing}`;
      hit.append(title);
      this.svg.append(svgPath(d, `${edge.timing === 'previous_step' ? 'delayed' : ''}${selected ? ' selected' : ''}`), hit);
    }
    if (this.wire) this.svg.append(svgPath(curve(this.wire.from, this.wire.to), `wire${this.wire.valid ? ' valid' : ''}`));
  }

  port(node, side, name, definition, index) {
    const row = el('div', `graph-port ${side === 'inputs' ? 'in' : 'out'}`);
    row.style.top = `${HEAD + index * ROW}px`;
    const dot = el('button', 'port-dot');
    dot.type = 'button';
    dot.disabled = Boolean(node.manifest.unavailable);
    dot.dataset.node = node.id;
    dot.dataset.port = name;
    dot.dataset.side = side;
    const species = definition.species_parameter ? node.parameters[definition.species_parameter]?.value : null;
    const label = `${name} · ${definition.quantity} [${definition.unit}]${species ? ` · ${species}` : ''}`;
    dot.setAttribute('aria-label', `${node.id} ${side === 'inputs' ? 'input' : 'output'} ${label}`);
    dot.title = label;
    dot.addEventListener('click', event => {
      event.stopPropagation();
      if (this.skipClick) { this.skipClick = false; return; }
      if (side === 'outputs') { this.armed = {node:node.id,port:name}; this.callbacks.hint?.(); }
      else if (this.armed) { const from = this.armed; this.armed = null; this.callbacks.connect(from,{node:node.id,port:name}); }
    });
    if (side === 'inputs' && this.missing.has(`${node.id}\u0000${name}`)) row.classList.add('missing');
    if (side === 'outputs') dot.addEventListener('pointerdown', event => this.startWire(event, node.id, name));
    const text = el('span', 'port-name', name);
    row.append(...(side === 'inputs' ? [dot, text] : [text, dot]));
    return row;
  }

  card(node) {
    const { x, y } = this.layout[node.id];
    const selected = this.selection?.kind === 'node' && this.selection.id === node.id;
    const card = el('div', `graph-node${selected ? ' selected' : ''}`);
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `${node.id} · ${node.module_id}`);
    card.dataset.node = node.id;
    card.classList.toggle('unavailable-module', Boolean(node.manifest.unavailable));
    card.style.left = `${x}px`;
    card.style.top = `${y}px`;
    card.style.height = `${nodeHeight(node.manifest, this.layout[node.id]?.collapsed)}px`;
    card.append(el('span', 'graph-scope', `${node.manifest.scope} · ${node.owner.id} · P${node.manifest.phase}`),
      el('strong', '', node.id), el('span', 'graph-module-name', `${node.module_id}@${node.module_version}`));
    if (node.manifest.declaration?.category === 'data') {
      const toggle = el('button', 'data-node-toggle', this.layout[node.id]?.collapsed ? '+' : '−');
      toggle.type = 'button'; toggle.setAttribute('aria-label', 'Expand or collapse data node ' + node.id);
      toggle.addEventListener('pointerdown', event => event.stopPropagation());
      toggle.addEventListener('click', event => { event.stopPropagation(); this.callbacks.toggle?.(node.id); });
      card.append(toggle);
    }
    if (!this.layout[node.id]?.collapsed) {
      Object.entries(node.manifest.inputs).forEach(([name, def], i) => card.append(this.port(node, 'inputs', name, def, i)));
      Object.entries(node.manifest.outputs).forEach(([name, def], i) => card.append(this.port(node, 'outputs', name, def, i)));
    } else card.append(el('span','data-node-summary',Object.keys(node.manifest.outputs).join(' · ') || 'Object initialization'));
    card.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('.port-dot')) return;
      event.stopPropagation();
      this.startDrag(event, card, node.id);
    });
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.callbacks.select({ kind: 'node', id: node.id }); }
    });
    card.addEventListener('contextmenu', event => {
      event.preventDefault();
      this.callbacks.context({ kind: 'node', id: node.id }, event.clientX, event.clientY);
    });
    return card;
  }

  startDrag(event, card, id) {
    const origin = { ...this.layout[id] };
    const start = [event.clientX, event.clientY];
    let moved = false;
    card.setPointerCapture(event.pointerId);
    const move = e => {
      const dx = (e.clientX - start[0])/this.zoom, dy = (e.clientY - start[1])/this.zoom;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true;
      card.classList.add('dragging');
      this.layout[id] = { ...origin, x: snap(origin.x + dx), y: snap(origin.y + dy) };
      card.style.left = `${this.layout[id].x}px`;
      card.style.top = `${this.layout[id].y}px`;
      this.drawEdges();
    };
    const up = e => {
      card.removeEventListener('pointermove', move);
      card.removeEventListener('pointerup', up);
      card.removeEventListener('pointercancel', up);
      card.classList.remove('dragging');
      if (e.type === 'pointercancel') { this.layout[id] = origin; card.style.left = `${origin.x}px`; card.style.top = `${origin.y}px`; this.drawEdges(); }
      else if (moved) this.callbacks.move(id, this.layout[id], origin);
      else this.callbacks.select({ kind: 'node', id });
    };
    card.addEventListener('pointermove', move);
    card.addEventListener('pointerup', up);
    card.addEventListener('pointercancel', up);
  }

  // Finds the input dot under the pointer, ignoring the wire itself.
  inputAt(event) {
    const dot = document.elementFromPoint(event.clientX, event.clientY)?.closest?.('.port-dot');
    return dot?.dataset.side === 'inputs' ? { node: dot.dataset.node, port: dot.dataset.port } : null;
  }

  startWire(event, nodeId, port) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const from = { node: nodeId, port };
    const start = this.portPoint(nodeId, 'outputs', port);
    if (!start) return;
    this.wire = { from: start, to: start, valid: false };
    this.board.classList.add('wiring');
    const move = e => {
      const target = this.inputAt(e);
      const point = target ? this.portPoint(target.node, 'inputs', target.port) : null;
      this.wire = { from: start, to: point ?? this.local(e),
        valid: Boolean(target && this.callbacks.canConnect(from, target)) };
      this.drawEdges();
    };
    const up = e => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      const target = this.inputAt(e);
      this.wire = null;
      this.board.classList.remove('wiring');
      this.drawEdges();
      if (target) { this.skipClick = true; this.armed = null; this.callbacks.connect(from, target); }
    };
    const cancel = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      this.wire = null;
      this.board.classList.remove('wiring');
      this.drawEdges();
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
  }
}
