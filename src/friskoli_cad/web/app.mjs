import { normalizeReplay, cellHistory } from './replay.mjs';
import { registerModules, resolveGraph } from './catalog.mjs';
import { blocksFromProject, createBlock, scatterBlock } from './population.mjs';
import { SpatialViewport } from './scene3d.mjs';
import { renderWorkflow } from './workflow.mjs';
import { applyLanguage, currentLanguage, setLanguage, t } from './i18n.mjs';

const $ = id => document.getElementById(id);
const el = (tag, className = '', value = '') => {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = value;
  return item;
};
const fmt = (value, digits = 2) => Number.isFinite(value) ? Number(value.toFixed(digits)).toString() : '—';
const state = { project: null, blocks: [], modules: new Map(), replay: null, view: 'space', left: 'objects',
  frameIndex: 0, selectedBlock: null, selectedCell: null, selectedNode: null, selectedManifest: null,
  field: '', slice: 0, ranges: {}, timer: null, status: ['ready', {}, false] };
let viewport;

applyLanguage();
$('language-select').value = currentLanguage();

function status(key, values = {}, error = false) {
  state.status = [key, values, error];
  $('status-message').textContent = t(key, values);
  document.querySelector('.status-dot').classList.toggle('error', error);
}

function kv(parent, label, value) {
  const row = el('div', 'property-row');
  row.append(el('span', 'property-key', label), el('span', 'property-value', value));
  parent.append(row);
}

function section(parent, title) {
  const part = el('section', 'property-section');
  part.append(el('h3', '', title));
  parent.append(part);
  return part;
}

function stop() {
  if (state.timer !== null) clearInterval(state.timer);
  state.timer = null;
  $('play-button').textContent = '▶';
  $('play-button').setAttribute('aria-label', 'Play');
}

function updateRanges() {
  state.ranges = {};
  for (const snapshot of state.replay.snapshots) for (const [id, field] of Object.entries(snapshot.concentrations)) {
    const range = state.ranges[id] ?? { min: Infinity, max: -Infinity };
    for (const layer of field.values_zyx) for (const row of layer) for (const value of row) {
      range.min = Math.min(range.min, value);
      range.max = Math.max(range.max, value);
    }
    state.ranges[id] = range;
  }
}

function renderFieldControls() {
  const fields = Object.keys(state.replay?.snapshots[0]?.concentrations ?? {});
  if (!fields.includes(state.field)) state.field = '';
  $('field-select').replaceChildren(new Option(t('noField'), ''), ...fields.map(id => new Option(id, id)));
  $('field-select').value = state.field;
  $('field-select').hidden = state.view !== 'results' || fields.length === 0;
  const nz = state.project?.domain.counts_xyz[2] ?? 1;
  $('slice-select').replaceChildren(...Array.from({ length: nz }, (_, index) =>
    new Option(`Z ${index + 1} / ${nz}`, String(index))));
  state.slice = Math.min(state.slice, nz - 1);
  $('slice-select').value = String(state.slice);
  $('slice-select').hidden = state.view !== 'results' || !state.field || nz === 1;
}

function currentSnapshot() {
  if (!state.replay) return null;
  return state.replay.snapshots[state.view === 'results' ? state.frameIndex : 0];
}

function renderScene() {
  if (!state.project || !state.replay) return;
  renderFieldControls();
  const snapshot = currentSnapshot();
  viewport?.setDomain(state.project.domain);
  viewport?.setMode(state.view);
  if (state.view !== 'workflow') {
    viewport?.setSnapshot(snapshot, state.view === 'results' ? state.selectedCell : null,
      state.view === 'results' ? state.field : '', state.slice, state.ranges[state.field]);
    viewport?.setBlocks(state.blocks, state.selectedBlock);
  }
  const [nx, ny, nz] = state.project.domain.counts_xyz;
  const [dx, dy, dz] = state.project.domain.spacing_um_xyz;
  $('view-readout').textContent = `${fmt(nx * dx)} × ${fmt(ny * dy)} × ${fmt(nz * dz)} µm`;
  $('document-meta').textContent = `${state.project.domain.geometry === 'volume' ? '3D' : 'THIN LAYER'}  ·  ${nx} × ${ny} × ${nz}`;
  $('status-count').textContent = `${snapshot.frame.cells.length} ${t('cells')} · ${state.blocks.length} ${t('populations')}`;
  $('status-version').textContent = `FRAME ${snapshot.frame.frame_version ?? '0.1.0'}`;
}

