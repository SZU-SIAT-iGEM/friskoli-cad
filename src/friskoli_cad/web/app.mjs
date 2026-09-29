import { normalizeReplay, cellHistory } from './replay.mjs';
import { registerCatalog, resolveGraph, readableManifest, unavailableModules } from './catalog.mjs';
import { adaptWorkspace } from './migration.mjs';
import { renderModuleDocumentation } from './math-inspector.mjs';
import { blocksFromProject, createBlock, checkBlock } from './population.mjs';
import { writeWorkspace, draftSnapshot, blankProject, deletePopulation, exportRun, metricsCSV, RECOVERY_KEY } from './workspace.mjs';
import { KernelClient } from './kernel-client.mjs';
import { renderResultData } from './results.mjs';
import { installDockSizing } from './panels.mjs';
import { SpatialViewport } from './scene3d.mjs';
import { GraphEditor, autoLayout } from './workflow.mjs';
import { addNode, connect, connectionProblem, disconnect, missingInputs, preferredTiming, removeNode,
  setParameter } from './graph-edit.mjs';
import { applyLanguage, currentLanguage, setLanguage, t } from './i18n.mjs';
import { availablePlaceables, objectForBlock, initializeObject } from './placeables.mjs';
import { icon } from './icons.mjs';

const $ = id => document.getElementById(id);
const el = (tag, className = '', value = '') => {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = value;
  return item;
};
const fmt = (value, digits = 2) => Number.isFinite(value) ? Number(value.toFixed(digits)).toString() : '—';
const state = { project: null, blocks: [], modules: new Map(), replay: null, view: 'space', left: 'objects',
  frameIndex: 0, selectedBlock: null, selectedCell: null, graphSelection: null, selectedManifest: null,
  field: '', slice: 0, ranges: {}, timer: null, status: ['ready', {}, false], layout: {},
  history: [], future: [], tool: 'select', snap: false, settings: {dt_s: .5, steps: 8},
  revision: 0, saved: '', busy: false, checks: [], runs: [], activeRun: null, template: null, capabilities: null };
const kernel = new KernelClient();
state.objects = new Map();
state.activeObject = null;
const TOOLS = { select: 'select-tool', population: 'population-tool', move: 'move-tool', scale: 'scale-tool',
  rotate: 'rotate-tool', hand: 'hand-tool', orbit: 'orbit-tool', measure: 'measure-tool' };
let viewport;
installDockSizing($('studio'), $('diagnostics'));

// Undo keeps whole-document snapshots; projects are small enough that this stays cheap.
const snapshot = () => structuredClone({ project: state.project, blocks: state.blocks, layout: state.layout, settings: state.settings });
const fingerprint = () => JSON.stringify(snapshot());
function changed() {
  state.revision++;
  state.checks = [];
  try { localStorage.setItem(RECOVERY_KEY, JSON.stringify(writeWorkspace(state))); }
  catch { status('recoveryFailed', {}, true); }
}
function pushHistory(before) {
  state.history.push(before);
  if (state.history.length > 80) state.history.shift();
  state.future = [];
  changed();
  updateHistoryButtons();
}
function restore(saved) {
  Object.assign(state, saved);
  const graph = state.project.graph;
  const sel = state.graphSelection;
  if (sel && !(sel.kind === 'node' ? graph.nodes : graph.edges).some(item => item.id === sel.id)) state.graphSelection = null;
  if (!state.blocks.some(block => block.id === state.selectedBlock)) state.selectedBlock = null;
}
function updateHistoryButtons() {
  $('undo-button').disabled = !state.history.length;
  $('redo-button').disabled = !state.future.length;
}
function travel(from, to, message) {
  if (!from.length || !state.project) return;
  to.push(snapshot());
  restore(from.pop());
  changed();
  updateHistoryButtons();
  renderAll();
  status(message);
}
const undo = () => travel(state.history, state.future, 'undone');
const redo = () => travel(state.future, state.history, 'redone');

// Applies one recorded edit; a thrown error code rolls the document back.
function edit(message, change, values = {}) {
  const before = snapshot();
  try { change(); }
  catch (error) {
    restore(before);
    renderAll();
    status('editFailed', { message: t(error.message) }, true);
    return false;
  }
  pushHistory(before);
  renderAll();
  status(message, values);
  return true;
}

applyLanguage();
$('language-select').value = currentLanguage();

function status(key, values = {}, error = false) {
  state.status = [key, values, error];
  $('status-message').textContent = t(key, values);
  document.querySelector('.status-dot').classList.toggle('error', error);
  const list = $('log-list');
  const entry = el('li', error ? 'error' : '');
  entry.append(el('time', '', new Date().toLocaleTimeString()), el('span', '', t(key, values)));
  list.prepend(entry);
  while (list.children.length > 200) list.lastChild.remove();
}

// Space tools: select, place, move/scale gizmo, measure. Any tool other than select switches to Space.
function useTool(tool) {
  if (!state.project) return;
  if (tool !== 'select' && state.view !== 'space') setView('space');
  state.tool = tool;
  viewport?.setTool(tool);
  for (const [name, id] of Object.entries(TOOLS)) $(id).classList.toggle('active', name === tool);
  if (tool === 'measure') status('measureHint');
  if (state.project && state.view === 'space') renderLeft();
}

function kv(parent, label, value) {
  const row = el('div', 'property-row');
  if (label === t('description')) row.classList.add('property-description');
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
  const nz = (state.view === 'results' ? state.replay?.domain : state.project?.domain)?.counts_xyz[2] ?? 1;
  $('slice-select').replaceChildren(...Array.from({ length: nz }, (_, index) =>
    new Option(`Z ${index + 1} / ${nz}`, String(index))));
  state.slice = Math.min(state.slice, nz - 1);
  $('slice-select').value = String(state.slice);
  $('slice-select').hidden = state.view !== 'results' || !state.field || nz === 1;
}

function currentSnapshot() {
  if (state.view === 'results') return state.replay?.snapshots[state.frameIndex] ?? null;
  return state.project ? draftSnapshot(state.project) : null;
}

