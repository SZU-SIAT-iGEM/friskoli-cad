import {renderDesignPanel,designRunQueue,designPackagePayload} from './design-panel.mjs';
import { normalizeReplay, cellHistory } from './replay.mjs';
import { registerCatalog, resolveGraph, readableManifest, unavailableModules } from './catalog.mjs';
import { adaptWorkspace } from './migration.mjs';
import { renderModuleDocumentation } from './math-inspector.mjs';
import { blocksFromProject, createBlock, checkBlock } from './population.mjs';
import { readWorkspace, writeWorkspace, validateDesignBrief, validateDesignDocument, draftSnapshot, blankProject, deletePopulation, exportRun, metricsCSV, RECOVERY_KEY } from './workspace.mjs';
import { KernelClient } from './kernel-client.mjs';
import { TaskStore, preflightSubmission, matchesDraft, supportsTasks, taskCapability, executionStepLimit } from './task-store.mjs';
import { renderResultData } from './results.mjs';
import { compareRuns, METRIC_KEYS, csvCell } from './metrics.mjs';
import { copyPopulationBranch } from './templates.mjs';
import { renderComparison } from './metric-results.mjs';
import { installDockSizing } from './panels.mjs';
import { SpatialViewport, canTransformBlock, canTransformEnvironment } from './scene3d.mjs';
import { GraphEditor, autoLayout } from './workflow.mjs';
import { moduleName } from './workflow-components.mjs';
import { addNode, connect, connectionProblem, disconnect, missingInputs, missingParameters, preferredTiming, removeNode,
  setParameter } from './graph-edit.mjs';
import { applyLanguage, currentLanguage, setLanguage, t } from './i18n.mjs';
import { availablePlaceables, objectForBlock, initializeObject, environmentObjects,
  initializeEnvironmentObject, deleteEnvironmentObject, transformEnvironmentObject, roleProviders, requiredRoleRemovalProblem, applyRegisteredDefaults } from './placeables.mjs';
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
  frameIndex: 0, selectedBlock: null, selectedCell: null, selectedEnvironment: null, graphSelection: null, selectedManifest: null,
  field: '', slice: 0, ranges: {}, timer: null, status: ['ready', {}, false], layout: {},
  history: [], future: [], tool: 'select', snap: false, settings: {dt_s: .5, steps: 8},
  revision: 0, draftToken: crypto.randomUUID(), latestTask: null, saved: '', busy: false, checks: [], runs: [], activeRun: null, template: null, capabilities: null,
  assemblyDraft: null, assemblyBusy: false, assemblyError: '', assemblyNotice: '', assemblyToken: 0 };
const kernel = new KernelClient();
let taskStorage = null;
try { taskStorage = localStorage; } catch { /* display unavailable recovery in the task panel */ }
const taskStore = new TaskStore(kernel, {storage:taskStorage, maxRecords:32, onChange:taskUpdated, isProtected:record => Boolean(record.design_ref) || state.comparison?.has(record.id) || state.batch?.records.has(record.localId)});
state.objects = new Map();
state.registries = new Map();
state.activeObject = null;
state.comparison = new Set();
const isSpatial = project => ['spatial-unbiased-v1','chemotaxis-spatial-v1'].includes(project?.execution_profile);
const stepLimit = () => executionStepLimit(state.capabilities, state.project);
const TOOLS = { select: 'select-tool', population: 'population-tool', move: 'move-tool', scale: 'scale-tool',
  rotate: 'rotate-tool', hand: 'hand-tool', orbit: 'orbit-tool', measure: 'measure-tool' };
let viewport;
installDockSizing($('studio'), $('diagnostics'));

// Undo keeps whole-document snapshots; projects are small enough that this stays cheap.
const snapshot = () => structuredClone({ project: state.project, blocks: state.blocks, layout: state.layout, settings: state.settings, design:state.design, designBrief:state.designBrief });
const fingerprint = () => JSON.stringify(snapshot());
function changed() {
  clearDesignEvaluation();
  state.assemblyToken = (state.assemblyToken ?? 0) + 1;
  // A project edit invalidates an in-flight assembly request. Release its UI lock;
  // the request's completion handler will discard the stale response.
  state.assemblyBusy = false;
  state.revision++;
  state.checks = [];
  try { localStorage.setItem(RECOVERY_KEY, JSON.stringify(writeWorkspace(state))); }
  catch { status('recoveryFailed', {}, true); }
}
function clearDesignEvaluation(){state.designEvaluation=null;state.designEvaluationToken=(state.designEvaluationToken??0)+1;}
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
  if (!graph.nodes.some(node => node.id === state.selectedEnvironment)) state.selectedEnvironment = null;
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

// Transform tools require an editable Space selection; other editing tools can switch to Space.
function selectedBlockTransformable() {
  const block = state.blocks.find(item => item.id === state.selectedBlock);
  return canTransformBlock(block, state.view) && Boolean(objectForBlock(block, state.objects));
}

function selectedEnvironmentObject() {
  return visibleEnvironment().find(item => item.id === state.selectedEnvironment);
}

function selectedTransformable(tool) {
  return state.selectedEnvironment ? canTransformEnvironment(selectedEnvironmentObject(),state.view,tool) : selectedBlockTransformable();
}

function updateTransformTools() {
  for (const tool of ['move','scale','rotate']) $(`${tool}-tool`).disabled = !selectedTransformable(tool);
  $('rotate-tool').title = t(state.selectedEnvironment ? 'environmentRotationUnsupported' : 'rotate');
  if (['move','scale','rotate'].includes(state.tool) && !selectedTransformable(state.tool)) useTool('select');
}