function renderTimeline() {
  const replay = state.replay;
  if (!replay) return;
  $('frame-range').max = String(replay.snapshots.length - 1);
  $('frame-range').value = String(state.frameIndex);
  const frame = replay.snapshots[state.frameIndex].frame;
  $('time-label').textContent = `${fmt(frame.time_s, 3)} s`;
  $('frame-label').textContent = `${t('frame')} ${state.frameIndex} / ${replay.snapshots.length - 1}`;
  $('previous-button').disabled = state.frameIndex === 0;
  $('next-button').disabled = state.frameIndex === replay.snapshots.length - 1;
  const track = $('event-track');
  track.replaceChildren();
  for (const snapshot of replay.snapshots) {
    const tick = el('button', `event-tick${snapshot.frame.events.length ? ' has-event' : ''}${snapshot.frame.frame_index === state.frameIndex ? ' current' : ''}`,
      snapshot.frame.events.length ? '◆' : '·');
    tick.type = 'button';
    tick.title = `${fmt(snapshot.frame.time_s, 3)} s · ${snapshot.frame.events.length} events`;
    tick.addEventListener('click', () => setFrame(snapshot.frame.frame_index));
    track.append(tick);
  }
}

function closeDocks() {
  $('studio').classList.remove('show-left', 'show-right');
  $('dock-backdrop').hidden = true;
  viewport?.resize();
}

function selectBlock(id) {
  state.selectedBlock = id;
  state.selectedCell = null;
  renderLeft();
  renderInspector();
  viewport?.setBlocks(state.blocks, id);
  closeDocks();
}

function selectCell(id) {
  state.selectedCell = id;
  renderLeft();
  renderInspector();
  renderScene();
  closeDocks();
}

function treeRow(parent, label, meta, active, action, icon = '◇') {
  const button = el('button', `tree-row${active ? ' active' : ''}`);
  button.type = 'button';
  button.append(el('span', 'tree-icon', icon), el('span', 'tree-name', label), el('span', 'tree-meta', meta));
  button.addEventListener('click', action);
  parent.append(button);
  return button;
}

function renderLeft() {
  const container = $('left-content');
  container.replaceChildren();
  if (!state.project) return;
  const filter = $('left-search').value.trim().toLowerCase();
  if (state.left === 'modules') {
    container.append(el('div', 'tree-heading', `${t('compiled')} · ${state.modules.size}`));
    for (const [key, manifest] of state.modules) {
      if (filter && !`${key} ${manifest.description}`.toLowerCase().includes(filter)) continue;
      treeRow(container, manifest.id, manifest.version, state.selectedManifest === key,
        () => { state.selectedManifest = key; renderLeft(); renderInspector(); closeDocks(); }, '⬡');
    }
    return;
  }
  if (state.view === 'workflow') {
    container.append(el('div', 'tree-heading', `${t('workflow')} · ${state.project.graph.nodes.length}`));
    for (const node of state.project.graph.nodes) {
      if (filter && !`${node.id} ${node.module_id}`.toLowerCase().includes(filter)) continue;
      treeRow(container, node.id, node.module_id.split('.').at(-1), state.selectedNode === node.id,
        () => { state.selectedNode = node.id; renderAll(); closeDocks(); }, '⬡');
    }
    return;
  }
  container.append(el('div', 'tree-heading', `${t('domain')} · ${state.blocks.length}`));
  for (const block of state.blocks) {
    if (filter && !`${block.name} ${block.id}`.toLowerCase().includes(filter) && state.view !== 'results') continue;
    const shownCount = state.view === 'results' ? currentSnapshot().frame.cells.filter(cell => cell.group_id === block.id).length : block.count;
    const row = treeRow(container, block.name, `${shownCount}${block.dirty ? ' *' : ''}`,
      state.view === 'space' && state.selectedBlock === block.id,
      () => { setView('space'); selectBlock(block.id); }, '◌');
    row.addEventListener('contextmenu', event => { event.preventDefault(); showContext(block.id, event.clientX, event.clientY); });
    if (state.view === 'results') {
      const cells = currentSnapshot().frame.cells.filter(cell => cell.group_id === block.id &&
        (!filter || `${cell.id} ${block.name}`.toLowerCase().includes(filter)));
      const shown = filter ? cells : cells.slice(0, 60);
      for (const cell of shown) treeRow(container, cell.id, '', state.selectedCell === cell.id,
        () => selectCell(cell.id), '·').classList.add('cell-row');
      if (cells.length > shown.length) container.append(el('div', 'tree-more', `+ ${cells.length - shown.length}`));
    }
  }
}