function renderScene() {
  if (!state.project) return;
  renderFieldControls();
  const snapshot = currentSnapshot();
  const domain = state.view === 'results' ? state.replay?.domain ?? state.project.domain : state.project.domain;
  viewport?.setDomain(domain);
  viewport?.setSnap(state.snap);
  viewport?.setMode(state.view);
  if (state.view !== 'workflow') {
    viewport?.setSnapshot(snapshot ?? {frame:{cells:[]},concentrations:{}}, state.view === 'results' ? state.selectedCell : null,
      state.view === 'results' ? state.field : '', state.slice, state.ranges[state.field]);
    viewport?.setBlocks(state.blocks, state.selectedBlock);
  }
  const [nx, ny, nz] = domain.counts_xyz;
  const [dx, dy, dz] = domain.spacing_um_xyz;
  $('view-readout').textContent = `${fmt(nx * dx)} × ${fmt(ny * dy)} × ${fmt(nz * dz)} µm`;
  $('document-meta').textContent = `${domain.geometry === 'volume' ? '3D' : 'THIN LAYER'}  ·  ${nx} × ${ny} × ${nz}`;
  $('status-count').textContent = `${snapshot?.frame.cells.length ?? 0} ${t('cells')} · ${state.blocks.length} ${t('populations')}`;
  $('status-version').textContent = state.view === 'results' ? `FRAME ${snapshot?.frame.frame_version ?? '—'}` : 'DRAFT';
  $('empty-results').hidden = state.view !== 'results' || Boolean(state.replay);
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
  renderData();
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
  if (state.view === 'results') {
    container.append(el('div', 'tree-heading', t('runs')));
    for (const run of state.runs) treeRow(container, run.id, t(run.status), run === state.activeRun, () => {
      if (!run.replay) { status('runFailed', {message:run.error ?? t('running')}, true); return; }
      state.activeRun = run; state.replay = run.replay; state.frameIndex = 0; state.selectedCell = null;
      updateRanges(); renderAll();
    }, '◷');
    for (const cell of (currentSnapshot()?.frame.cells ?? []).filter(c => !filter || c.id.toLowerCase().includes(filter)).slice(0, 100)) {
      treeRow(container, cell.id, cell.group_id, state.selectedCell === cell.id, () => selectCell(cell.id), '·');
    }
    return;
  }
  if (state.left === 'modules') {
    container.append(el('div', 'tree-heading', `${t('compiled')} · ${state.modules.size}`));
    for (const [key, manifest] of state.modules) {
      if (filter && !`${key} ${manifest.description}`.toLowerCase().includes(filter)) continue;
      treeRow(container, manifest.declaration?.label ?? manifest.id, manifest.version, state.selectedManifest === key,
        () => { state.selectedManifest = key; renderLeft(); renderInspector(); closeDocks(); }, '⬡');
    }
    return;
  }
  if (state.view === 'workflow') {
    container.append(el('div', 'tree-heading', `${t('workflow')} · ${state.project.graph.nodes.length}`));
    for (const node of state.project.graph.nodes) {
      if (filter && !`${node.id} ${node.module_id}`.toLowerCase().includes(filter)) continue;
      const active = state.graphSelection?.kind === 'node' && state.graphSelection.id === node.id;
      const row = treeRow(container, node.id, node.module_id.split('.').at(-1), active,
        () => { selectGraph({ kind: 'node', id: node.id }); closeDocks(); }, '⬡');
      row.addEventListener('contextmenu', event => {
        event.preventDefault(); showGraphContext({ kind: 'node', id: node.id }, event.clientX, event.clientY);
      });
    }
    return;
  }
  if (state.view === 'space') {
    container.append(el('div', 'tree-heading', t('library')));
    for (const item of availablePlaceables(state.modules, state.capabilities, state.objects)) {
      const ready = item.status === 'ready';
      const row = el('button', `tree-row library-row${ready ? '' : ' unavailable'}${ready && state.tool === item.tool ? ' active' : ''}`);
      row.type = 'button';
      row.title = ready ? t(item.hint) : `${t(item.hint)} ${t(item.status)}`;
      const glyph = el('span', 'tree-icon');
      glyph.innerHTML = icon(item.icon);
      const name = el('span', 'tree-name');
      name.append(el('span', 'library-name', t(item.label)),
        el('span', 'library-hint', ready ? t(item.hint) : `${t(item.hint)} · ${t(item.status)}`));
      row.append(glyph, name);
      if (ready) {
        row.draggable = true;
        row.addEventListener('dragstart', event => { state.activeObject = item.id; event.dataTransfer.setData('application/friskoli-object', item.kind); });
        row.addEventListener('click', () => { state.activeObject = item.id; useTool(item.tool); closeDocks(); });
      } else { row.disabled = true; row.title = t(item.status); }
      container.append(row);
    }
  }
  container.append(el('div', 'tree-heading', `${t('domain')} · ${state.blocks.length}`));
  treeRow(container, state.project.id, t('domain'), !state.selectedBlock, () => { state.selectedBlock = null; renderInspector(); renderLeft(); });
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

function numberField(parent, label, block, key, axis = null, definition = {}) {
  const row = el('label', 'edit-row');
  const input = el('input');
  input.type = 'number';
  if (definition.minimum !== undefined) input.min = definition.minimum;
  if (definition.maximum !== undefined) input.max = definition.maximum;
  input.step = key === 'count' || key === 'seed' ? '1' : '.1';
  input.value = axis === null ? block[key] : block[key][axis];
  input.setAttribute('aria-label', label);
  row.append(el('span', '', label), input);
  const update = () => {
    if (input.value === '') return;
    const value = Number(input.value);
    if ((definition.minimum !== undefined && value < definition.minimum) ||
        (definition.maximum !== undefined && value > definition.maximum)) { input.reportValidity(); return; }
    if (!Number.isFinite(value) || ((key === 'count' || key === 'seed') && !Number.isInteger(value))) {
      input.setCustomValidity('Invalid number'); input.reportValidity(); return;
    }
    input.setCustomValidity('');
    edit('blockUpdated', () => {
      if (axis === null) block[key] = value; else block[key][axis] = value;
      checkBlock(state.project.domain, block);
      if (block.count < 1 || block.count > 2000) throw new Error('Invalid count');
      block.dirty = true;
    });
  };
  input.disabled = Boolean(block.locked);
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
    edit('updated', () => { block.name = value; });
  });
  count.append(nameRow);
  block.rotation ??= [0, 0, 0];
  const object = objectForBlock(block, state.objects);
  for (const property of object?.properties ?? []) {
    const parent = property.type === 'vector3' ? section(root, t(property.label) + ' · ' + property.unit) : count;
    if (property.type === 'vector3') ['X','Y','Z'].forEach((axis,index) =>
      numberField(parent, axis, block, property.path, index, property));
    else numberField(parent, t(property.label) + (property.unit === '1' ? '' : ' · ' + property.unit), block, property.path, null, property);
  }
  const rotation = section(root, t('rotation') + ' · °');
  for (const key of ['hidden', 'locked']) {
    const label = el('label', 'edit-row', t(key)), input = el('input'); input.type = 'checkbox'; input.checked = Boolean(block[key]);
    input.addEventListener('change', () => edit('updated', () => { block[key] = input.checked; })); label.append(input); rotation.append(label);
  }
  const action = el('button', 'inspector-action', t('scatter'));
  action.type = 'button';
  action.disabled = Boolean(block.locked) || !object;
  action.addEventListener('click', () => scatter(block.id));
  root.append(action);
  if (!object) root.append(el('p','pending-note',t('unsupportedObject')));
  if (block.binding) {
    const details = el('details','property-section');
    details.append(el('summary','',t('objectData')));
    for (const id of block.binding.data_nodes) {
      const node = findNode(id);
      const button = el('button','inspector-action',id + (node ? '' : ' · ' + t('removed')));
      button.disabled = !node;
      button.addEventListener('click', () => { setView('workflow'); selectGraph({kind:'node',id}); });
      details.append(button);
    }
    root.append(details);
  }
  for (const [label, handler] of [['duplicate', () => duplicateBlock(block.id)], ['delete', () => removeBlock(block.id)]]) {
    const button = el('button', 'inspector-action', t(label)); button.disabled = Boolean(block.locked);
    button.addEventListener('click', handler); root.append(button);
  }
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

