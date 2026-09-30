import { resolveGraph } from './catalog.mjs';
import { currentLanguage } from './i18n.mjs';
import { NODE_WIDTH, element as el, nodeGeometry, nodeStatus, createNodeShell, createNodeHeader,
  createPortRow, createNodeFooter, graphBounds, workflowText } from './workflow-components.mjs';
export { NODE_WIDTH } from './workflow-components.mjs';

const SVG = 'http://www.w3.org/2000/svg';
// Unscaled content inset reserves room for the sticky toolbar, including coarse pointers.
export const GRAPH_TOP_INSET = 56;
const GRID = 14;
const snap = value => Math.max(0, Math.round(value / GRID) * GRID);
const key = node => `${node.module_id}@${node.module_version}`;
const svgPath = (d, className) => {
  const path = document.createElementNS(SVG, 'path');
  path.setAttribute('d', d);
  path.setAttribute('class', className);
  return path;
};

export function nodeHeight(manifest, collapsed = false, coarse = false) {
  return nodeGeometry(manifest, collapsed, coarse).height;
}

// Keeps saved positions and places new nodes in their phase column below existing cards.
export function autoLayout(graph, modules, layout = {}) {
  const next = {};
  const phases = [...new Set(graph.nodes.map(node => modules.get(key(node))?.phase ?? 0))].sort((a, b) => a - b);
  for (const node of graph.nodes) if (layout[node.id]) next[node.id] = { ...layout[node.id] };
  for (const node of graph.nodes) {
    if (next[node.id]) continue;
    const manifest = modules.get(key(node));
    const x = 70 + phases.indexOf(manifest?.phase ?? 0) * 322;
    const collapsed = manifest?.declaration?.category === 'data';
    const height = manifest ? nodeHeight(manifest, collapsed, true) : 180;
    let y = 70;
    const clash = () => Object.entries(next).some(([id, p]) => {
      const other = graph.nodes.find(item => item.id === id);
      const h = modules.get(other ? key(other) : '') ? nodeHeight(modules.get(key(other)), p.collapsed, true) : 180;
      return Math.abs(p.x - x) < NODE_WIDTH + 20 && y < p.y + h + 24 && p.y < y + height + 24;
    });
    while (clash()) y += GRID * 2;
    next[node.id] = { x, y, ...(collapsed ? {collapsed:true} : {}) };
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
    this.coarse = globalThis.matchMedia?.('(pointer: coarse)').matches ?? false;
    if (globalThis.ResizeObserver) new ResizeObserver(() => {
      if (this.board && container.clientWidth && container.clientHeight) this.resizeBoard();
    }).observe(container);
    container.addEventListener('wheel', event => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault(); this.zoomBy(Math.exp(-event.deltaY * .005), [event.clientX,event.clientY]);
    }, {passive:false});
    container.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('.graph-toolbar,button:not(.port-dot),input,summary,.graph-mathematics')) return;
      this.pointers.set(event.pointerId,[event.clientX,event.clientY]);
      if (this.pointers.size > 1) { this.cancelDrag?.(); this.cancelWire?.(); this.panStart = null; }
      if (event.target.closest('.graph-node,.edge-hit')) return;
      container.setPointerCapture(event.pointerId); this.panStart=[event.clientX,event.clientY,container.scrollLeft,container.scrollTop];
    }, true);
    container.addEventListener('pointermove', event => {
      if (!this.pointers.has(event.pointerId)) return;
      const old=[...this.pointers.values()];this.pointers.set(event.pointerId,[event.clientX,event.clientY]);
      if(this.pointers.size===2){const next=[...this.pointers.values()];const distance=p=>Math.hypot(p[0][0]-p[1][0],p[0][1]-p[1][1]);
        if(distance(old)>0)this.zoomBy(distance(next)/distance(old),[(next[0][0]+next[1][0])/2,(next[0][1]+next[1][1])/2]);this.panStart=null;
      }else if(this.panStart){container.scrollLeft=this.panStart[2]-(event.clientX-this.panStart[0]);container.scrollTop=this.panStart[3]-(event.clientY-this.panStart[1]);}
    });
    const end=event=>{
      this.pointers.delete(event.pointerId); this.panStart=null;
      if (this.pointers.size === 1) { const [x,y] = [...this.pointers.values()][0]; this.panStart=[x,y,container.scrollLeft,container.scrollTop]; }
    };
    container.addEventListener('pointerup',end);container.addEventListener('pointercancel',end);
  }

  zoomBy(factor, point = null) {
    if(!this.board)return;
    const rect=this.container.getBoundingClientRect(), x=point?point[0]-rect.left:rect.width/2,y=point?point[1]-rect.top:rect.height/2;
    const local=[(this.container.scrollLeft+x)/this.zoom,(this.container.scrollTop+y-GRAPH_TOP_INSET)/this.zoom];
    this.zoom=Math.max(.25,Math.min(2.5,this.zoom*factor));this.applyZoom();
    this.container.scrollLeft=local[0]*this.zoom-x;this.container.scrollTop=local[1]*this.zoom-y+GRAPH_TOP_INSET;
  }
  applyZoom(){
    if(!this.board)return;
    this.board.style.transform=`scale(${this.zoom})`;
    this.wrapper.style.width=`${this.width*this.zoom}px`; this.wrapper.style.height=`${this.height*this.zoom}px`;
    if (this.zoomLabel) this.zoomLabel.textContent=`${Math.round(this.zoom*100)}%`;
  }
  resizeBoard() {
    if (!this.board) return;
    const bounds = graphBounds(this.resolved.nodes, this.layout, this.coarse);
    this.width = Math.max(this.container.clientWidth / this.zoom, bounds.x + bounds.width + 48);
    this.height = Math.max((this.container.clientHeight-GRAPH_TOP_INSET) / this.zoom, bounds.y + bounds.height + 48);
    this.board.style.width=`${this.width}px`; this.board.style.height=`${this.height}px`;
    this.svg.setAttribute('width', String(this.width)); this.svg.setAttribute('height', String(this.height)); this.applyZoom();
  }
  fit(){
    if(!this.board)return;
    const bounds = graphBounds(this.resolved.nodes, this.layout, this.coarse);
    this.zoom=Math.max(.25,Math.min(1,(this.container.clientWidth-48)/bounds.width,(this.container.clientHeight-88)/bounds.height));
    this.resizeBoard();
    this.container.scrollLeft=Math.max(0,bounds.x*this.zoom-24);
    this.container.scrollTop=Math.max(0,bounds.y*this.zoom);
  }

  portPoint(nodeId, side, name) {
    const node = this.resolved.nodes.find(item => item.id === nodeId);
    const position = this.layout[nodeId];
    if (!node || !position) return null;
    const index = this.layout[nodeId]?.collapsed ? 0 : Object.keys(node.manifest[side]).indexOf(name);
    if (index < 0) return null;
    const geometry = nodeGeometry(node.manifest, position.collapsed, this.coarse);
    return [position.x + (side === 'outputs' ? NODE_WIDTH : 0), position.y + geometry.head + index * geometry.row + geometry.row / 2];
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
    this.missingInputs = missing;
    this.language = currentLanguage();
    this.missing = new Set(missing.map(item => `${item.node}\u0000${item.port}`));
    const scroll = [this.container.scrollLeft, this.container.scrollTop];
    this.container.replaceChildren();
    const board = this.board = el('div', 'graph-board');
    let width = this.container.clientWidth / this.zoom, height = Math.max(0,this.container.clientHeight-GRAPH_TOP_INSET) / this.zoom;
    for (const node of this.resolved.nodes) {
      const { x, y } = layout[node.id];
      width = Math.max(width, x + NODE_WIDTH + 48);
      height = Math.max(height, y + nodeHeight(node.manifest, layout[node.id]?.collapsed, this.coarse) + 48);
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
    this.wrapper.style.marginTop=`${GRAPH_TOP_INSET}px`;
    const toolbar = el('div', 'graph-toolbar'); toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', 'Workflow');
    for (const [label, action] of [['zoomOut', () => this.zoomBy(1/1.2)], ['actual', () => this.zoomBy(1/this.zoom)], ['zoomIn', () => this.zoomBy(1.2)], ['fit', () => this.fit()]]) {
      const button = el('button', '', label === 'zoomOut' ? '−' : label === 'zoomIn' ? '+' : workflowText(this.language, label)); button.type='button';
      button.setAttribute('aria-label', workflowText(this.language, label)); button.title=workflowText(this.language, label);
      button.addEventListener('click', action); if(label==='actual')this.zoomLabel=button; toolbar.append(button);
    }
    this.container.append(toolbar,this.wrapper);this.applyZoom();
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
      const definition = this.resolved.nodes.find(node => node.id === edge.from.node)?.manifest.outputs[edge.from.port];
      const description = `${edge.from.node}.${edge.from.port} → ${edge.to.node}.${edge.to.port} · ${edge.timing}` +
        (definition ? ` · ${definition.quantity} [${definition.unit}] · ${definition.shape}` : '');
      hit.setAttribute('tabindex', '0'); hit.setAttribute('role', 'button');
      hit.setAttribute('aria-label', description);
      hit.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.callbacks.select({kind:'edge',id:edge.id}); }
      });
      hit.addEventListener('pointerdown', event => {
        event.stopPropagation();
        this.callbacks.select({ kind: 'edge', id: edge.id });
      });
      hit.addEventListener('contextmenu', event => {
        event.preventDefault();
        this.callbacks.context({ kind: 'edge', id: edge.id }, event.clientX, event.clientY);
      });
      const title = document.createElementNS(SVG, 'title');
      title.textContent = description;
      hit.append(title);
      this.svg.append(svgPath(d, `${edge.timing === 'previous_step' ? 'delayed' : ''}${selected ? ' selected' : ''}`), hit);
    }
    if (this.wire) this.svg.append(svgPath(curve(this.wire.from, this.wire.to), `wire${this.wire.valid ? ' valid' : ''}`));
  }

  port(node, side, name, definition, index) {
    const {row, dot} = createPortRow(node, side, name, definition, {index,
      geometry:nodeGeometry(node.manifest, this.layout[node.id]?.collapsed, this.coarse),
      missing:side==='inputs' && this.missing.has(`${node.id}\u0000${name}`), language:this.language});
    dot.addEventListener('click', event => {
      event.stopPropagation();
      if (this.skipClick) { this.skipClick = false; return; }
      if (side === 'outputs') { this.armed = {node:node.id,port:name}; this.callbacks.hint?.(); }
      else if (this.armed) { const from = this.armed; this.armed = null; this.callbacks.connect(from,{node:node.id,port:name}); }
    });
    if (side === 'outputs') dot.addEventListener('pointerdown', event => this.startWire(event, node.id, name));
    return row;
  }

  card(node) {
    const position = this.layout[node.id];
    const selected = this.selection?.kind === 'node' && this.selection.id === node.id;
    const geometry = nodeGeometry(node.manifest, position.collapsed, this.coarse);
    const options = {position, geometry, selected, collapsed:position.collapsed, language:this.language};
    const card = createNodeShell(node, options);
    card.append(createNodeHeader(node, {...options, status:nodeStatus(node, this.missingInputs, this.language), onToggle:this.callbacks.toggle}));
    if (!this.layout[node.id]?.collapsed) {
      Object.entries(node.manifest.inputs).forEach(([name, def], i) => card.append(this.port(node, 'inputs', name, def, i)));
      Object.entries(node.manifest.outputs).forEach(([name, def], i) => card.append(this.port(node, 'outputs', name, def, i)));
    }
    card.append(createNodeFooter(node, options));
    card.addEventListener('pointerdown', event => {
      if (event.button !== 0 || this.pointers.size > 1 || event.target.closest('button,input,summary,.graph-identity-panel,.graph-mathematics')) return;
      event.stopPropagation();
      this.startDrag(event, card, node.id);
    });
    card.addEventListener('keydown', event => {
      if (event.target !== card) return;
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
      if (e.pointerId !== event.pointerId) return;
      const dx = (e.clientX - start[0])/this.zoom, dy = (e.clientY - start[1])/this.zoom;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      moved = true;
      card.classList.add('dragging');
      this.layout[id] = { ...origin, x: snap(origin.x + dx), y: snap(origin.y + dy) };
      card.style.left = `${this.layout[id].x}px`;
      card.style.top = `${this.layout[id].y}px`;
      this.resizeBoard(); this.drawEdges();
    };
    const up = e => {
      if (e.pointerId !== undefined && e.pointerId !== event.pointerId) return;
      card.removeEventListener('pointermove', move);
      card.removeEventListener('pointerup', up);
      card.removeEventListener('pointercancel', up);
      card.classList.remove('dragging');
      this.cancelDrag = null;
      if (e.type === 'pointercancel') { this.layout[id] = origin; card.style.left = `${origin.x}px`; card.style.top = `${origin.y}px`; this.drawEdges(); }
      else if (moved) this.callbacks.move(id, this.layout[id], origin);
      else this.callbacks.select({ kind: 'node', id });
    };
    this.cancelDrag = () => up({type:'pointercancel'});
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
    if (event.button !== 0 || this.pointers.size > 1) return;
    event.preventDefault();
    event.stopPropagation();
    const from = { node: nodeId, port };
    const start = this.portPoint(nodeId, 'outputs', port);
    if (!start) return;
    this.wire = { from: start, to: start, valid: false };
    this.board.classList.add('wiring');
    const move = e => {
      if (e.pointerId !== event.pointerId) return;
      const target = this.inputAt(e);
      const point = target ? this.portPoint(target.node, 'inputs', target.port) : null;
      this.wire = { from: start, to: point ?? this.local(e),
        valid: Boolean(target && this.callbacks.canConnect(from, target)) };
      this.drawEdges();
    };
    const up = e => {
      if (e.pointerId !== event.pointerId) return;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      const target = this.inputAt(e);
      this.wire = null;
      this.cancelWire = null;
      this.board.classList.remove('wiring');
      this.drawEdges();
      if (target) { this.skipClick = true; this.armed = null; this.callbacks.connect(from, target); }
    };
    const cancel = e => {
      if (e?.pointerId !== undefined && e.pointerId !== event.pointerId) return;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', cancel);
      this.wire = null;
      this.cancelWire = null;
      this.board.classList.remove('wiring');
      this.drawEdges();
    };
    this.cancelWire = () => cancel();
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
  }
}