function numberField(parent, label, block, key, axis = null) {
  const row = el('label', 'edit-row');
  const input = el('input');
  input.type = 'number';
  input.step = key === 'count' || key === 'seed' ? '1' : '.1';
  input.value = axis === null ? block[key] : block[key][axis];
  input.setAttribute('aria-label', label);
  row.append(el('span', '', label), input);
  const update = () => {
    if (input.value === '') return;
    const value = Number(input.value);
    if (!Number.isFinite(value) || ((key === 'count' || key === 'seed') && !Number.isInteger(value))) {
      input.setCustomValidity('Invalid number'); input.reportValidity(); return;
    }
    input.setCustomValidity('');
    if (axis === null) block[key] = value; else block[key][axis] = value;
    block.dirty = true;
    status('blockUpdated');
    renderLeft();
    renderScene();
    updateRunButton();
  };
  input.addEventListener('input', update);
  input.addEventListener('change', update);
  parent.append(row);
}

function renderBlockInspector(root, block) {
  root.append(el('div', 'inspector-title', block.name), el('div', 'inspector-subtitle', `${t('population')} · ${block.id}`));
  const count = section(root, t('population'));
  const nameRow = el('label', 'edit-row');
  const nameInput = el('input');
  nameInput.type = 'text';
  nameInput.value = block.name;
  nameInput.setAttribute('aria-label', t('name'));
  nameRow.append(el('span', '', t('name')), nameInput);
  nameInput.addEventListener('change', () => {
    const value = nameInput.value.trim();
    if (!value) { nameInput.value = block.name; return; }
    block.name = value;
    root.querySelector('.inspector-title').textContent = value;
    renderLeft();
  });
  count.append(nameRow);
  numberField(count, t('count'), block, 'count');
  numberField(count, t('length') + ' µm', block, 'length');
  numberField(count, t('diameter') + ' µm', block, 'diameter');
  numberField(count, t('seed'), block, 'seed');
  const position = section(root, `${t('center')} · µm`);
  ['X', 'Y', 'Z'].forEach((axis, index) => numberField(position, axis, block, 'center', index));
  const volume = section(root, `${t('size')} · µm`);
  ['X', 'Y', 'Z'].forEach((axis, index) => numberField(volume, axis, block, 'size', index));
  const action = el('button', 'inspector-action', t('scatter'));
  action.type = 'button';
  action.addEventListener('click', () => scatter(block.id));
  root.append(action);
  if (block.dirty) root.append(el('div', 'pending-note', t('pending')));
}

function renderCellInspector(root, cell) {
  root.append(el('div', 'inspector-title', state.selectedCell), el('div', 'inspector-subtitle', t('cells')));
  if (!cell) { root.append(el('p', 'empty-message', t('noCell'))); return; }
  const location = section(root, t('geometry'));
  kv(location, t('group'), cell.group_id);
  kv(location, t('position'), cell.position_um.map(value => fmt(value, 3)).join(' / ') + ' µm');
  kv(location, t('orientation'), cell.orientation_xyzw.map(value => fmt(value, 4)).join(' / '));
  if (cell.geometry) {
    kv(location, t('length'), `${fmt(cell.geometry.length_um, 3)} µm`);
    kv(location, t('diameter'), `${fmt(cell.geometry.diameter_um, 3)} µm`);
  } else kv(location, t('geometry'), t('unknown'));
  const channels = Object.entries(cell.channels);
  if (channels.length) {
    const part = section(root, t('channels'));
    for (const [id, value] of channels) kv(part, id, `${fmt(value, 4)} ${state.replay.run.channels[id]?.unit ?? ''}`);
  }
  root.append(el('div', 'inspector-foot', t('history', { count: cellHistory(state.replay, cell.id).length })));
}