function renderDomainInspector(root) {
  const part = section(root, t('geometry'));
  const form = el('form', 'domain-form');
  const name = el('input'); name.value = state.project.id; name.required = true; name.setAttribute('aria-label', t('name'));
  const nameRow = el('label', 'edit-row', t('name')); nameRow.append(name); form.append(nameRow);
  const mode = el('select'); mode.setAttribute('aria-label', t('geometry'));
  mode.append(new Option('3D', 'volume'), new Option(t('thinLayer'), 'thin_layer')); mode.value = state.project.domain.geometry;
  const row = el('label', 'edit-row', t('geometry')); row.append(mode); form.append(row);
  const inputs = [];
  for (const key of ['counts_xyz', 'spacing_um_xyz']) {
    form.append(el('h3', '', t(key === 'counts_xyz' ? 'grid' : 'spacing')));
    const triple = [];
    for (let axis = 0; axis < 3; axis++) {
      const input = el('input'); input.type = 'number'; input.required = true; input.min = key === 'counts_xyz' ? '1' : '.000001';
      input.step = key === 'counts_xyz' ? '1' : 'any'; input.value = state.project.domain[key][axis];
      input.setAttribute('aria-label', `${key} ${'XYZ'[axis]}`);
      const row = el('label', 'edit-row', `${'XYZ'[axis]}${key === 'spacing_um_xyz' ? ' µm' : ''}`); row.append(input); form.append(row); triple.push(input);
    }
    inputs.push(triple);
  }
  const apply = el('button', 'inspector-action', t('apply')); apply.type = 'submit'; form.append(apply);
  form.addEventListener('submit', event => {
    event.preventDefault();
    edit('updated', () => {
      const [counts, spacing] = inputs.map(group => group.map(input => Number(input.value)));
      if (mode.value === 'thin_layer' && counts[2] !== 1) throw new Error('Thin layers require one Z grid cell');
      if (!counts.every(n => Number.isInteger(n) && n > 0) || !spacing.every(n => Number.isFinite(n) && n > 0)) throw new Error('Invalid domain');
      const domain = {geometry:mode.value, counts_xyz:counts, spacing_um_xyz:spacing};
      for (const block of state.blocks) checkBlock(domain, block);
      const extent = counts.map((n,i) => n*spacing[i]);
      for (const group of Object.values(state.project.groups)) if (group.positions_um.some(p => p.some((v,i) => v < 0 || v > extent[i]))) throw new Error('Cells outside domain');
      state.project.domain = domain; state.project.id = name.value.trim();
    });
  });
  part.append(form);
  const species = section(root, t('species'));
  for (const [id, entry] of Object.entries(state.project.species)) {
    const label = el('label', 'edit-row', `${id} [µM]`), input = el('input'); input.type = 'number'; input.min = '0'; input.step = 'any';
    input.value = entry.initial_concentration.value; input.setAttribute('aria-label', `${id} concentration`);
    input.addEventListener('change', () => edit('updated', () => {
      const value = Number(input.value); if (!Number.isFinite(value) || value < 0 || input.value === '') throw new Error('Invalid concentration');
      const initial = state.project.species[id].initial_concentration;
      initial.value = value; initial.provenance = {kind:'estimated', reference:'user-defined initial concentration'};
      for (const node of state.project.graph.nodes) if (node.parameters.species?.value === id && node.parameters.initial_concentration) node.parameters.initial_concentration = {value,unit:'uM',provenance:{kind:'user',reference:'user-defined initial concentration'}};
    })); label.append(input); species.append(label);
    const active = state.project.graph.nodes.some(n => n.parameters.species?.value === id);
    species.append(el('div', 'inspector-foot', t(active ? 'connected' : 'registered')));
  }
  const advanced = el('button', 'inspector-action', t('projectData')); advanced.addEventListener('click', () => {
    $('data-editor').value = JSON.stringify(state.project, null, 2); $('data-dialog').showModal();
  }); root.append(advanced);
}