function useTool(tool) {
  if (!state.project) return;
  if (['move','scale','rotate'].includes(tool) && !selectedTransformable(tool)) {
    if (tool === 'rotate' && state.selectedEnvironment) status('environmentRotationUnsupported',{},true);
    return;
  }
  if (tool === 'population' && !availablePlaceables(state.modules,state.capabilities,state.objects,state.project).some(item => item.kind === 'population' && item.status === 'ready')) {
    status('populationAdapterMissing',{},true); return;
  }
  if (tool !== 'select' && state.view !== 'space') setView('space');
  state.tool = tool;
  viewport?.setTool(tool);
  for (const [name, id] of Object.entries(TOOLS)) $(id).classList.toggle('active', name === tool);
  if (tool === 'measure') status('measureHint');
  if (state.selectedEnvironment && ['move','scale'].includes(tool)) {
    const object=selectedEnvironmentObject();
    if (object?.kind !== 'local_source') status('environmentBoxGridSnap');
    else if (tool === 'scale') status('sourceUniformScale');
  }
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

function visibleProject() {
  return state.view === 'results' ? state.activeRun?.submission?.project ?? state.activeRun?.project ?? null : state.project;
}
function visibleEnvironment() {
  const project = visibleProject();
  if (!project) return [];
  const registry = state.registries.get(project.execution_profile ?? 'legacy-explicit-v1');
  return environmentObjects(project,registry?.objects ?? state.objects,registry?.modules ?? state.modules,
    state.view === 'results' ? currentSnapshot()?.object_states ?? {} : null);
}

function renderScene() {
  if (!state.project) return;
  updateTransformTools();
  renderFieldControls();
  const snapshot = currentSnapshot();
  const domain = state.view === 'results' ? state.replay?.domain ?? state.project.domain : state.project.domain;
  viewport?.setDomain(domain);
  viewport?.setSnap(state.snap);
  viewport?.setMode(state.view);
  if (!['workflow','design'].includes(state.view)) {
    viewport?.setSnapshot(snapshot ?? {frame:{cells:[]},concentrations:{}}, state.view === 'results' ? state.selectedCell : null,
      state.view === 'results' ? state.field : '', state.slice, state.ranges[state.field]);
    viewport?.setBlocks(state.blocks.map(block => objectForBlock(block,state.objects) ? block : {...block,locked:true}), state.selectedBlock);
    viewport?.setObjects(visibleEnvironment(),state.selectedEnvironment);
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
  for (const [index,snapshot] of replay.snapshots.entries()) {
    const tick = el('button', `event-tick${snapshot.frame.events.length ? ' has-event' : ''}${index === state.frameIndex ? ' current' : ''}`,
      snapshot.frame.events.length ? '◆' : '·');
    tick.type = 'button';
    tick.title = `${fmt(snapshot.frame.time_s, 3)} s · ${snapshot.frame.events.length} events`;
    tick.addEventListener('click', () => setFrame(index));
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
  state.selectedEnvironment = null;
  state.selectedCell = null;
  renderLeft();
  renderInspector();
  renderScene();
  closeDocks();
}

function selectCell(id) {
  state.selectedCell = id;
  state.selectedEnvironment = null;
  renderLeft();
  renderInspector();
  renderScene();
  renderData();
  closeDocks();
}

function selectEnvironment(id) {
  state.selectedEnvironment = id; state.selectedBlock = null; state.selectedCell = null;
  renderLeft(); renderInspector(); renderScene(); closeDocks();
}

function renderEnvironmentRows(container,filter) {
  for (const object of visibleEnvironment()) {
    if (filter && !`${object.id} ${object.declaration.label}`.toLowerCase().includes(filter)) continue;
    treeRow(container,t(object.declaration.label),object.id,state.selectedEnvironment === object.id,
      () => selectEnvironment(object.id),object.kind !== 'local_source' ? '▧' : '◉');
  }
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
      if (!run.replay) { showBottom('runs'); renderDiagnostics(); return; }
      selectRun(run);
    }, '◷');
    renderEnvironmentRows(container,filter);
    for (const cell of (currentSnapshot()?.frame.cells ?? []).filter(c => !filter || c.id.toLowerCase().includes(filter)).slice(0, 100)) {
      treeRow(container, cell.id, cell.group_id, state.selectedCell === cell.id, () => selectCell(cell.id), '·');
    }
    return;
  }
  if (state.left === 'modules') {
    container.append(el('div', 'tree-heading', `${t('compiled')} · ${state.modules.size}`));
    for (const [key, manifest] of state.modules) {
      if (filter && !`${key} ${manifest.description}`.toLowerCase().includes(filter)) continue;
      treeRow(container, moduleName(manifest,currentLanguage()), key, state.selectedManifest === key,
        () => { state.selectedManifest = key; renderLeft(); renderInspector(); closeDocks(); }, '⬡');
    }
    return;
  }
  if (state.view === 'workflow') {
    renderTemplateSummary(container);
    container.append(el('div', 'tree-heading', `${t('workflow')} · ${state.project.graph.nodes.length}`));
    for (const node of state.project.graph.nodes) {
      if (filter && !`${node.id} ${node.module_id}`.toLowerCase().includes(filter)) continue;
      const active = state.graphSelection?.kind === 'node' && state.graphSelection.id === node.id;
      const row = treeRow(container, moduleName(state.modules.get(moduleKey(node))??{id:node.module_id},currentLanguage()), node.id, active,
        () => { selectGraph({ kind: 'node', id: node.id }); closeDocks(); }, '⬡');
      row.addEventListener('contextmenu', event => {
        event.preventDefault(); showGraphContext({ kind: 'node', id: node.id }, event.clientX, event.clientY);
      });
    }
    return;
  }
  if (state.view === 'space') {
    container.append(el('div', 'tree-heading', t('library')));
    const speciesRow=el('label','edit-row',t('sourceSpecies')),species=el('select');
    species.setAttribute('aria-label',t('sourceSpecies'));species.append(new Option(t('chooseSourceSpecies'),''));
    for(const id of Object.keys(state.project.species))species.append(new Option(id,id));
    if(Object.keys(state.project.species).length===1)state.activeSpecies=Object.keys(state.project.species)[0];
    species.value=state.activeSpecies??'';species.addEventListener('change',()=>{state.activeSpecies=species.value;});speciesRow.append(species);container.append(speciesRow);
    for (const item of availablePlaceables(state.modules, state.capabilities, state.objects,state.project)) {
      const ready = item.status === 'ready';
      const row = el('button', `tree-row library-row${ready ? '' : ' unavailable'}${ready && state.tool === item.tool && state.activeObject === item.id ? ' active' : ''}`);
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
  treeRow(container, state.project.id, t('domain'), !state.selectedBlock && !state.selectedEnvironment, () => { state.selectedBlock = null; state.selectedEnvironment = null; renderInspector(); renderLeft(); renderScene(); });
  renderEnvironmentRows(container,filter);
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

function renderTemplateSummary(container) {
  const registry = state.registries.get(state.project.execution_profile ?? 'legacy-explicit-v1');
  if (!registry?.templates?.length) return;
  const details = el('details','template-overview'); details.open=true;
  details.append(el('summary','',t('templates')));
  const active = registry.templates.find(item => item.example_id === state.project.id || item.id === state.project.id);
  if (active) details.append(el('strong','',active.label),el('p','empty-message',active.description));
  details.append(el('p','empty-message',t('templateEditable')));
  const select = el('select'); select.setAttribute('aria-label',t('replaceTemplate'));
  for (const item of registry.templates) select.append(new Option(`${item.label} · ${item.id}`,item.example_id));
  if (active) select.value=active.example_id;
  const replace = el('button','inspector-action',t('replaceTemplate'));
  replace.addEventListener('click',async()=>{
    if (!replaceAllowed()) return;
    const revision=state.revision,draft=state.draftToken; replace.disabled=true;
    try {
      const document=await kernel.request(`/api/examples/${encodeURIComponent(select.value)}`);
      if (revision!==state.revision || draft!==state.draftToken) return;
      await loadProject(document); setView('workflow');
    } catch(error) { status('loadFailed',{message:error.message},true); }
    finally { replace.disabled=false; }
  }); details.append(select,replace);container.append(details);
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
  if (!objectForBlock(block,state.objects)) {
    root.append(el('p','pending-note',t('populationAdapterMissing')));
    renderExistingCells(root,block);
    const remove = el('button','inspector-action danger',t('delete'));
    remove.addEventListener('click',() => removeBlock(block.id)); root.append(remove);
    return;
  }
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
  if (isSpatial(state.project) && !block.dirty) {
    const part = section(root,t('copyBranch'));
    const source = el('select'); source.setAttribute('aria-label',t('sourcePopulation'));
    for (const candidate of state.blocks.filter(b => b.id !== block.id && state.project.groups[b.id])) source.append(new Option(`${candidate.name} · ${candidate.id}`,candidate.id));
    const button = el('button','inspector-action',t('copyBranch'));
    button.disabled = !source.options.length || block.locked;
    button.addEventListener('click',() => edit('updated',() => {
      block.binding = {data_nodes:copyPopulationBranch(state.project,source.value,block.id)};
      state.layout = autoLayout(state.project.graph,state.modules,state.layout);
    }));
    part.append(el('p','empty-message',t('copyBranchHint')),source,button);
  }
  if (block.dirty) root.append(el('div', 'pending-note', t('pending')));
}

function renderExistingCells(root,block) {
  const group = state.project.groups[block.id];
  if (!group) return;
  group.ids.forEach((id,index) => {
    const part = el('details','property-section');
    part.append(el('summary','',id)); root.append(part);
    const field = (label,value,write) => {
      const row = el('label','edit-row',label), input = el('input');
      input.type = 'number'; input.step = 'any'; input.value = value; input.setAttribute('aria-label',`${id} ${label}`);
      input.disabled = Boolean(block.locked);
      input.addEventListener('change',() => edit('updated',() => {
        const value = Number(input.value);
        if (input.value === '' || !Number.isFinite(value)) throw new Error('Invalid cell data');
        write(state.project.groups[block.id],value);
        const fresh = blocksFromProject(state.project).find(item => item.id === block.id);
        if (fresh) Object.assign(block,{center:fresh.center,size:fresh.size,length:fresh.length,diameter:fresh.diameter,dirty:false});
      })); row.append(input); part.append(row);
    };
    for (let axis=0;axis<3;axis++) field(`${'XYZ'[axis]} · µm`,group.positions_um[index][axis],(target,value) => {
      const extent = state.project.domain.counts_xyz[axis]*state.project.domain.spacing_um_xyz[axis];
      if (value < 0 || value > extent) throw new Error('Cells outside domain');
      target.positions_um[index][axis] = value;
    });
    for (const name of ['length_um','diameter_um']) if (group.initial_geometry?.[index]) {
      field(t(name === 'length_um' ? 'length' : 'diameter')+' · µm',group.initial_geometry[index][name],(target,value) => {
        const geometry = target.initial_geometry[index]; geometry[name] = value;
        if (geometry.diameter_um <= 0 || geometry.length_um < geometry.diameter_um) throw new Error('Invalid cell geometry');
      });
    }
  });
}

function renderEnvironmentInspector(root,object) {
  const project = visibleProject(), registry = state.registries.get(project.execution_profile ?? 'legacy-explicit-v1');
  const manifest = (registry?.modules ?? state.modules).get(object.declaration.initializer.module);
  root.append(el('div','inspector-title',object.id),el('div','inspector-subtitle',t(object.declaration.label)));
  root.append(el('p','empty-message',t(object.kind === 'obstacle_box' ? 'obstacleScope' : object.kind === 'degradable_box' ? 'materialScope' : 'sourceScope')));
  root.append(el('p','empty-message',t('environmentRotationUnsupported')));
  root.append(el('p','empty-message',t(object.kind === 'local_source' ? 'sourceUniformScale' : 'environmentBoxGridSnap')));
  const part = section(root,t('parameters'));
  if (state.view === 'results' && ['degradable_box','local_source'].includes(object.kind)) {
    kv(part,t('remainingNutrient'),object.remaining_molecules === null ? t('objectStateUnavailable') : `${fmt(object.remaining_molecules,4)} molecule`);
    if (object.kind === 'degradable_box' && object.remaining_molecules === 0) root.append(el('p','empty-message',t('materialExhausted')));
  }
  for (const property of object.declaration.properties) {
    const definition = manifest.parameters[property.path];
    if (!definition) continue;
    if (state.view === 'results') kv(part,t(property.label),`${object.node.parameters[property.path]?.value ?? '—'} ${property.unit === '1' ? '' : property.unit ?? ''}`);
    else parameterField(part,object.node,manifest,property.path,{...definition,label:t(property.label)});
  }
  for (const requirement of object.declaration.initializer.requirements ?? []) {
    const providers = roleProviders(project,registry?.modules ?? state.modules,requirement);
    kv(part,requirement.role,providers.map(node => node.id).join(', ') || t('missing'));
  }
  if (state.view === 'results') { root.append(el('p','empty-message',t('frozenObject'))); return; }
  const graph = el('button','inspector-action',t('workflow'));
  graph.addEventListener('click',() => { setView('workflow'); selectGraph({kind:'node',id:object.id}); }); root.append(graph);
  const remove = el('button','inspector-action danger',t('deleteObject'));
  remove.addEventListener('click',() => removeEnvironment(object.id)); root.append(remove);
}

function removeEnvironment(id) {
  edit('nodeRemoved',() => {
    deleteEnvironmentObject(state.project,id); delete state.layout[id]; state.selectedEnvironment = null;
  },{id});
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
  let seed = null;
  if (isSpatial(state.project)) {
    seed = el('input'); seed.type = 'number'; seed.min = '0'; seed.max = String(Number.MAX_SAFE_INTEGER); seed.step = '1'; seed.required = true;
    seed.value = state.settings.seed ?? state.project.random_seed; seed.setAttribute('aria-label',t('randomSeed'));
    const row = el('label','edit-row',t('randomSeed')); row.append(seed); form.append(row);
    const outputRow = el('label','edit-row',t('includeFields')), output = el('input');
    output.type = 'checkbox'; output.checked = state.settings.include_fields !== false;
    output.addEventListener('change',() => edit('updated',() => { state.settings.include_fields = output.checked; }));
    outputRow.append(output); part.append(outputRow);
    if (state.project.project_version === '0.5.0') {
      const row = el('label','edit-row',t('frameInterval')), input = el('input'); input.type='number'; input.min='1'; input.step='1'; input.value=state.settings.frame_every_steps ?? 1;
      input.addEventListener('change',()=>edit('updated',()=>{
        const value=Number(input.value); if (!Number.isSafeInteger(value)||value<1||value>10000) throw new Error('Invalid frame interval'); state.settings.frame_every_steps=value;
      })); row.append(input);part.append(row);
    }
  }
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
      if (seed) {
        const value = Number(seed.value);
        if (!Number.isSafeInteger(value) || value < 0 || seed.value === '') throw new Error('Invalid random seed');
        state.project.random_seed = value;
        delete state.settings.seed;
      }
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
  if (state.project.observation) {
    const observation = section(root,t('observation'));
    observation.append(el('p','empty-message',t('observationHint')));
    observation.append(el('pre','parameter-record',JSON.stringify(state.project.observation,null,2)));
    const editor=el('details','parameter-evidence');editor.append(el('summary','',t('editObservation')));
    const form=el('form'),axis=el('select');axis.setAttribute('aria-label',t('observationAxis'));
    for(let i=0;i<3;i++)axis.append(new Option('XYZ'[i],String(i)));axis.value=String(state.project.observation.axis);
    const axisRow=el('label','edit-row',t('observationAxis'));axisRow.append(axis);form.append(axisRow);
    const bounds={};
    for(const key of ['region_lower_um','region_upper_um']){
      bounds[key]=[];
      for(let i=0;i<3;i++){const row=el('label','edit-row',`${t(key)} ${'XYZ'[i]} [µm]`),input=el('input');input.type='number';input.step='any';input.required=true;input.value=state.project.observation[key][i];row.append(input);form.append(row);bounds[key].push(input);}
    }
    const apply=el('button','inspector-action',t('apply'));apply.type='submit';form.append(apply);
    form.addEventListener('submit',event=>{event.preventDefault();edit('updated',()=>{
      const lower=bounds.region_lower_um.map(input=>Number(input.value)),upper=bounds.region_upper_um.map(input=>Number(input.value)),size=state.project.domain.counts_xyz.map((n,i)=>n*state.project.domain.spacing_um_xyz[i]);
      if(lower.some((v,i)=>!Number.isFinite(v)||!Number.isFinite(upper[i])||v<0||v>=upper[i]||upper[i]>size[i]))throw new Error(t('invalidObservation'));
      Object.assign(state.project.observation,{axis:Number(axis.value),region_lower_um:lower,region_upper_um:upper});
    });});editor.append(form);observation.append(editor);
    const seeds=el('input');seeds.type='text';seeds.value=state.seedList??'0, 1, 2';seeds.setAttribute('aria-label',t('seedList'));
    seeds.addEventListener('input',()=>{state.seedList=seeds.value;});
    const label=el('label','edit-row',t('seedList'));label.append(seeds);
    const run=el('button','inspector-action',t('runSeeds'));run.disabled=Boolean(state.batch);
    run.addEventListener('click',()=>startSeedBatch(seeds.value));
    observation.append(label,el('p','empty-message',t('seedListHint')),run);
  }
}

function duplicateBlock(id) {
  const source = state.blocks.find(b => b.id === id);
  if (!source || source.locked || !objectForBlock(source,state.objects)) return;
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
  for (const run of [...state.runs].reverse()) {
    const row = el('li', 'task-record');
    row.append(el('strong', '', `${run.project.id} · ${run.id}`));
    if(run.design_ref)row.append(el('span','task-note',`${run.design_ref.candidate_name} · ${run.design_ref.candidate_id} · ${run.design_ref.design_id}`));
    const progress = run.task ? ` · ${run.task.progress.committed_step}/${run.settings.steps} ${t('steps')}` : '';
    row.append(el('span', '', `${t(run.status)}${progress}${run.localId ? ' · ' + t(run.completeness) : ''}`));
    if (run.task?.cancel_requested && run.status === 'running') row.append(el('span', 'task-note', t('cancelRequested')));
    if (run.localId) {
      row.append(el('span', 'task-note', t('taskDraft', {revision:run.submission.edit_revision})));
      if (!run.eventsComplete) row.append(el('span', 'task-note', t('eventsExpired')));
      if (run.connection === 'lost') row.append(el('span', 'task-note', t(run.paused ? 'queryPaused' : 'connectionRetry')));
    }
    if (run.status==='completed' && run.replay?.snapshots.at(-1)?.metrics) {
      const label=el('label','comparison-choice',t('includeComparison')),checkbox=el('input');checkbox.type='checkbox';checkbox.checked=state.comparison.has(run.id);
      checkbox.addEventListener('change',()=>{checkbox.checked?state.comparison.add(run.id):state.comparison.delete(run.id);renderComparisons();});label.prepend(checkbox);row.append(label);
    }
    for (const issue of run.task?.issues ?? []) row.append(el('span','error',`${issue.code}: ${issue.message}`));
    if (run.error) row.append(el('span', 'error', run.error === 'task.idempotency_window_expired' ? t('retryExpired') : run.error));
    const actions = el('div', 'task-actions');
    const action = (label, fn) => { const button = el('button', 'menu-button', t(label)); button.type = 'button'; button.addEventListener('click', fn); actions.append(button); };
    if (run.replay) action('viewPublished', () => selectRun(run));
    if (run.localId && !run.imported) {
      action('inputSnapshot', () => { $('task-input-json').value = JSON.stringify(run.submission, null, 2); $('task-input-dialog').showModal(); });
      if (['queued', 'running'].includes(run.status) && !run.task?.cancel_requested) action('cancelTask', () => taskStore.cancel(run.localId));
      if (run.paused && run.status !== 'unavailable' && run.error !== 'task.idempotency_window_expired')
        action(run.runId ? 'retryQuery' : 'retrySubmission', () => taskStore.retry(run.localId));
    }
    row.append(actions); runs.append(row);
  }
  $('task-recovery-warning').hidden = !taskStore.persistenceError && Boolean(taskStorage);
  $('task-output-note').hidden = !supportsTasks(state.capabilities, state.project);
  $('task-output-note').textContent = t(isSpatial(state.project) ? 'spatialTaskOutputScope' : 'taskOutputScope');
  renderComparisons();renderData();
}

function renderComparisons() {
  let comparison=$('comparison-results');
  if(!comparison){comparison=el('section','comparison-results');comparison.id='comparison-results';$('runs-pane').append(comparison);}
  comparison.replaceChildren();
  if(state.batch){const cancel=el('button','menu-button',t('stopSeedBatch'));cancel.addEventListener('click',()=>{const active=state.batch?.active;state.batch=null;if(active)taskStore.cancel(active);renderDiagnostics();});comparison.append(cancel);}
  const selected=state.runs.filter(run=>state.comparison.has(run.id));
  if(selected.length){
    const heading=el('h3','',t('comparison'));comparison.append(heading);
    try{
      const variants=compareRuns(selected),output=el('div','result-data');renderComparison(output,variants,t);comparison.append(output);
      const exportButton=el('button','menu-button',t('exportComparison'));
      exportButton.addEventListener('click',()=>{
        const rows=[['variant','version_lock','run_id','seed','group_id','statistic','n',...METRIC_KEYS]];
        for(const variant of variants){
          for(const run of variant.runs)for(const [group,values] of Object.entries(run.replay.snapshots.at(-1).metrics.by_group))rows.push([variant.label,JSON.stringify(variant.versionLock??{kind:'legacy-sync'}),run.id,run.submission?.execution.seed??run.project.random_seed,group,'run',1,...METRIC_KEYS.map(key=>values[key]??'')]);
          for(const [group,stats] of Object.entries(variant.byGroup))for(const kind of ['mean','sd'])rows.push([variant.label,JSON.stringify(variant.versionLock??{kind:'legacy-sync'}),'',variant.seeds.join(' '),group,kind,variant.runs.length,...METRIC_KEYS.map(key=>stats[key][kind]??'')]);
        }
        download('comparison.csv',rows.map(row=>row.map(csvCell).join(',')).join('\n')+'\n','text/csv');
      });comparison.prepend(exportButton);
    }catch(error){comparison.append(el('p','task-note',t(error.message)));}
  }
}

function selectRun(record) {
  if (!record.replay) return;
  state.activeRun = record; state.replay = record.replay; state.frameIndex = 0; state.selectedCell = null; state.selectedEnvironment = null;
  state.field = Object.keys(record.replay.snapshots[0]?.concentrations ?? {})[0] ?? '';
  updateRanges(); setView('results');
}

function taskUpdated(record, records) {
  const previous = state.runs.find(run => run.localId === record.localId);
  if(record.design_ref?.design_id===state.design?.id)clearDesignEvaluation();
  state.runs = [...state.runs.filter(run => !run.localId || run.imported), ...records];
  if(state.batch?.active===record.localId && ['completed','failed','cancelled','interrupted','rejected','unavailable'].includes(record.status)) {
    const batch=state.batch;batch.active=null;
    if(record.status==='completed')queueMicrotask(()=>nextSeed(batch));
    else {state.designBatchStatus=record.status;state.batch=null;}
  }
  if (record.localId === state.latestTask && matchesDraft(record, state) && record.status !== previous?.status)
    status(record.status, {}, ['failed', 'rejected', 'unavailable'].includes(record.status));
  if (state.activeRun?.localId === record.localId) {
    state.activeRun = record;
    if (record.replay) { state.replay = record.replay; state.frameIndex = Math.min(state.frameIndex, record.replay.snapshots.length - 1); updateRanges(); }
  }
  if (record.replay && record.replay !== previous?.replay && record.status === 'completed' &&
      record.localId === state.latestTask && matchesDraft(record, state) && !record.design_ref) selectRun(record);
  else if (state.view === 'results' && state.activeRun?.localId === record.localId) renderAll();
  else { updateRunButton(); renderDiagnostics(); }
  if(state.view==='design')renderDesign();
}

function renderData() { renderResultData($('data-pane'), state.replay, state.selectedCell, setFrame,
  {empty:t('noResults'),cells:t('cells'),events:t('events'),frame:t('frame'),curve:t('cellCountHistory')},
  {t,project:state.activeRun?.project,definitions:state.registries.get(state.activeRun?.project?.execution_profile)?.observations??[]}); }

function startSeedBatch(text) {
  if(state.batch || !state.project || $('run-button').disabled || !supportsTasks(state.capabilities,state.project))return;
  const seeds=text.split(/[\s,，]+/).filter(Boolean).map(Number);
  if(!seeds.length||seeds.length>8||seeds.some(seed=>!Number.isSafeInteger(seed)||seed<0)||new Set(seeds).size!==seeds.length){status('invalidSeeds',{},true);return;}
  try { taskStore.assertCapacity(seeds.length); } catch(error) { status('runFailed',{message:t(error.code)},true); return; }
  const batch={seeds,records:new Set(),project:structuredClone(state.project),settings:structuredClone(state.settings),draftToken:state.draftToken,revision:state.revision,active:null};
  state.batch=batch;nextSeed(batch);
}
async function nextSeed(batch) {
  if(state.batch!==batch)return;
  const entry=batch.queue?.shift();
  const seed=batch.queue ? entry?.seed : batch.seeds.shift();if(seed===undefined){state.batch=null;state.designBatchStatus='Completed';renderDiagnostics();if(state.view==='design')renderDesign();return;}
  try {
    const {submission}=await preflightSubmission(kernel,state.capabilities,entry?.project??batch.project,{...batch.settings,seed},`${batch.draftToken}:${batch.revision}`);
    if(state.batch!==batch)return;
    const record=taskStore.create(submission,{draftToken:batch.draftToken,revision:batch.revision,design_ref:entry?.design_ref,idempotencyRetentionSeconds:taskCapability(state.capabilities,entry?.project??batch.project).limits.idempotency_retention_seconds});
    batch.records.add(record.localId);batch.active=record.localId;state.latestTask=record.localId;showBottom('runs');renderDiagnostics();await taskStore.submit(record.localId);taskStore.start();
  }catch(error){state.batch=null;state.designBatchStatus=error.message;status('runFailed',{message:t(error.code??error.message)},true);renderDiagnostics();}
}

async function designRequest(path,body,kind='json') {
  const response=await fetch('/api/design/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.message??error.error?.message??error.issues?.[0]?.message??JSON.stringify(error));}
  return kind==='blob'?response.blob():kind==='text'?response.text():response.json();
}
async function assemblyRequest(path,body) {
  const response=await fetch('/api/assemblies/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){const error=await response.json().catch(()=>({}));throw new Error(error.message??error.error?.message??error.issues?.[0]?.message??JSON.stringify(error));}
  return response.json();
}
function downloadBlob(name,blob){const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function renderDesign(){renderDesignPanel($('design-view'),state,{
  render:renderDesign,changed, error:error=>{state.designError=error.message;renderDesign();},
  async generate(brief){
    if(state.batch)return;
    const draftToken=state.draftToken,revision=state.revision,requestBrief=structuredClone(brief);
    validateDesignBrief(requestBrief);
    if(state.runs.some(r=>r.design_ref?.design_id===requestBrief.id)||(state.design?.id===requestBrief.id&&JSON.stringify(state.design.brief)!==JSON.stringify(requestBrief))){
      requestBrief.id='design-'+crypto.randomUUID();state.designNotice=currentLanguage()==='zh-CN'?'已分配新的设计 ID，原运行仍保留。':'A new design ID was assigned; earlier runs are retained.';
    }
    state.designBusy=true;state.designError='';renderDesign();
    try{
      const design=await designRequest('generate',{project:structuredClone(state.project),settings:structuredClone(state.settings),brief:requestBrief});
      if(state.draftToken!==draftToken||state.revision!==revision)throw new Error(currentLanguage()==='zh-CN'?'草稿已修改，未应用迟到的候选结果。':'The draft changed; generated candidates were not applied.');
      validateDesignDocument(design);state.design=design;state.designBrief=structuredClone(design.brief);state.designBatchStatus='';state.designImported=false;changed();
    }finally{state.designBusy=false;renderDesign();}

  },
  run(){
    if(state.batch)return;const queue=designRunQueue(state.design);taskStore.assertCapacity(queue.length);
    if(state.runs.some(r=>r.design_ref?.design_id===state.design.id))throw new Error('This design already has run history. Generate a new design ID to run again.');
    const batch={queue,designId:state.design.id,records:new Set(),settings:structuredClone(state.design.settings),draftToken:state.draftToken,revision:state.revision,active:null};
    state.batch=batch;state.designBatchStatus='Running';nextSeed(batch);renderDesign();
  },
  stop(){const active=state.batch?.active;state.batch=null;state.designBatchStatus='Stopped; published results retained';if(active)taskStore.cancel(active);renderDesign();},
  compare(){state.comparison.clear();for(const run of state.runs.filter(r=>r.design_ref?.design_id===state.design.id&&r.status==='completed'&&r.replay))state.comparison.add(run.id);showBottom('runs');renderDiagnostics();},
  async evaluate(){
    if(state.batch||state.designEvaluating||!state.design)return;
    clearDesignEvaluation();const token=state.designEvaluationToken,id=state.design.id;state.designEvaluating=true;state.designError='';renderDesign();
    try{
      const registry=await fetch('/api/catalog?execution_profile='+encodeURIComponent(state.design.baseline_project.execution_profile)).then(r=>{if(!r.ok)throw new Error('Catalog unavailable');return r.json();});
      if(token!==state.designEvaluationToken||id!==state.design?.id)return;
      const evaluation=await designRequest('evaluate',designPackagePayload(state,writeWorkspace(state),registry));
      if(token===state.designEvaluationToken&&id===state.design?.id)state.designEvaluation=evaluation;
    }finally{state.designEvaluating=false;renderDesign();}
  },
  async exportAssembly({groupId,metadata}){
    if(state.assemblyBusy||!state.project)return;
    const token=state.assemblyToken,draftToken=state.draftToken,revision=state.revision;
    state.assemblyBusy=true;state.assemblyError='';state.assemblyNotice='';renderDesign();
    try{
      const assembly=await assemblyRequest('extract',{project:structuredClone(state.project),group_id:groupId,metadata:structuredClone(metadata)});
      if(token!==state.assemblyToken||draftToken!==state.draftToken||revision!==state.revision)return;
      download(`${metadata.id}.friskoli-assembly.json`,JSON.stringify(assembly,null,2),'application/json');
      state.assemblyNotice=currentLanguage()==='zh-CN'?`已导出 ${metadata.name}；assembly 保留菌群机制、模块版本和环境依赖。`:`Exported ${metadata.name}; the assembly includes its mechanism, module versions and environment dependencies.`;
    }catch(error){
      if(token===state.assemblyToken&&draftToken===state.draftToken){state.assemblyError=error.message;renderDesign();}
    }finally{if(token===state.assemblyToken&&draftToken===state.draftToken){state.assemblyBusy=false;renderDesign();}}
  },
  async importAssembly(file,groupId){
    if(state.assemblyBusy||!state.project)return;
    const token=state.assemblyToken,draftToken=state.draftToken,revision=state.revision;
    state.assemblyBusy=true;state.assemblyError='';state.assemblyNotice='';renderDesign();
    try{
      if(file.size>10*1024*1024)throw new Error('Assembly file exceeds 10 MiB');
      const assembly=JSON.parse(await file.text());
      const result=await assemblyRequest('apply',{project:structuredClone(state.project),group_id:groupId,assembly,bindings:null});
      if(token!==state.assemblyToken||draftToken!==state.draftToken||revision!==state.revision)return;
      const loaded=readWorkspace(result);
      state.assemblyBusy=false;
      edit('updated',()=>{
        state.project=loaded.project;state.blocks=loaded.blocks;state.layout=autoLayout(state.project.graph,state.modules,state.layout);
        state.selectedBlock=groupId;state.selectedEnvironment=null;state.graphSelection=null;state.replay=null;state.activeRun=null;
        state.design=null;state.designBrief=null;state.designImported=false;state.designBatchStatus='';state.designNotice='';state.designError='';
        state.draftToken=crypto.randomUUID();
      });
      state.assemblyNotice=currentLanguage()==='zh-CN'?`已应用 ${assembly.name} 到 ${groupId}；原有空间位置和其他菌群保持不变。`:`Applied ${assembly.name} to ${groupId}; positions and other populations were preserved.`;
      renderDesign();
    }catch(error){
      if(token===state.assemblyToken&&draftToken===state.draftToken){state.assemblyError=error.message;renderDesign();}
    }finally{if(token===state.assemblyToken&&draftToken===state.draftToken){state.assemblyBusy=false;renderDesign();}}
  },
  view:selectRun,
  async open(candidate){const design=state.design,brief=state.designBrief;await loadProject(candidate.project);state.design=design;state.designBrief=brief;changed();setView('workflow');},
  async export(format){
    const registry=await fetch('/api/catalog?execution_profile='+encodeURIComponent(state.design.baseline_project.execution_profile)).then(r=>r.json());
    const payload=designPackagePayload(state,writeWorkspace(state),registry);
    if(format==='package'){downloadBlob(state.design.id+'.friskoli',await designRequest('export',payload,'blob'));state.exportedDesigns??=new Set();state.exportedDesigns.add(state.design.id);renderDesign();}
    else downloadBlob(state.design.id+'.'+format,new Blob([await designRequest('report',{payload,format},'text')],{type:format==='html'?'text/html':'text/csv'}));
  },
  clear(){
    const id=state.design.id;
    if(!state.exportedDesigns?.has(id))throw new Error(currentLanguage()==='zh-CN'?'请先导出 .friskoli 设计包。':'Export the .friskoli package first.');
    if(!confirm(currentLanguage()==='zh-CN'?'从本机历史清除此设计的已结束运行？导出的包保留完整记录。':'Remove this design’s finished runs from local history? The exported package keeps the records.'))return;
    const records=taskStore.removeDesignRecords(id);state.runs=[...state.runs.filter(r=>(!r.localId||r.imported)&&r.design_ref?.design_id!==id),...records];state.comparison.clear();state.designBatchStatus='';clearDesignEvaluation();renderDesign();renderDiagnostics();
  },
  import(){
    const input=el('input');input.type='file';input.accept='.friskoli';input.addEventListener('change',async()=>{
      const file=input.files[0];if(!file)return;try{
        if(file.size>100*1024*1024)throw new Error('Package exceeds 100 MiB');
        const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
        const payload=await designRequest('import',{archive_base64:btoa(binary)});
        if(state.project&&fingerprint()!==state.saved&&!confirm(t('replaceDraft')))return;
        validateDesignDocument(payload.design);await loadProject(payload.workspace);state.designImported=true;state.design=payload.design;state.designBrief=structuredClone(payload.design.brief);
        const ids=new Set(state.runs.map(r=>r.id));
        for(const record of payload.runs){if(ids.has(record.id))continue;const run=structuredClone(record);run.imported=true;run.paused=true;if(run.replay)run.replay=normalizeReplay(run.replay);state.runs.push(run);ids.add(run.id);}
        changed();setView('design');
      }catch(error){state.designError=error.message;renderDesign();}
    });input.click();
  }
});}

async function checkProject() {
  if (!state.project || state.busy) return;
  const revision = state.revision, draftToken = state.draftToken;
  state.checks = [];
  for (const b of state.blocks) if (b.dirty) state.checks.push({code:'scatter.pending',path:b.id,message:t('pending')});
  try {
    const asynchronous = supportsTasks(state.capabilities, state.project);
    const result = asynchronous ? (await preflightSubmission(kernel, state.capabilities,
      structuredClone(state.project), structuredClone(state.settings), `${draftToken}:${revision}`)).result :
      await kernel.validate(structuredClone(state.project), state.settings);
    if (revision !== state.revision || draftToken !== state.draftToken) return;
    if (!state.checks.length) state.checks = [{code:'valid', message:t(asynchronous ? 'preflightPassed' : 'checkPassed',
      {...result, outputMiB:result.estimate ? (result.estimate.output_bytes / 1048576).toFixed(1) : '',
        wallTimeSeconds:result.limits?.wall_time_s ?? ''})}];
  } catch (error) {
    if (revision !== state.revision || draftToken !== state.draftToken) return;
    state.checks.push(...(error.issues?.length ? error.issues : [error.issue ?? {code:'connection.failed',message:error.message}]));
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
  const input = el(definition.enum ? 'select' : 'input');
  const entry = node.parameters[name];
  if (definition.enum) {
    input.append(new Option(t('parameterMissing'), ''));
    for (const value of definition.enum) input.append(new Option(String(value), value));
    input.value = entry?.value ?? '';
  }
  else
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
  const label = definition.unit ? `${definition.label ?? name} [${definition.unit}]` : definition.label ?? name;
  row.append(el('span', '', label), input);
  if (entry?.value === undefined) input.setAttribute('aria-description', t('parameterMissing'));
  const evidence = el('details', 'parameter-evidence');
  evidence.append(el('summary', '', t('parameterSource')));
  evidence.append(el('p', '', entry?.provenance ? `${entry.provenance.kind} · ${entry.provenance.reference}` : t('parameterMissing')));
  if (definition.description) evidence.append(el('p', '', definition.description));
  if (definition.minimum !== undefined || definition.maximum !== undefined) evidence.append(el('p', '', `${t('range')}: ${definition.minimum ?? '−∞'} … ${definition.maximum ?? '∞'}`));
  input.addEventListener('change', () => {
    const raw = definition.type === 'boolean' ? input.checked : input.value;
    const id = node.id;
    edit('parameterUpdated', () => {
      const problem = setParameter(findNode(id), manifest, name, raw);
      if (problem) throw new Error(problem);
    }, { name });
  });
  parent.append(row, evidence);
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
    applyRegisteredDefaults(node,manifest);
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
    if (selection.kind === 'node') {
      const role = requiredRoleRemovalProblem(state.project,selection.id,state.objects,state.modules);
      if (role) throw new Error(t('requiredMechanism',{role}));
    }
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
  const parameters = missingParameters(graph,state.modules);
  if (!missing.length && !unavailable.length && !parameters.length) check.append(el('p', 'empty-message', t('graphReady')));
  for (const item of parameters) {
    const button = el('button','event-row warning',`${item.node} · ${item.parameter}: ${t('parameterMissing')}`);
    button.addEventListener('click',()=>selectGraph({kind:'node',id:item.node})); check.append(button);
  }
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
    const role = requiredRoleRemovalProblem(state.project,node.id,state.objects,state.modules);
    remove.disabled = Boolean(role);
    if (role) { remove.title = t('requiredMechanism',{role}); root.append(el('p','pending-note',remove.title)); }
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
  if (state.view !== 'workflow' && state.selectedEnvironment) {
    const object = visibleEnvironment().find(item => item.id === state.selectedEnvironment);
    if (object) { renderEnvironmentInspector(root,object); return; }
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
        const detail=currentSnapshot()?.lifecycle_details?.deaths?.find(death=>death.cell_id===event.cell_id&&event.type==='death');
        if(detail){events.append(el('p','empty-message',t('deathRuleHint')),el('pre','parameter-record',JSON.stringify(detail,null,2)));}
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
  renderModuleDocumentation(root, manifest, t);
  const declaration=el('details','parameter-evidence');declaration.append(el('summary','',t('registeredDeclaration')),el('pre','',JSON.stringify(manifest.declaration??manifest,null,2)));root.append(declaration);
  if (selectedNode) {
    const values = section(root,t('parameterSource'));
    for (const [name, definition] of Object.entries(manifest.parameters ?? {})) {
      const record = selectedNode.parameters[name];
      const detail = el('details','parameter-evidence');
      detail.append(el('summary','',`${name} · ${record?.value ?? t('parameterMissing')} ${definition.unit ?? ''}`));
      detail.append(el('pre','',record ? JSON.stringify(record,null,2) : t('parameterMissing')));
      values.append(detail);
    }
  }
  const assumptions=section(root,t('description'));assumptions.append(el('p','empty-message',manifest.description ?? '—'));
  const params=section(root,t('parameters'));for(const [name,definition] of Object.entries(manifest.parameters ?? {})) kv(params,name,definition.unit ? `${definition.type} · ${definition.unit}` : definition.type);
}

function renderGraph() {
  if (!state.project || state.view !== 'workflow') return;
  state.layout = autoLayout(state.project.graph, state.modules, state.layout);
  try {
    graphEditor.render(state.project.graph, state.modules, state.layout, state.graphSelection,
      [...missingInputs(state.project.graph, state.modules),...missingParameters(state.project.graph,state.modules)]);
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
  const missingValues = state.project ? missingParameters(state.project.graph,state.modules).length : 0;
  const unavailable = state.project ? unavailableModules(state.project.graph, state.modules).length : 0;
  const unresolved = taskStore.list().some(run => matchesDraft(run, state) && ['submitting', 'submission_unknown'].includes(run.status));
  $('run-button').disabled = Boolean(state.batch) || state.busy || unresolved || pending || !state.project || missing > 0 || missingValues > 0 || unavailable > 0;
  $('run-button').title = state.busy ? t('running') : pending ? t('pending') : missing ? t('missingConnections') : '';
  if (unavailable) $('run-button').title = t('unknownModule');
  if (missingValues) $('run-button').title = t('parameterMissing');
  if (unresolved) $('run-button').title = t('retrySubmission');
  $('check-button').disabled = state.busy || !state.project;
}

function renderAll() {
  $('steps-input').max = String(stepLimit());
  if (!state.project) return;
  $('project-title').textContent = state.project.id + (fingerprint() !== state.saved ? ' •' : '');
  $('save-button').classList.toggle('modified', fingerprint() !== state.saved);
  $('dt-input').value = state.settings.dt_s;
  $('steps-input').value = state.settings.steps;
  $('species-options').replaceChildren(...Object.keys(state.project.species ?? {}).map(id => new Option(id, id)));
  $('document-label').textContent = t(state.view === 'design' ? 'design' : state.view === 'workflow' ? 'workflow' : state.view === 'results' ? 'results' : 'space');
  for (const button of document.querySelectorAll('[data-view]')) button.classList.toggle('active', button.dataset.view === state.view);
  for (const button of document.querySelectorAll('[data-left]')) button.classList.toggle('active', button.dataset.left === state.left);
  $('spatial-canvas').hidden = ['workflow','design'].includes(state.view);
  $('design-view').hidden = state.view !== 'design';
  if(state.view==='design')renderDesign();
  $('scene-annotations').hidden = ['workflow','design'].includes(state.view);
  $('view-controls').hidden = ['workflow','design'].includes(state.view);
  $('workflow-view').hidden = state.view !== 'workflow';
  $('bottom-dock').hidden = state.view !== 'results' || !state.replay;
  $('result-banner').hidden = state.view !== 'results' || !state.activeRun;
  $('result-banner').textContent = state.activeRun ? `${state.activeRun.id} · ${t(state.activeRun.status)}${state.activeRun.localId ? ' · ' + t(state.activeRun.manifest?.completeness ?? state.activeRun.completeness) + ' · ' + t('taskFramesOnly') : ''}${matchesDraft(state.activeRun, state) ? '' : ' · ' + t('earlierRevision')}` : '';
  const hasFields = state.replay?.snapshots.some(snapshot => Object.keys(snapshot.concentrations ?? {}).length);
  if (hasFields) $('result-banner').textContent = $('result-banner').textContent.replace(t('taskFramesOnly'),t('taskWithFields'));
  $('export-button').disabled = !state.replay;
  $('population-tool').disabled = state.view !== 'space' || !availablePlaceables(state.modules,state.capabilities,state.objects,state.project).some(item => item.kind === 'population' && item.status === 'ready');
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
  if (!Number.isFinite(dt) || dt <= 0 || !Number.isInteger(steps) || steps < 1 || steps > stepLimit()) {
    status('invalidControls', {}, true); return false;
  }
  stop();
  const submitted = structuredClone(candidate), settings = structuredClone(state.settings);
  try { resolveGraph(submitted.graph, state.modules); }
  catch (error) { status('runFailed', {message:error.message}, true); return false; }
  if (supportsTasks(state.capabilities, submitted)) {
    const draftToken = state.draftToken, revision = state.revision;
    state.busy = true; updateRunButton();
    try {
      const {submission} = await preflightSubmission(kernel, state.capabilities, submitted, settings, `${draftToken}:${revision}`);
      const record = taskStore.create(submission, {draftToken, revision,
        idempotencyRetentionSeconds:taskCapability(state.capabilities, submitted).limits.idempotency_retention_seconds});
      state.latestTask = record.localId;
      showBottom('runs'); renderDiagnostics(); status('taskSubmitted');
      await taskStore.submit(record.localId);
      taskStore.start(); return true;
    } catch (error) { status('runFailed', {message:error.message}, true); return false; }
    finally { state.busy = false; updateRunButton(); }
  }
  const id = `run-${Date.now()}-${state.runs.length + 1}`;
  submitted.run.run_id = id;
  const record = {id, project:submitted, settings, revision:state.revision, draftToken:state.draftToken, status:'running'};
  state.runs.push(record); state.busy = true;
  updateRunButton(); renderDiagnostics(); status('running');
  try {
    const result = await kernel.run(submitted, settings, id);
    if (result.execution?.request_id !== id) throw new Error('Run response identity mismatch');
    record.replay = normalizeReplay(result); record.status = 'completed';
    if (matchesDraft(record, state)) {
      selectRun(record);
      status('runReady', {count:record.replay.snapshots.length, cells:currentSnapshot().frame.cells.length});
    }
    return true;
  } catch (error) {
    record.status = 'failed'; record.error = error.message;
    if (matchesDraft(record, state)) status('runFailed', {message:error.message}, true);
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
  scatterButton.disabled = Boolean(block?.locked) || !objectForBlock(block,state.objects);
  for (const [key, action] of [['duplicate', () => duplicateBlock(id)], ['delete', () => removeBlock(id)]]) {
    const b = el('button', '', t(key)); b.disabled = Boolean(block?.locked) || (key === 'duplicate' && !objectForBlock(block,state.objects)); b.addEventListener('click', () => { menu.hidden = true; action(); }); menu.append(b);
  }
  menu.style.left = `${Math.min(x, innerWidth - 190)}px`;
  menu.style.top = `${Math.max(0, Math.min(y, innerHeight - 150))}px`;
  menu.hidden = false;
  state.selectedBlock = id;
  state.selectedEnvironment = null;
  renderInspector();
  renderScene();
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
  const project = document.workspace_format_version ? document.project : document;
  const profile = project?.execution_profile ?? 'legacy-explicit-v1';
  const registry = state.registries.get(profile);
  if (!registry) throw new Error('Unsupported execution profile: ' + profile);
  const {state:loaded,report} = adaptWorkspace(document, registry.modules);
  state.modules = registry.modules; state.objects = registry.objects;
  state.activeObject = availablePlaceables(state.modules, state.capabilities, state.objects,loaded.project).find(item => item.status === 'ready')?.id ?? null;
  state.draftToken = crypto.randomUUID(); state.latestTask = null;
  state.assemblyBusy = false; state.assemblyError = ''; state.assemblyNotice = ''; state.assemblyDraft = null;
  state.activeSpecies=null;
  Object.assign(state, loaded);
  state.layout = autoLayout(state.project.graph, state.modules, state.layout);
  state.selectedBlock = loaded.blocks[0]?.id ?? null;
  state.selectedCell = null;
  state.selectedEnvironment = null;
  state.graphSelection = null;
  state.selectedManifest = null;
    state.replay = null; state.activeRun = null;
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
    selectCell, selectBlock, selectEnvironment,
    placeEnvironment(point) {
      const object = availablePlaceables(state.modules,state.capabilities,state.objects,state.project).find(item => item.id === state.activeObject && item.status === 'ready' && item.kind !== 'population');
      if (!object || !state.project) return;
      edit('objectPlaced',() => {
        state.selectedEnvironment = initializeEnvironmentObject(state.project,object,state.modules,point,state.activeSpecies);
        state.selectedBlock = null;
        state.layout = autoLayout(state.project.graph,state.modules,state.layout);
      }); useTool('select');
    },
    placePopulation(point) {
      if (!state.project || !availablePlaceables(state.modules,state.capabilities,state.objects,state.project).some(item => item.id === state.activeObject && item.kind === 'population' && item.status === 'ready')) return;
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
      if (!canTransformBlock(block, state.view) || !objectForBlock(block, state.objects)) return;
      edit('blockMoved', () => { Object.assign(block, {center, size, rotation, dirty:true}); checkBlock(state.project.domain, block); });
    },
    transformEnvironment(id, geometry) {
      const object=visibleEnvironment().find(item=>item.id===id);
      if (!canTransformEnvironment(object,state.view,state.tool)) return;
      edit('updated', () => transformEnvironmentObject(state.project,id,state.objects,state.modules,geometry));
    },
    measure(result) {
      const readout = $('measure-readout');
      const endpoints = result?.endpoints.map((endpoint,index) => {
        const source = t({surface:'measureSurface',cell:'measureCellSurface','reference-plane':'measureReferencePlane'}[endpoint.source]);
        return `${index ? 'B' : 'A'} (${endpoint.position.map(v => fmt(v,3)).join(', ')}) µm · ${source}${endpoint.id ? ` · ${endpoint.id}` : ''}`;
      }).join(' → ');
      readout.textContent = result ? `${result.distance === null ? t('measureSecondPoint') : `${fmt(result.distance,3)} µm · Δ ${result.delta.map(v => fmt(v,3)).join(' / ')}`} · ${endpoints}` : '';
      readout.title = readout.textContent;
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
$('open-button').addEventListener('click', () => chooseProjectFile());
$('save-button').addEventListener('click', saveWorkspace);
$('undo-button').addEventListener('click', undo);
$('redo-button').addEventListener('click', redo);
$('project-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  const origin = fileWelcomeEpoch;
  fileWelcomeEpoch = null;
  const generation = ++fileReadGeneration;
  const revision = state.revision;
  const startupEpoch = welcomeEpoch;
  event.target.value = '';
  const read = async () => {
    if (file.size > 2_000_000) throw new Error(t('workspaceTooLarge'));
    return JSON.parse(await file.text());
  };
  if (origin !== null) {
    if (origin === welcomeEpoch && $('welcome-dialog').open) await loadWelcome(read, true);
    return;
  }
  const current = () => generation === fileReadGeneration && revision === state.revision && startupEpoch === welcomeEpoch;
  try {
    const document = await read();
    if (!current()) { if (generation === fileReadGeneration) status('importStale'); return; }
    await loadProject(document);
  } catch (error) { if (current()) status('loadFailed', { message: error.message }, true); }
});
$('project-file').addEventListener('cancel', () => { fileWelcomeEpoch = null; });
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
matchMedia('(min-width:961px)').addEventListener('change',event=>{if(event.matches)closeDocks();});
document.addEventListener('click', event => { if (!event.target.closest('#context-menu')) $('context-menu').hidden = true; });
document.addEventListener('keydown', event => {
  if (document.querySelector('dialog[open]')) return;
  if (!$('settings-overlay').hidden) {
    if (event.key === 'Escape') { event.preventDefault(); $('settings-close').click(); }
    return;
  }
  if (event.key === 'Escape') { $('context-menu').hidden = true; graphEditor.armed = null; closeDocks(); useTool('select'); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); saveWorkspace(); return; }
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable) return;
  const mod = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  if (mod && key === 'z') { event.preventDefault(); if (event.shiftKey) redo(); else undo(); return; }
  if (mod && key === 'y') { event.preventDefault(); redo(); return; }
  if (mod && key === 's') { event.preventDefault(); saveWorkspace(); return; }
  if (mod && event.key === 'Enter') { event.preventDefault(); if (!$('run-button').disabled) $('run-button').click(); return; }
  if (mod || event.altKey || event.shiftKey) return;
  if (key === 'f') {
    if (state.view === 'workflow') { event.preventDefault(); graphEditor.fit(); }
    else if (state.view === 'space' || state.view === 'results') { event.preventDefault(); viewport?.fit(); }
    return;
  }
  if (key === 'g') { viewport?.toggleGrid(); return; }
  if (state.view === 'space') {
    const tool = {v:'select',b:'population',t:'move',w:'move',e:'rotate',r:'scale',m:'measure',h:'hand',o:'orbit'}[key];
    if (tool) { event.preventDefault(); useTool(tool); return; }
    if ((key === 'delete' || key === 'backspace') && state.selectedEnvironment) { event.preventDefault(); removeEnvironment(state.selectedEnvironment); return; }
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
  edit('updated', () => { if (!Number.isFinite(value) || value <= 0 || (key === 'steps' && (!Number.isInteger(value) || value > stepLimit()))) throw new Error(t('invalidControls')); state.settings[key] = value; });
});
$('check-button').addEventListener('click', checkProject);
$('log-button').addEventListener('click', () => showBottom('console'));
$('diagnostics-close').addEventListener('click', () => { $('diagnostics').hidden = true; });
for (const button of document.querySelectorAll('[data-bottom]')) button.addEventListener('click', () => showBottom(button.dataset.bottom));
let welcomeEpoch = 0;
let welcomePending = false;
let fileWelcomeEpoch = null;
let fileReadGeneration = 0;
const replaceAllowed = () => !state.project || fingerprint() === state.saved || confirm(t('replaceDraft'));
function refreshWelcome() {
  let templates = $('welcome-templates');
  if (!templates) { templates=el('div');templates.id='welcome-templates';$('welcome-spatial').after(templates); }
  templates.replaceChildren();
  for (const registry of state.registries.values()) for (const item of registry.templates ?? []) {
    const button=el('button','start-command'); button.type='button';
    const text=el('span'); text.append(el('strong','',item.label),el('small','',`${item.description} · ${t('exploratory')}`));
    button.append(el('span','','◇'),text);
    button.addEventListener('click',()=>loadWelcome(()=>kernel.request(`/api/examples/${encodeURIComponent(item.example_id)}`)));
    templates.append(button);
  }
  let recovery = false;
  try { recovery = Boolean(localStorage.getItem(RECOVERY_KEY)); } catch { /* storage unavailable */ }
  for (const button of $('welcome-dialog').querySelectorAll('.start-command')) button.disabled = welcomePending;
  $('welcome-pts').disabled = welcomePending || !state.registries.has('conservative-pts-bulk-v1');
  $('welcome-spatial').disabled = welcomePending || !state.registries.has('spatial-unbiased-v1');
  $('welcome-recover').disabled = welcomePending || !recovery;
  $('welcome-recovery-hint').dataset.i18n = recovery ? 'recoveryPresent' : 'recoveryAbsent';
  $('welcome-recovery-hint').textContent = t($('welcome-recovery-hint').dataset.i18n);
  $('welcome-dialog').setAttribute('aria-busy', String(welcomePending));
}
function showWelcome() {
  welcomeEpoch++;
  welcomePending = false;
  $('welcome-error').hidden = true;
  refreshWelcome();
  $('welcome-dialog').showModal();
  $('welcome-new').focus({preventScroll:true});
  $('welcome-dialog').scrollTop = 0;
}
function chooseProjectFile(fromWelcome = false) {
  if (!replaceAllowed()) return;
  fileReadGeneration++;
  fileWelcomeEpoch = fromWelcome ? welcomeEpoch : null;
  $('project-file').click();
}
async function loadWelcome(read, alreadyConfirmed = false) {
  if (welcomePending || (!alreadyConfirmed && !replaceAllowed())) return;
  const epoch = ++welcomeEpoch;
  welcomePending = true;
  $('welcome-error').hidden = true;
  refreshWelcome();
  try {
    const document = await read();
    if (epoch !== welcomeEpoch || !$('welcome-dialog').open) return;
    await loadProject(document);
  } catch (error) {
    if (epoch !== welcomeEpoch || !$('welcome-dialog').open) return;
    $('welcome-error').textContent = t('loadFailed', {message:error.message});
    $('welcome-error').hidden = false;
    $('welcome-error').scrollIntoView({block:'nearest'});
    status('loadFailed', {message:error.message}, true);
  } finally {
    if (epoch === welcomeEpoch) { welcomePending = false; refreshWelcome(); }
  }
}
$('new-button').addEventListener('click', showWelcome);
$('welcome-close').addEventListener('click', () => $('welcome-dialog').close());
$('welcome-dialog').addEventListener('close', () => {
  welcomeEpoch++;
  welcomePending = false;
  refreshWelcome();
  $('new-button').focus({preventScroll:true});
});
$('welcome-open').addEventListener('click', () => chooseProjectFile(true));
$('welcome-new').addEventListener('click', () => loadWelcome(() => blankProject(state.template)));
$('welcome-demo').addEventListener('click', () => loadWelcome(() => state.template));
$('welcome-registry').addEventListener('click', () => loadWelcome(() => kernel.request('/api/examples/registry-readout')));
$('welcome-pts').addEventListener('click', () => loadWelcome(() => kernel.request('/api/examples/pts-bulk')));
$('welcome-spatial').addEventListener('click', () => loadWelcome(() => kernel.request('/api/examples/spatial-baseline')));
$('welcome-recover').addEventListener('click', () => loadWelcome(() => {
  const recovery = localStorage.getItem(RECOVERY_KEY);
  if (!recovery) throw new Error(t('recoveryAbsent'));
  return JSON.parse(recovery);
}));
$('data-close').addEventListener('click', () => $('data-dialog').close());
$('data-apply').addEventListener('click', () => {
  try {
    const {state:loaded} = adaptWorkspace(JSON.parse($('data-editor').value), state.modules);
    if (edit('updated', () => { state.project = loaded.project; state.blocks = loaded.blocks; state.selectedBlock = null; })) $('data-dialog').close();
  } catch (error) { $('data-error').textContent = error.message; }
});
$('export-button').addEventListener('click', () => { if (state.activeRun?.replay) $('export-dialog').showModal(); });
$('export-close').addEventListener('click', () => $('export-dialog').close());
$('export-json').addEventListener('click', () => {
  const run = state.activeRun; if (!run?.replay) return;
  const payload = run.localId ? {task_contract_version:run.submission.task_contract_version, task_run_id:run.runId, status:run.manifest.status,
    completeness:run.manifest.completeness, task:run.task, submission:run.submission, manifest:run.manifest, replay:run.replay} : exportRun(run);
  download(`${run.id}.result.json`, JSON.stringify(payload, null, 2));
});
$('task-input-close').addEventListener('click', () => $('task-input-dialog').close());
$('export-csv').addEventListener('click', () => { if (state.replay) download(`${state.activeRun?.id ?? state.replay.run.run_id}.metrics.csv`, metricsCSV(state.replay), 'text/csv'); });
window.addEventListener('beforeunload', event => { if (state.project && fingerprint() !== state.saved) { event.preventDefault(); event.returnValue = ''; } });
for (const [id, glyph] of Object.entries({'select-tool':'select','population-tool':'cell','move-tool':'move','rotate-tool':'reset','scale-tool':'scale',
  'snap-tool':'grid','measure-tool':'measure','fit-tool':'fit','objects-tool':'layers','properties-tool':'settings','hand-tool':'hand','orbit-tool':'reset'})) $(id).innerHTML = icon(glyph);
for (const element of document.querySelectorAll('[data-start-icon]')) element.innerHTML = icon(element.dataset.startIcon);
for (const dialog of document.querySelectorAll('dialog')) {
  let outsidePointer = null;
  const isOutside = event => {
    const rect = dialog.getBoundingClientRect();
    return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
  };
  dialog.addEventListener('pointerdown', event => {
    outsidePointer = event.button === 0 && event.target === dialog && isOutside(event) ? event.pointerId : null;
  });
  dialog.addEventListener('pointercancel', () => { outsidePointer = null; });
  dialog.addEventListener('close', () => { outsidePointer = null; });
  dialog.addEventListener('click', event => {
    const close = outsidePointer !== null && event.detail > 0 && event.target === dialog && isOutside(event);
    outsidePointer = null;
    if (close) dialog.close();
  });
}

try {
  const [catalogResponse, projectResponse, capabilities] = await Promise.all([fetch('/api/catalog'), fetch('/api/example-project'), kernel.capabilities()]);
  if (!catalogResponse.ok || !projectResponse.ok) throw new Error('Local kernel unavailable');
  const registry = registerCatalog(await catalogResponse.json());
  state.registries.set('legacy-explicit-v1', registry);
  if (capabilities.execution_profiles?.includes('conservative-pts-bulk-v1')) {
    const ptsRegistry = registerCatalog(await kernel.request('/api/catalog?execution_profile=conservative-pts-bulk-v1'));
    state.registries.set('conservative-pts-bulk-v1', ptsRegistry);
  }
  $('welcome-pts').disabled = !state.registries.has('conservative-pts-bulk-v1');
  if (capabilities.execution_profiles?.includes('spatial-unbiased-v1')) {
    const spatialRegistry = registerCatalog(await kernel.request('/api/catalog?execution_profile=spatial-unbiased-v1'));
    state.registries.set('spatial-unbiased-v1',spatialRegistry);
  }
  if (capabilities.execution_profiles?.includes('chemotaxis-spatial-v1')) {
    state.registries.set('chemotaxis-spatial-v1',registerCatalog(await kernel.request('/api/catalog?execution_profile=chemotaxis-spatial-v1')));
  }
  state.modules = registry.modules; state.objects = registry.objects;
  state.activeObject = availablePlaceables(state.modules, capabilities, state.objects).find(item => item.status === 'ready')?.id ?? null;
  state.capabilities = capabilities;
  if (supportsTasks(capabilities)) { state.runs.push(...taskStore.restore()); taskStore.start(); renderDiagnostics(); }
  state.template = await projectResponse.json();
  showWelcome();
  updateRunButton(); status('ready');
} catch (error) { status('loadFailed', { message: error.message }, true); }