function renderManifest(root, manifest, node = null) {
  root.append(el('div', 'inspector-title', node?.id ?? manifest.id),
    el('div', 'inspector-subtitle', `${manifest.id}@${manifest.version}`));
  const identity = section(root, t('module'));
  kv(identity, t('scope'), manifest.scope);
  kv(identity, t('phase'), String(manifest.phase));
  kv(identity, t('description'), manifest.description ?? '—');
  if (node) {
    const params = section(root, t('parameters'));
    for (const [key, value] of Object.entries(node.parameters)) kv(params, key, `${value.value} ${value.unit ?? ''}`);
  } else {
    const params = section(root, t('parameters'));
    for (const [key, definition] of Object.entries(manifest.parameters)) kv(params, key, definition.unit ?? definition.type);
  }
  for (const side of ['inputs', 'outputs']) {
    const part = section(root, t(side));
    for (const [name, port] of Object.entries(manifest[side])) kv(part, name,
      `${port.shape} · ${port.quantity} [${port.unit}]`);
  }
}

function renderInspector() {
  const root = $('inspector');
  root.replaceChildren();
  if (!state.project || !state.replay) return;
  if (state.left === 'modules' && state.selectedManifest) {
    renderManifest(root, state.modules.get(state.selectedManifest)); return;
  }
  if (state.view === 'space') {
    const block = state.blocks.find(item => item.id === state.selectedBlock);
    if (block) { renderBlockInspector(root, block); return; }
    root.append(el('div', 'inspector-title', t('domain')));
    const part = section(root, t('geometry'));
    kv(part, t('grid'), state.project.domain.counts_xyz.join(' × '));
    kv(part, t('size'), state.project.domain.spacing_um_xyz.join(' × ') + ' µm');
    kv(part, t('geometry'), state.project.domain.geometry);
  } else if (state.view === 'workflow') {
    const node = state.project.graph.nodes.find(item => item.id === state.selectedNode);
    if (node) renderManifest(root, state.modules.get(`${node.module_id}@${node.module_version}`), node);
    else root.append(el('p', 'empty-message', t('noSelection')));
  } else {
    const frame = currentSnapshot().frame;
    if (state.selectedCell) renderCellInspector(root, frame.cells.find(cell => cell.id === state.selectedCell));
    else {
      root.append(el('div', 'inspector-title', `${t('frame')} ${frame.frame_index}`));
      const part = section(root, t('localRun'));
      kv(part, t('time'), `${fmt(frame.time_s, 3)} s`);
      kv(part, t('cells'), String(frame.cells.length));
      kv(part, t('events'), String(frame.events.length));
      const events = section(root, t('events'));
      if (!frame.events.length) events.append(el('p', 'empty-message', t('noEvents')));
      for (const event of frame.events) {
        const button = el('button', 'event-row', `${t(event.type)} · ${event.parent_id ?? event.cell_id} → ${event.child_id ?? ''}`);
        button.type = 'button';
        button.addEventListener('click', () => selectCell(event.child_id ?? event.cell_id));
        events.append(button);
      }
    }
  }
}

function renderGraph() {
  if (!state.project || state.view !== 'workflow') return;
  renderWorkflow($('workflow-view'), state.project.graph, state.modules, state.selectedNode,
    id => { state.selectedNode = id; renderLeft(); renderGraph(); renderInspector(); });
}

function updateRunButton() {
  const pending = state.blocks.some(block => block.dirty);
  $('run-button').disabled = pending || !state.project;
  $('run-button').title = pending ? t('pending') : '';
}

function renderAll() {
  if (!state.project || !state.replay) return;
  $('project-title').textContent = state.project.id;
  $('document-label').textContent = t(state.view === 'workflow' ? 'workflow' : state.view === 'results' ? 'results' : 'space');
  for (const button of document.querySelectorAll('[data-view]')) button.classList.toggle('active', button.dataset.view === state.view);
  for (const button of document.querySelectorAll('[data-left]')) button.classList.toggle('active', button.dataset.left === state.left);
  $('spatial-canvas').hidden = state.view === 'workflow';
  $('scene-annotations').hidden = state.view === 'workflow';
  $('view-controls').hidden = state.view === 'workflow';
  $('workflow-view').hidden = state.view !== 'workflow';
  $('bottom-dock').hidden = state.view !== 'results';
  $('population-tool').disabled = state.view !== 'space';
  renderScene();
  renderTimeline();
  renderLeft();
  renderGraph();
  renderInspector();
  updateRunButton();
  viewport?.resize();
}

function setView(view) {
  stop();
  state.view = view;
  if (view !== 'space') { viewport?.setTool('select'); $('select-tool').classList.add('active'); $('population-tool').classList.remove('active'); }
  renderAll();
}