function duplicateBlock(id) {
  const source = state.blocks.find(b => b.id === id);
  if (!source || source.locked) return;
  edit('updated', () => {
    const newId = createBlock(state.project, source.center, state.blocks.map(b => b.id)).id;
    const copy = {...structuredClone(source), id:newId, name:source.name + ' copy', dirty:true, hidden:false, locked:false};
    delete copy.binding;
    state.blocks.push(copy); state.selectedBlock = copy.id;
  });
}

function removeBlock(id) {
  if (state.blocks.find(b => b.id === id)?.locked) return;
  edit('updated', () => {
    deletePopulation(state.project, id); state.blocks = state.blocks.filter(b => b.id !== id); state.selectedBlock = null;
    for (const key of Object.keys(state.layout)) if (!findNode(key)) delete state.layout[key];
  });
}

function renderDiagnostics() {
  const root = $('checks-list'); root.replaceChildren();
  for (const issue of state.checks) {
    const item = el('li', issue.code === 'valid' ? '' : issue.severity === 'info' ? 'info' : 'error');
    item.append(el('strong', '', issue.code), el('span', '', `${issue.path ?? ''} ${issue.message}`)); root.append(item);
  }
  if (!state.checks.length) root.append(el('li', '', t('unchecked')));
  const runs = $('runs-list'); runs.replaceChildren();
  for (const run of [...state.runs].reverse()) runs.append(el('li', '', `${run.id} · ${t(run.status)}${run.error ? ' · ' + run.error : ''}`));
  renderData();
}

function renderData() { renderResultData($('data-pane'), state.replay, state.selectedCell, setFrame,
  {empty:t('noResults'),cells:t('cells'),events:t('events'),frame:t('frame'),curve:t('cellCountHistory')}); }

async function checkProject() {
  if (!state.project || state.busy) return;
  const revision = state.revision;
  state.checks = [];
  for (const b of state.blocks) if (b.dirty) state.checks.push({code:'scatter.pending',path:b.id,message:t('pending')});
  try {
    const result = await kernel.validate(structuredClone(state.project), state.settings);
    if (revision !== state.revision) return;
    if (!state.checks.length) state.checks = [{code:'valid', message:t('checkPassed', result)}];
  } catch (error) {
    if (revision !== state.revision) return;
    state.checks.push(error.issue ?? {code:'connection.failed',message:error.message});
  }
  $('diagnostics').hidden = false; showBottom('checks'); renderDiagnostics();
  status(state.checks[0]?.code === 'valid' ? 'checked' : 'checkFailed', {}, state.checks[0]?.code !== 'valid');
}

function showBottom(tab) {
  $('diagnostics').hidden = false;
  for (const name of ['checks', 'runs', 'console', 'data']) $(`${name}-pane`).hidden = name !== tab;
  for (const button of document.querySelectorAll('[data-bottom]')) button.classList.toggle('active', button.dataset.bottom === tab);
}

const moduleKey = node => `${node.module_id}@${node.module_version}`;
const findNode = id => state.project.graph.nodes.find(node => node.id === id);

function parameterField(parent, node, manifest, name, definition) {
  const row = el('label', 'edit-row');
  const input = el('input');
  const entry = node.parameters[name];
  if (definition.type === 'boolean') { input.type = 'checkbox'; input.checked = entry?.value === true; }
  else if (definition.type === 'string') {
    input.type = 'text';
    input.value = entry?.value ?? '';
    input.setAttribute('list', 'species-options');
  } else {
    input.type = 'number';
    input.step = definition.type === 'integer' ? '1' : 'any';
    if (definition.minimum !== undefined) input.min = String(definition.minimum);
    if (definition.maximum !== undefined) input.max = String(definition.maximum);
    input.value = String(entry?.value ?? '');
  }
  input.setAttribute('aria-label', name);
  const label = definition.unit ? `${name} [${definition.unit}]` : name;
  row.append(el('span', '', label), input);
  if (entry?.provenance) row.title = `${entry.provenance.kind} · ${entry.provenance.reference}`;
  input.addEventListener('change', () => {
    const raw = definition.type === 'boolean' ? input.checked : input.value;
    const id = node.id;
    edit('parameterUpdated', () => {
      const problem = setParameter(findNode(id), manifest, name, raw);
      if (problem) throw new Error(problem);
    }, { name });
  });
  parent.append(row);
}

function ownerFor(manifest) {
  if (manifest.scope === 'environment') return { kind: 'environment', id: 'domain' };
  if (manifest.scope === 'population') {
    const groups = Object.keys(state.project.groups ?? {});
    const id = groups.includes(state.selectedBlock) ? state.selectedBlock : groups[0];
    return id ? { kind: 'population', id } : null;
  }
  const existing = state.project.graph.nodes.find(node => node.owner.kind === 'source');
  return { kind: 'source', id: existing?.owner.id ?? 'source_1' };
}

function addGraphNode(manifest) {
  const owner = ownerFor(manifest);
  if (!owner) { status('editFailed', { message: t('node.owner') }, true); return; }
  let node;
  if (edit('nodeAdded', () => {
    node = addNode(state.project.graph, manifest, owner, Object.keys(state.project.species ?? {}));
  }, { id: manifest.id })) {
    state.graphSelection = { kind: 'node', id: node.id };
    state.selectedManifest = null;
    state.left = 'objects';
    setView('workflow');
  }
}

function deleteGraphItem(selection) {
  if (!selection) return;
  edit(selection.kind === 'node' ? 'nodeRemoved' : 'edgeRemoved', () => {
    const graph = state.project.graph;
    const removed = selection.kind === 'node' ? removeNode(graph, selection.id) : disconnect(graph, selection.id);
    if (!removed) throw new Error('edge.node');
    if (selection.kind === 'node') delete state.layout[selection.id];
    if (selection.kind === 'node') for (const [id, channel] of Object.entries(state.project.run.channels)) {
      if (channel.node === selection.id) delete state.project.run.channels[id];
    }
    state.graphSelection = null;
  }, { id: selection.id });
}

