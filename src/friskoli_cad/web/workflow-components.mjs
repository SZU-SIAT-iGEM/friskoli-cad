import katex from './vendor/katex/katex.mjs';

// Presentation only. Graph mutations and history remain the editor/store's responsibility.
export const NODE_WIDTH = 250;
export const NODE_HEAD = 108;
export const portRowHeight = coarse => coarse ? 44 : 28;
export const element = (tag, className = '', text = '') => {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = text;
  return item;
};
const words = {
  en: { ids:'IDs', instance:'Instance ID', registry:'Registry ID', copy:'Copy', copied:'Copied', manual:'Select and copy',
    unavailable:'Module unavailable · run blocked', missing:'Missing inputs', data:'Data', module:'Module',
    expand:'Expand data node', collapse:'Collapse data node', input:'Input', output:'Output', parameters:'Parameters',
    math:'Mathematics · documentation', source:'LaTeX source', none:'No ports', fit:'Fit graph', zoomIn:'Zoom in', zoomOut:'Zoom out', actual:'Actual size' },
  'zh-CN': { ids:'ID', instance:'实例 ID', registry:'注册 ID', copy:'复制', copied:'已复制', manual:'请选中并复制',
    unavailable:'模块不可用 · 无法运行', missing:'缺少输入连接', data:'数据', module:'模块',
    expand:'展开数据节点', collapse:'折叠数据节点', input:'输入', output:'输出', parameters:'参数',
    math:'数学式 · 文档', source:'LaTeX 源码', none:'无端口', fit:'适合图形', zoomIn:'放大', zoomOut:'缩小', actual:'原始大小' },
};
export const workflowText = (language, key) => (words[language] ?? words.en)[key] ?? key;

export function moduleName(manifest, language = 'en') {
  const raw = manifest.declaration?.label ?? manifest.declaration?.name ?? manifest.declaration?.display_name ?? manifest.name;
  if (raw && typeof raw === 'object') return String(raw[language] ?? raw.en ?? Object.values(raw)[0]);
  if (typeof raw === 'string' && raw.trim() && raw !== manifest.id) {
    const bilingual = raw.split(' / ');
    return bilingual.length === 2 ? bilingual[language === 'zh-CN' ? 0 : 1] : raw;
  }
  const name = String(manifest.id ?? '').split('.').at(-1).replace(/[_-]+/g, ' ');
  return name ? name[0].toUpperCase() + name.slice(1) : workflowText(language, 'module');
}

export function nodeGeometry(manifest, collapsed = false, coarse = false) {
  const row = portRowHeight(coarse);
  const ports = Math.max(1, collapsed ? 1 : Object.keys(manifest.inputs ?? {}).length,
    collapsed ? 1 : Object.keys(manifest.outputs ?? {}).length);
  const parameters = collapsed ? 0 : Math.min(2, Object.keys(manifest.parameters ?? {}).length);
  const math = !collapsed && (manifest.declaration?.mathematics?.equations?.length ?? 0) > 0;
  return { width:NODE_WIDTH, head:NODE_HEAD, row, ports, parameters, math,
    footer:NODE_HEAD + ports * row, height:NODE_HEAD + ports * row + parameters * 26 + (math ? 80 : 0) + 10 };
}

export function nodeStatus(node, missing = [], language = 'en') {
  if (node.manifest.unavailable) return {kind:'error', text:workflowText(language, 'unavailable')};
  const count = missing.filter(item => item.node === node.id).length;
  if (count) return {kind:'error', text:`${workflowText(language, 'missing')} · ${count}`};
  return {kind:'neutral', text:workflowText(language, node.manifest.declaration?.category === 'data' ? 'data' : 'module')};
}

export function createNodeShell(node, {position, geometry, selected, language}) {
  const card = element('div', `graph-node${selected ? ' selected' : ''}`);
  card.tabIndex = 0;
  card.setAttribute('role', 'group');
  card.setAttribute('aria-label', `${moduleName(node.manifest, language)} · ${node.id}`);
  card.dataset.node = node.id;
  card.classList.toggle('unavailable-module', Boolean(node.manifest.unavailable));
  Object.assign(card.style, {left:`${position.x}px`, top:`${position.y}px`, width:`${geometry.width}px`, height:`${geometry.height}px`});
  return card;
}

function identifierRow(label, value, language) {
  const row = element('div', 'graph-identity-row');
  const input = element('input'); input.type = 'text'; input.readOnly = true; input.value = value;
  input.setAttribute('aria-label', label); input.addEventListener('focus', () => input.select());
  const copy = element('button', 'graph-small-button', workflowText(language, 'copy')); copy.type = 'button';
  copy.setAttribute('aria-label', `${workflowText(language, 'copy')} ${label}`);
  copy.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(value); copy.textContent = workflowText(language, 'copied'); }
    catch { input.focus(); input.select(); copy.textContent = workflowText(language, 'manual'); }
  });
  row.append(element('span', '', label), input, copy);
  return row;
}