function setFrame(index) {
  if (!state.replay) return;
  state.frameIndex = Math.max(0, Math.min(state.replay.snapshots.length - 1, index));
  renderTimeline();
  renderScene();
  renderLeft();
  renderInspector();
}

async function executeProject(candidate, blocks = state.blocks) {
  const dt = Number($('dt-input').value), steps = Number($('steps-input').value);
  if (!Number.isFinite(dt) || dt <= 0 || !Number.isInteger(steps) || steps < 1 || steps > 100) {
    status('invalidControls', {}, true); return false;
  }
  stop();
  $('run-button').disabled = true;
  status('running');
  try {
    resolveGraph(candidate.graph, state.modules);
    const response = await fetch('/api/replay', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: candidate, dt_s: dt, steps }) });
    const result = await response.json();
    if (!response.ok) throw new Error(`${result.error?.code ?? 'request.invalid'}: ${result.error?.message ?? ''}`);
    state.replay = normalizeReplay(result);
    state.project = candidate;
    state.blocks = blocks;
    state.frameIndex = Math.max(0, state.replay.snapshots.findIndex(item => item.frame.events.length));
    updateRanges();
    viewport?.setDomain(candidate.domain);
    renderAll();
    status('runReady', { count: state.replay.snapshots.length,
      cells: currentSnapshot().frame.cells.length });
    return true;
  } catch (error) {
    status('runFailed', { message: error.message }, true);
    return false;
  } finally { updateRunButton(); }
}

async function scatter(id) {
  const candidate = structuredClone(state.project);
  const blocks = structuredClone(state.blocks);
  const block = blocks.find(item => item.id === id);
  if (!block) return;
  try {
    scatterBlock(candidate, block, state.modules.get('population.static@1.0.0'));
    if (await executeProject(candidate, blocks)) {
      state.selectedBlock = id;
      setView('space');
      status('scattered', { count: block.count, id });
    }
  } catch (error) { status('runFailed', { message: error.message }, true); }
}

function showContext(id, x, y) {
  const menu = $('context-menu');
  menu.replaceChildren();
  const scatterButton = el('button', '', t('scatter'));
  scatterButton.type = 'button';
  scatterButton.addEventListener('click', () => { menu.hidden = true; scatter(id); });
  menu.append(scatterButton);
  menu.style.left = `${Math.min(x, innerWidth - 190)}px`;
  menu.style.top = `${Math.min(y, innerHeight - 75)}px`;
  menu.hidden = false;
  state.selectedBlock = id;
  renderInspector();
  viewport?.setBlocks(state.blocks, id);
}