function setEdgeTiming(edgeId, timing) {
  edit('edgeUpdated', () => {
    const graph = state.project.graph;
    const edge = graph.edges.find(item => item.id === edgeId);
    if (!edge) throw new Error('edge.node');
    // Check the new timing against the graph without this edge, so fan-in does not flag itself.
    const others = { ...graph, edges: graph.edges.filter(item => item.id !== edgeId) };
    const problem = connectionProblem(others, state.modules, edge.from, edge.to, timing);
    if (problem) throw new Error(problem);
    edge.timing = timing;
  }, { id: edgeId });
}

function renderEdgeInspector(root, edge) {
  root.append(el('div', 'inspector-title', edge.id), el('div', 'inspector-subtitle', t('connection')));
  const part = section(root, t('connection'));
  kv(part, t('source'), `${edge.from.node}.${edge.from.port}`);
  kv(part, t('target'), `${edge.to.node}.${edge.to.port}`);
  const src = findNode(edge.from.node);
  const port = src ? state.modules.get(moduleKey(src))?.outputs[edge.from.port] : null;
  if (port) kv(part, t('portType'), `${port.shape} · ${port.quantity} [${port.unit}]`);
  const row = el('label', 'edit-row');
  const select = el('select');
  select.setAttribute('aria-label', t('timing'));
  for (const timing of ['same_step', 'previous_step']) select.append(new Option(t(timing), timing));
  select.value = edge.timing;
  select.addEventListener('change', () => setEdgeTiming(edge.id, select.value));
  row.append(el('span', '', t('timing')), select);
  part.append(row);
  const remove = el('button', 'inspector-action danger', t('deleteEdge'));
  remove.type = 'button';
  remove.addEventListener('click', () => deleteGraphItem({ kind: 'edge', id: edge.id }));
  root.append(remove);
}

function renderGraphSummary(root) {
  const graph = state.project.graph;
  root.append(el('div', 'inspector-title', graph.id), el('div', 'inspector-subtitle', t('workflow')));
  const part = section(root, t('workflow'));
  kv(part, t('nodes'), String(graph.nodes.length));
  kv(part, t('edges'), String(graph.edges.length));
  const missing = missingInputs(graph, state.modules);
  const check = section(root, t('checks'));
  const unavailable = unavailableModules(graph, state.modules);
  if (!missing.length && !unavailable.length) check.append(el('p', 'empty-message', t('graphReady')));
  for (const node of unavailable) {
    const button = el('button','event-row warning',node.id + ': ' + t('unknownModule'));
    button.addEventListener('click', () => selectGraph({kind:'node',id:node.id})); check.append(button);
  }
  for (const item of missing) {
    const button = el('button', 'event-row warning', t('missingInput', { node: item.node, port: item.port }));
    button.type = 'button';
    button.addEventListener('click', () => selectGraph({ kind: 'node', id: item.node }));
    check.append(button);
  }
  root.append(el('p', 'empty-message', t('graphHint')));
}