export function createNodeHeader(node, {collapsed, status, language, onToggle}) {
  const header = element('div', 'graph-node-header');
  const title = element('strong', 'graph-node-title', moduleName(node.manifest, language)); title.title = title.textContent;
  const scope = element('span', 'graph-scope', `${node.manifest.scope} · ${node.owner.id} · P${node.manifest.phase}`); scope.title = scope.textContent;
  const state = element('span', `graph-status ${status.kind}`, status.text); state.title = status.text;
  const ids = element('details', 'graph-identifiers');
  ids.append(element('summary', '', workflowText(language, 'ids')));
  const panel = element('div', 'graph-identity-panel');
  panel.append(identifierRow(workflowText(language, 'instance'), node.id, language),
    identifierRow(workflowText(language, 'registry'), `${node.module_id}@${node.module_version}`, language));
  ids.append(panel);
  ids.addEventListener('toggle', () => ids.closest('.graph-node')?.classList.toggle('identity-open', ids.open));
  header.append(title, scope, state, ids);
  if (node.manifest.declaration?.category === 'data') {
    const toggle = element('button', 'data-node-toggle', collapsed ? '+' : '−'); toggle.type = 'button';
    toggle.setAttribute('aria-label', workflowText(language, collapsed ? 'expand' : 'collapse'));
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.addEventListener('click', event => { event.stopPropagation(); onToggle?.(node.id); });
    header.append(toggle);
  }
  return header;
}

export function createPortRow(node, side, name, definition, {index, geometry, missing, language}) {
  const row = element('div', `graph-port ${side === 'inputs' ? 'in' : 'out'}${missing ? ' missing' : ''}`);
  row.style.top = `${geometry.head + index * geometry.row}px`; row.style.height = `${geometry.row}px`;
  const dot = element('button', 'port-dot'); dot.type = 'button'; dot.disabled = Boolean(node.manifest.unavailable);
  Object.assign(dot.dataset, {node:node.id, port:name, side});
  const species = definition.species_parameter ? node.parameters?.[definition.species_parameter]?.value : null;
  const label = `${name} · ${definition.quantity} [${definition.unit}]${definition.shape ? ` · ${definition.shape}` : ''}${species ? ` · ${species}` : ''}`;
  dot.setAttribute('aria-label', `${node.id} ${workflowText(language, side === 'inputs' ? 'input' : 'output')} ${label}`);
  dot.title = label;
  const text = element('span', 'port-name', name); text.title = label;
  row.append(...(side === 'inputs' ? [dot,text] : [text,dot]));
  return {row, dot};
}

export function createParameterRow(name, parameter, definition) {
  const row = element('div', 'graph-parameter-row');
  const label = element('span', '', name); label.title = name;
  const value = parameter?.value === undefined ? '?' : String(parameter.value);
  const text = element('span', '', `${value}${definition.unit ? ` ${definition.unit}` : ''}`); text.title = text.textContent;
  row.append(label, text); return row;
}

export function createMathematics(math, language) {
  const panel = element('div', 'graph-mathematics'); panel.tabIndex = 0;
  panel.setAttribute('aria-label', workflowText(language, 'math'));
  // The surrounding canvas owns touch gestures, so scroll this inner document explicitly.
  let touch = null, moved = false;
  panel.addEventListener('pointerdown', event => {
    if (event.pointerType !== 'touch' || event.target.closest('summary')) return;
    touch={id:event.pointerId,x:event.clientX,y:event.clientY,left:panel.scrollLeft,top:panel.scrollTop}; moved=false;
    panel.setPointerCapture(event.pointerId);
  });
  panel.addEventListener('pointermove', event => {
    if (touch?.id !== event.pointerId) return;
    const dx=event.clientX-touch.x, dy=event.clientY-touch.y;
    if (Math.hypot(dx,dy)>4) moved=true;
    panel.scrollLeft=touch.left-dx; panel.scrollTop=touch.top-dy;
  });
  const end = event => { if(touch?.id===event.pointerId)touch=null; };
  panel.addEventListener('pointerup',end); panel.addEventListener('pointercancel',end);
  panel.addEventListener('click',event=>{ if(moved){event.preventDefault();event.stopPropagation();moved=false;} });
  for (const equation of math.equations ?? []) {
    const rendered = element('div', 'graph-equation');
    try { katex.render(equation.latex, rendered, {displayMode:true, trust:false, throwOnError:false, strict:'error', maxExpand:1000, maxSize:10}); }
    catch { rendered.textContent = equation.latex; }
    panel.append(rendered);
    const source = element('details', 'graph-equation-source');
    source.append(element('summary', '', workflowText(language, 'source')), element('pre', '', equation.latex)); panel.append(source);
  }
  return panel;
}

export function createNodeFooter(node, {collapsed, geometry, language}) {
  const footer = element('div', 'graph-node-footer'); footer.style.top = `${geometry.footer}px`;
  if (collapsed) {
    footer.style.top = `${geometry.head}px`;
    const summary = element('span', 'data-node-summary', Object.keys(node.manifest.outputs).join(' · ') || workflowText(language, 'none'));
    summary.title = summary.textContent; footer.append(summary); return footer;
  }
  const entries = Object.entries(node.manifest.parameters ?? {});
  for (const [name, definition] of entries.slice(0, 2)) footer.append(createParameterRow(name, node.parameters?.[name], definition));
  if (entries.length > 2) footer.title = `${workflowText(language, 'parameters')}: ${entries.map(([name]) => name).join(', ')}`;
  if (geometry.math) footer.append(createMathematics(node.manifest.declaration.mathematics, language));
  return footer;
}

export function graphBounds(nodes, layout, coarse = false) {
  if (!nodes.length) return {x:0, y:0, width:NODE_WIDTH, height:180};
  const left = Math.min(...nodes.map(node => layout[node.id].x));
  const top = Math.min(...nodes.map(node => layout[node.id].y));
  const right = Math.max(...nodes.map(node => layout[node.id].x + NODE_WIDTH));
  const bottom = Math.max(...nodes.map(node => layout[node.id].y + nodeGeometry(node.manifest, layout[node.id].collapsed, coarse).height));
  return {x:left, y:top, width:right-left, height:bottom-top};
}