function saveWorkspace() {
  if (!state.project) return;
  const document = { workspace_format_version: '0.1.0', project: state.project, population_blocks: state.blocks };
  const blob = new Blob([JSON.stringify(document, null, 2) + '\n'], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = el('a');
  link.href = url;
  link.download = `${state.project.id}.friskoli-workspace.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  status('saved');
}

async function loadProject(document) {
  let project, blocks;
  if (document?.workspace_format_version === '0.1.0') {
    project = document.project;
    blocks = document.population_blocks;
    if (!Array.isArray(blocks)) throw new Error('Workspace population blocks are missing');
  } else { project = document; blocks = blocksFromProject(project); }
  state.selectedBlock = blocks[0]?.id ?? null;
  state.selectedCell = null;
  state.selectedNode = project.graph.nodes[0]?.id ?? null;
  state.selectedManifest = null;
  if (await executeProject(project, blocks)) { setView('space'); status('opened'); }
}

try {
  viewport = new SpatialViewport($('spatial-canvas'), $('scene-annotations'), {
    selectCell, selectBlock,
    placePopulation(point) {
      const block = createBlock(state.project, point, state.blocks.map(item => item.id));
      state.blocks.push(block);
      state.selectedBlock = block.id;
      viewport.setTool('select');
      $('population-tool').classList.remove('active');
      $('select-tool').classList.add('active');
      renderAll();
      status('blockPlaced');
    },
    contextBlock: showContext,
  });
} catch (error) {
  $('viewport-error').hidden = false;
  $('viewport-error').textContent = t('viewError', { message: error.message });
}

for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => setView(button.dataset.view));
for (const button of document.querySelectorAll('[data-left]')) button.addEventListener('click', () => {
  state.left = button.dataset.left;
  renderLeft(); renderInspector();
});
$('left-search').addEventListener('input', renderLeft);
$('open-button').addEventListener('click', () => $('project-file').click());
$('save-button').addEventListener('click', saveWorkspace);
$('project-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try { await loadProject(JSON.parse(await file.text())); }
  catch (error) { status('loadFailed', { message: error.message }, true); }
  event.target.value = '';
});
$('run-button').addEventListener('click', () => {
  if (state.blocks.some(block => block.dirty)) { status('pending', {}, true); return; }
  executeProject(state.project);
});
$('settings-button').addEventListener('click', () => { $('settings-overlay').hidden = false; $('language-select').focus(); });
$('settings-close').addEventListener('click', () => { $('settings-overlay').hidden = true; $('settings-button').focus(); });
$('settings-overlay').addEventListener('click', event => { if (event.target === $('settings-overlay')) $('settings-close').click(); });
$('language-select').addEventListener('change', event => {
  setLanguage(event.target.value);
  renderAll();
  status(...state.status);
});
$('camera-select').addEventListener('change', event => viewport?.setCamera(event.target.value));
$('field-select').addEventListener('change', event => { state.field = event.target.value; renderScene(); });
$('slice-select').addEventListener('change', event => { state.slice = Number(event.target.value); renderScene(); });
$('fit-button').addEventListener('click', () => viewport?.fit());
$('fit-tool').addEventListener('click', () => viewport?.fit());
$('zoom-in').addEventListener('click', () => viewport?.zoom(1.3));
$('zoom-out').addEventListener('click', () => viewport?.zoom(1 / 1.3));
for (const [id, view] of [['top-tool', 'top'], ['front-tool', 'front'], ['right-tool', 'right']]) {
  $(id).addEventListener('click', () => { $('camera-select').value = view; viewport?.setCamera(view); });
}
$('select-tool').addEventListener('click', () => {
  viewport?.setTool('select'); $('select-tool').classList.add('active'); $('population-tool').classList.remove('active');
});
$('population-tool').addEventListener('click', () => {
  setView('space'); viewport?.setTool('population'); $('population-tool').classList.add('active'); $('select-tool').classList.remove('active');
});
$('frame-range').addEventListener('input', event => { stop(); setFrame(Number(event.target.value)); });
$('previous-button').addEventListener('click', () => { stop(); setFrame(state.frameIndex - 1); });
$('next-button').addEventListener('click', () => { stop(); setFrame(state.frameIndex + 1); });
$('play-button').addEventListener('click', () => {
  if (!state.replay) return;
  if (state.timer !== null) { stop(); return; }
  if (state.frameIndex >= state.replay.snapshots.length - 1) setFrame(0);
  $('play-button').textContent = 'Ⅱ';
  $('play-button').setAttribute('aria-label', 'Pause');
  state.timer = setInterval(() => {
    if (state.frameIndex >= state.replay.snapshots.length - 1) { stop(); return; }
    setFrame(state.frameIndex + 1);
  }, 650);
});
for (const [button, dock] of [['objects-tool', 'left'], ['properties-tool', 'right']]) {
  $(button).addEventListener('click', () => {
    const className = `show-${dock}`;
    const opened = $('studio').classList.contains(className);
    closeDocks();
    if (!opened) { $('studio').classList.add(className); $('dock-backdrop').hidden = false; }
  });
}
$('close-left').addEventListener('click', closeDocks);
$('close-right').addEventListener('click', closeDocks);
$('dock-backdrop').addEventListener('click', closeDocks);
document.addEventListener('click', event => { if (!event.target.closest('#context-menu')) $('context-menu').hidden = true; });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { $('context-menu').hidden = true; $('settings-overlay').hidden = true; closeDocks(); }
  if (['INPUT', 'SELECT'].includes(document.activeElement?.tagName) || state.view !== 'results') return;
  if (event.key === 'ArrowLeft') { stop(); setFrame(state.frameIndex - 1); }
  if (event.key === 'ArrowRight') { stop(); setFrame(state.frameIndex + 1); }
});

try {
  const [catalogResponse, projectResponse] = await Promise.all([fetch('/api/modules'), fetch('/api/example-project')]);
  if (!catalogResponse.ok || !projectResponse.ok) throw new Error('Local kernel unavailable');
  state.modules = registerModules(await catalogResponse.json());
  await loadProject(await projectResponse.json());
} catch (error) { status('loadFailed', { message: error.message }, true); }