function renderManifest(root, manifest, node = null) {
  root.append(el('div', 'inspector-title', node?.id ?? manifest.id),
    el('div', 'inspector-subtitle', `${manifest.id}@${manifest.version}`));
  const identity = section(root, t('module'));
  kv(identity, t('scope'), manifest.scope);
  kv(identity, t('phase'), String(manifest.phase));
  kv(identity, t('maturity'), manifest.maturity ?? '—');
  kv(identity, t('description'), manifest.description ?? '—');
  renderModuleDocumentation(root, manifest, t);
  if (manifest.unavailable && node) {
    root.append(el('p','pending-note',t('unknownModule')));
    const original = section(root,t('parameters'));
    for (const [name,value] of Object.entries(node.parameters)) kv(original,name,JSON.stringify(value));
  }
  if (node) {
    kv(identity, t('owner'), `${node.owner.kind} · ${node.owner.id}`);
    const params = section(root, t('parameters'));
    for (const [name, definition] of Object.entries(manifest.parameters)) parameterField(params, node, manifest, name, definition);
    const remove = el('button', 'inspector-action danger', t('deleteNode'));
    remove.type = 'button';
    remove.addEventListener('click', () => deleteGraphItem({ kind: 'node', id: node.id }));
    root.append(remove);
  } else {
    const params = section(root, t('parameters'));
    for (const [key, definition] of Object.entries(manifest.parameters)) kv(params, key, definition.unit ?? definition.type);
    const add = el('button', 'inspector-action', t('addToGraph'));
    add.type = 'button';
    if (manifest.scope === 'population') {
      const owner = el('select'); owner.setAttribute('aria-label', t('owner'));
      for (const id of Object.keys(state.project.groups)) owner.append(new Option(id, id));
      if (state.project.groups[state.selectedBlock]) owner.value = state.selectedBlock;
      owner.addEventListener('change', () => { state.selectedBlock = owner.value; });
      params.append(owner);
      add.disabled = !owner.options.length;
    }
    add.addEventListener('click', () => addGraphNode(manifest));
    root.append(add);
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
  if (!state.project) return;
  if (state.inspectorTab === 'evidence') { renderEvidence(root); return; }
  if (state.view !== 'results' && state.left === 'modules' && state.selectedManifest) {
    renderManifest(root, state.modules.get(state.selectedManifest)); return;
  }
  if (state.view === 'space') {
    const block = state.blocks.find(item => item.id === state.selectedBlock);
    if (block) { renderBlockInspector(root, block); return; }
    root.append(el('div', 'inspector-title', t('domain')));
    renderDomainInspector(root);
  } else if (state.view === 'workflow') {
    const sel = state.graphSelection;
    const node = sel?.kind === 'node' ? findNode(sel.id) : null;
    const edge = sel?.kind === 'edge' ? state.project.graph.edges.find(item => item.id === sel.id) : null;
    if (node) renderManifest(root, readableManifest(node, state.project.graph, state.modules), node);
    else if (edge) renderEdgeInspector(root, edge);
    else renderGraphSummary(root);
  } else {
    const frame = currentSnapshot()?.frame;
    if (!frame) { root.append(el('p', 'empty-message', t('noResults'))); return; }
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

function renderEvidence(root) {
  const selectedNode = state.graphSelection?.kind === 'node' ? findNode(state.graphSelection.id) : null;
  const manifest = selectedNode ? state.modules.get(moduleKey(selectedNode)) : state.selectedManifest ? state.modules.get(state.selectedManifest) : null;
  root.append(el('div','inspector-title',manifest?.id ?? t('evidence')),el('div','inspector-subtitle',manifest ? `${manifest.id}@${manifest.version}` : t('evidenceHint')));
  if (!manifest) { root.append(el('p','empty-message',t('selectModuleEvidence'))); return; }
  const identity=section(root,t('evidence'));kv(identity,t('maturity'),manifest.maturity ?? '—');kv(identity,t('scientificRole'),manifest.scientific_role ?? '—');kv(identity,t('scope'),manifest.scope);kv(identity,t('phase'),String(manifest.phase));
  root.append(el('p','empty-message',t('evidenceRule')));
  const assumptions=section(root,t('description'));assumptions.append(el('p','empty-message',manifest.description ?? '—'));
  const params=section(root,t('parameters'));for(const [name,definition] of Object.entries(manifest.parameters ?? {})) kv(params,name,definition.unit ? `${definition.type} · ${definition.unit}` : definition.type);
}

function renderGraph() {
  if (!state.project || state.view !== 'workflow') return;
  state.layout = autoLayout(state.project.graph, state.modules, state.layout);
  try {
    graphEditor.render(state.project.graph, state.modules, state.layout, state.graphSelection,
      missingInputs(state.project.graph, state.modules));
  } catch (error) { status('runFailed', { message: error.message }, true); }
}

function selectGraph(selection) {
  state.graphSelection = selection;
  renderLeft();
  renderGraph();
  renderInspector();
  closeDocks();
}

function connectPorts(from, to) {
  const timing = preferredTiming(state.project.graph, state.modules, from, to);
  if (!timing) {
    const problem = connectionProblem(state.project.graph, state.modules, from, to, 'same_step');
    status('editFailed', { message: t(problem) }, true);
    return;
  }
  let edge;
  if (edit('edgeAdded', () => { edge = connect(state.project.graph, state.modules, from, to, timing); },
    { timing: t(timing) })) selectGraph({ kind: 'edge', id: edge.id });
}

const graphEditor = new GraphEditor($('workflow-view'), {
  toggle(id) { edit('updated', () => { state.layout[id].collapsed = !state.layout[id].collapsed; }); },
  hint: () => status('connectHint'),
  select: selectGraph,
  canConnect: (from, to) => Boolean(preferredTiming(state.project.graph, state.modules, from, to)),
  connect: connectPorts,
  move(id, position, origin) {
    // Layout is already updated for a smooth drag; record the pre-drag state for undo.
    const before = snapshot();
    before.layout[id] = origin;
    state.layout[id] = {...state.layout[id], ...position};
    pushHistory(before);
    renderAll();
  },
  context: (selection, x, y) => { selectGraph(selection); showGraphContext(selection, x, y); },
});

function showGraphContext(selection, x, y) {
  const menu = $('context-menu');
  menu.replaceChildren();
  const remove = el('button', '', t(selection.kind === 'node' ? 'deleteNode' : 'deleteEdge'));
  remove.type = 'button';
  remove.addEventListener('click', () => { menu.hidden = true; deleteGraphItem(selection); });
  menu.append(remove);
  if (selection.kind === 'edge') {
    const edge = state.project.graph.edges.find(item => item.id === selection.id);
    const other = edge?.timing === 'same_step' ? 'previous_step' : 'same_step';
    const flip = el('button', '', `${t('timing')} → ${t(other)}`);
    flip.type = 'button';
    flip.addEventListener('click', () => { menu.hidden = true; setEdgeTiming(selection.id, other); });
    menu.append(flip);
  }
  menu.style.left = `${Math.min(x, innerWidth - 190)}px`;
  menu.style.top = `${Math.min(y, innerHeight - 100)}px`;
  menu.hidden = false;
}

function updateRunButton() {
  const pending = state.blocks.some(block => block.dirty);
  const missing = state.project ? missingInputs(state.project.graph, state.modules).length : 0;
  const unavailable = state.project ? unavailableModules(state.project.graph, state.modules).length : 0;
  $('run-button').disabled = state.busy || pending || !state.project || missing > 0 || unavailable > 0;
  $('run-button').title = state.busy ? t('running') : pending ? t('pending') : missing ? t('missingConnections') : '';
  if (unavailable) $('run-button').title = t('unknownModule');
  $('check-button').disabled = state.busy || !state.project;
}

function renderAll() {
  if (!state.project) return;
  $('project-title').textContent = state.project.id + (fingerprint() !== state.saved ? ' •' : '');
  $('save-button').classList.toggle('modified', fingerprint() !== state.saved);
  $('dt-input').value = state.settings.dt_s;
  $('steps-input').value = state.settings.steps;
  $('species-options').replaceChildren(...Object.keys(state.project.species ?? {}).map(id => new Option(id, id)));
  $('document-label').textContent = t(state.view === 'workflow' ? 'workflow' : state.view === 'results' ? 'results' : 'space');
  for (const button of document.querySelectorAll('[data-view]')) button.classList.toggle('active', button.dataset.view === state.view);
  for (const button of document.querySelectorAll('[data-left]')) button.classList.toggle('active', button.dataset.left === state.left);
  $('spatial-canvas').hidden = state.view === 'workflow';
  $('scene-annotations').hidden = state.view === 'workflow';
  $('view-controls').hidden = state.view === 'workflow';
  $('workflow-view').hidden = state.view !== 'workflow';
  $('bottom-dock').hidden = state.view !== 'results' || !state.replay;
  $('result-banner').hidden = state.view !== 'results' || !state.activeRun;
  $('result-banner').textContent = state.activeRun ? `${state.activeRun.id} · ${t(state.activeRun.revision === state.revision ? 'completed' : 'earlierRevision')}` : '';
  $('export-button').disabled = !state.replay;
  $('population-tool').disabled = state.view !== 'space' || !state.activeObject;
  renderScene();
  renderTimeline();
  renderLeft();
  renderGraph();
  renderInspector();
  updateRunButton();
  renderDiagnostics();
  viewport?.resize();
}

function setView(view) {
  stop();
  state.view = view;
  if (view !== 'space') useTool('select');
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

async function executeProject(candidate) {
  if (state.busy) return false;
  const {dt_s: dt, steps} = state.settings;
  if (!Number.isFinite(dt) || dt <= 0 || !Number.isInteger(steps) || steps < 1 || steps > 100) {
    status('invalidControls', {}, true); return false;
  }
  stop();
  const submitted = structuredClone(candidate), settings = structuredClone(state.settings);
  const id = `run-${Date.now()}-${state.runs.length + 1}`;
  submitted.run.run_id = id;
  const record = {id, project: submitted, settings, revision: state.revision, status: 'running'};
  state.runs.push(record);
  state.busy = true;
  updateRunButton(); renderDiagnostics();
  status('running');
  try {
    resolveGraph(submitted.graph, state.modules);
    const result = await kernel.run(submitted, settings, id);
    if (result.execution?.request_id !== id) throw new Error('Run response identity mismatch');
    record.replay = normalizeReplay(result); record.status = 'completed';
    state.activeRun = record;
    state.replay = normalizeReplay(result);
    state.frameIndex = Math.max(0, state.replay.snapshots.findIndex(item => item.frame.events.length));
    updateRanges();
    setView('results');
    status('runReady', { count: state.replay.snapshots.length,
      cells: currentSnapshot().frame.cells.length });
    return true;
  } catch (error) {
    record.status = 'failed'; record.error = error.message;
    status('runFailed', { message: error.message }, true);
    return false;
  } finally { state.busy = false; updateRunButton(); renderDiagnostics(); }
}

function scatter(id) {
  const block = state.blocks.find(item => item.id === id);
  if (!block || block.locked) return;
  edit('scattered', () => initializeObject(state.project, block, objectForBlock(block, state.objects), state.modules), {count:block.count, id});
}

function showContext(id, x, y) {
  const menu = $('context-menu');
  menu.replaceChildren();
  const scatterButton = el('button', '', t('scatter'));
  scatterButton.type = 'button';
  scatterButton.addEventListener('click', () => { menu.hidden = true; scatter(id); });
  menu.append(scatterButton);
  const block = state.blocks.find(b => b.id === id);
  scatterButton.disabled = Boolean(block?.locked);
  for (const [key, action] of [['duplicate', () => duplicateBlock(id)], ['delete', () => removeBlock(id)]]) {
    const b = el('button', '', t(key)); b.disabled = Boolean(block?.locked); b.addEventListener('click', () => { menu.hidden = true; action(); }); menu.append(b);
  }
  menu.style.left = `${Math.min(x, innerWidth - 190)}px`;
  menu.style.top = `${Math.max(0, Math.min(y, innerHeight - 150))}px`;
  menu.hidden = false;
  state.selectedBlock = id;
  renderInspector();
  viewport?.setBlocks(state.blocks, id);
}

function saveWorkspace() {
  if (!state.project) return;
  download(`${state.project.id}.friskoli-workspace.json`, JSON.stringify(writeWorkspace(state), null, 2));
  state.saved = fingerprint(); renderAll(); status('saved');
}

function download(name, content, type = 'application/json') {
  const blob = new Blob([content + '\n'], { type });
  const url = URL.createObjectURL(blob);
  const link = el('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function loadProject(document) {
  const {state:loaded,report} = adaptWorkspace(document, state.modules);
  if (state.busy) throw new Error(t('running'));
  Object.assign(state, loaded);
  state.layout = autoLayout(state.project.graph, state.modules, state.layout);
  state.selectedBlock = loaded.blocks[0]?.id ?? null;
  state.selectedCell = null;
  state.graphSelection = null;
  state.selectedManifest = null;
    state.replay = null; state.runs = []; state.activeRun = null;
    state.history = [];
    state.future = [];
    updateHistoryButtons();
    changed();
    state.saved = fingerprint();
    $('welcome-dialog').close();
    setView('space');
    status('opened');
    state.checks = [...report.issues, ...report.changes.map(change => ({...change,severity:'info'}))];
    if (state.checks.length) { showBottom('checks'); renderDiagnostics(); }
}

try {
  viewport = new SpatialViewport($('spatial-canvas'), $('scene-annotations'), {
    selectCell, selectBlock,
    placePopulation(point) {
      if (!state.project || !state.activeObject) return;
      edit('blockPlaced', () => {
        const block = createBlock(state.project, point, state.blocks.map(item => item.id));
        block.object_type = state.activeObject;
        state.blocks.push(block); state.selectedBlock = block.id;
      });
      useTool('select');
    },
    contextBlock: showContext,
    transformBlock(id, center, size, rotation) {
      const block = state.blocks.find(b => b.id === id);
      if (!block || block.locked) return;
      edit('blockMoved', () => { Object.assign(block, {center, size, rotation, dirty:true}); checkBlock(state.project.domain, block); });
    },
    measure(result) {
      $('measure-readout').textContent = result ? `${fmt(result.distance, 3)} µm · Δ ${result.delta.map(v => fmt(v, 3)).join(' / ')}` : '';
    },
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
$('open-button').addEventListener('click', () => { if (replaceAllowed()) $('project-file').click(); });
$('save-button').addEventListener('click', saveWorkspace);
$('undo-button').addEventListener('click', undo);
$('redo-button').addEventListener('click', redo);
$('project-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try { if (file.size > 2_000_000) throw new Error('Workspace file exceeds 2 MB'); await loadProject(JSON.parse(await file.text())); }
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
for (const [tool, id] of Object.entries(TOOLS)) $(id).addEventListener('click', () => useTool(tool));
$('snap-tool').addEventListener('click', () => {
  state.snap = !state.snap; $('snap-tool').setAttribute('aria-pressed', state.snap); viewport?.setSnap(state.snap);
  status(state.snap ? 'snapOn' : 'snapOff');
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
for (const button of document.querySelectorAll('[data-inspector]')) button.addEventListener('click', () => {
  state.inspectorTab = button.dataset.inspector;
  for (const item of document.querySelectorAll('[data-inspector]')) item.classList.toggle('active', item === button);
  renderInspector();
});
$('mobile-objects').addEventListener('click', () => $('objects-tool').click());
$('mobile-properties').addEventListener('click', () => $('properties-tool').click());
$('close-left').addEventListener('click', closeDocks);
$('close-right').addEventListener('click', closeDocks);
$('dock-backdrop').addEventListener('click', closeDocks);
document.addEventListener('click', event => { if (!event.target.closest('#context-menu')) $('context-menu').hidden = true; });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { $('context-menu').hidden = true; $('settings-overlay').hidden = true; graphEditor.armed = null; closeDocks(); useTool('select'); }
  if (document.querySelector('dialog[open]') || !$('settings-overlay').hidden) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveWorkspace(); return; }
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
  const mod = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  if (mod && key === 'z') { event.preventDefault(); if (event.shiftKey) redo(); else undo(); return; }
  if (mod && key === 'y') { event.preventDefault(); redo(); return; }
  if (mod && key === 's') { event.preventDefault(); saveWorkspace(); return; }
  if (mod && event.key === 'Enter') { event.preventDefault(); if (!$('run-button').disabled) $('run-button').click(); return; }
  if (key === 'f') { event.preventDefault(); viewport?.fit(); return; }
  if (key === 'g') { viewport?.toggleGrid(); return; }
  if (state.view === 'space') {
    const tool = {v:'select',b:'population',t:'move',w:'move',e:'rotate',r:'scale',m:'measure',h:'hand',o:'orbit'}[key];
    if (tool) { event.preventDefault(); useTool(tool); return; }
    if ((key === 'delete' || key === 'backspace') && state.selectedBlock) { event.preventDefault(); removeBlock(state.selectedBlock); return; }
  }
  if (state.view === 'workflow' && (event.key === 'Delete' || event.key === 'Backspace') && state.graphSelection) {
    event.preventDefault(); deleteGraphItem(state.graphSelection); return;
  }
  if (state.view !== 'results') return;
  if (event.key === 'ArrowLeft') { stop(); setFrame(state.frameIndex - 1); }
  if (event.key === 'ArrowRight') { stop(); setFrame(state.frameIndex + 1); }
});

for (const [id, key] of [['dt-input', 'dt_s'], ['steps-input', 'steps']]) $(id).addEventListener('change', () => {
  const value = Number($(id).value);
  edit('updated', () => { if (!Number.isFinite(value) || value <= 0 || (key === 'steps' && (!Number.isInteger(value) || value > 100))) throw new Error(t('invalidControls')); state.settings[key] = value; });
});
$('check-button').addEventListener('click', checkProject);
$('log-button').addEventListener('click', () => showBottom('console'));
$('diagnostics-close').addEventListener('click', () => { $('diagnostics').hidden = true; });
for (const button of document.querySelectorAll('[data-bottom]')) button.addEventListener('click', () => showBottom(button.dataset.bottom));
$('new-button').addEventListener('click', () => $('welcome-dialog').showModal());
$('welcome-close').addEventListener('click', () => $('welcome-dialog').close());
$('welcome-open').addEventListener('click', () => $('project-file').click());
const replaceAllowed = () => !state.project || fingerprint() === state.saved || confirm(t('replaceDraft'));
$('welcome-new').addEventListener('click', () => { if (replaceAllowed()) loadProject(blankProject(state.template)); });
$('welcome-demo').addEventListener('click', () => { if (replaceAllowed()) loadProject(state.template); });
$('welcome-registry').addEventListener('click', async () => {
  if (!replaceAllowed()) return;
  try { await loadProject(await kernel.request('/api/examples/registry-readout')); }
  catch (error) { status('loadFailed', {message:error.message}, true); }
});
$('welcome-recover').addEventListener('click', async () => {
  try { if (replaceAllowed()) await loadProject(JSON.parse(localStorage.getItem(RECOVERY_KEY))); }
  catch (error) { status('loadFailed', {message:error.message}, true); }
});
$('data-close').addEventListener('click', () => $('data-dialog').close());
$('data-apply').addEventListener('click', () => {
  try {
    const {state:loaded} = adaptWorkspace(JSON.parse($('data-editor').value), state.modules);
    if (edit('updated', () => { state.project = loaded.project; state.blocks = loaded.blocks; state.selectedBlock = null; })) $('data-dialog').close();
  } catch (error) { $('data-error').textContent = error.message; }
});
$('export-button').addEventListener('click', () => { if (state.activeRun?.replay) $('export-dialog').showModal(); });
$('export-close').addEventListener('click', () => $('export-dialog').close());
$('export-json').addEventListener('click', () => { if (state.activeRun?.replay) download(`${state.activeRun.id}.result.json`, JSON.stringify(exportRun(state.activeRun), null, 2)); });
$('export-csv').addEventListener('click', () => { if (state.replay) download(`${state.replay.run.run_id}.metrics.csv`, metricsCSV(state.replay), 'text/csv'); });
window.addEventListener('beforeunload', event => { if (state.project && fingerprint() !== state.saved) { event.preventDefault(); event.returnValue = ''; } });
for (const [id, glyph] of Object.entries({'select-tool':'select','population-tool':'cell','move-tool':'move','rotate-tool':'reset','scale-tool':'scale',
  'snap-tool':'grid','measure-tool':'measure','fit-tool':'fit','objects-tool':'layers','properties-tool':'settings','hand-tool':'hand','orbit-tool':'reset'})) $(id).innerHTML = icon(glyph);
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });

try {
  const [catalogResponse, projectResponse, capabilities] = await Promise.all([fetch('/api/catalog'), fetch('/api/example-project'), kernel.capabilities()]);
  if (!catalogResponse.ok || !projectResponse.ok) throw new Error('Local kernel unavailable');
  const registry = registerCatalog(await catalogResponse.json());
  state.modules = registry.modules; state.objects = registry.objects;
  state.activeObject = availablePlaceables(state.modules, capabilities, state.objects).find(item => item.status === 'ready')?.id ?? null;
  state.capabilities = capabilities;
  state.template = await projectResponse.json();
  try { $('welcome-recover').disabled = !localStorage.getItem(RECOVERY_KEY); } catch { $('welcome-recover').disabled = true; }
  $('welcome-dialog').showModal();
  updateRunButton(); status('ready');
} catch (error) { status('loadFailed', { message: error.message }, true); }
