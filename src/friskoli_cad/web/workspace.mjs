// Editable documents and solved runs have separate lifetimes. No solver logic belongs here.
import { blocksFromProject } from './population.mjs';
import { METRIC_KEYS, metricRows, radialMetricColumns, csvCell } from './metrics.mjs';

export const WORKSPACE_VERSION = '0.7.0';
export const RECOVERY_KEY = 'friskoli.workspace.v2';
const vector = (v, positive = false) => Array.isArray(v) && v.length === 3 &&
  v.every(n => Number.isFinite(n) && (!positive || n > 0));

const nonempty = v => typeof v === 'string' && v.trim().length > 0;
export function validateDesignBrief(brief) {
  const metrics=METRIC_KEYS.slice(2);
  if (!brief || !['0.1.0','0.2.0','0.3.0'].includes(brief.brief_version) || !nonempty(brief.id) || !nonempty(brief.name) ||
    !metrics.includes(brief.goal?.metric) || !['maximize','minimize'].includes(brief.goal?.direction) || !nonempty(brief.goal?.group_id) ||
    !nonempty(brief.chassis?.name) || !nonempty(brief.chassis?.provenance) || !Array.isArray(brief.variables) || !brief.variables.length ||
    brief.variables.some(v=>!nonempty(v.node_id)||!nonempty(v.parameter)||!Array.isArray(v.values)||!v.values.length||v.values.some(x=>!Number.isFinite(x))) ||
    !Array.isArray(brief.constraints) || brief.constraints.some(c=>!nonempty(c.id)||!['hard','soft'].includes(c.kind)||!nonempty(c.node_id)||!nonempty(c.parameter)||!['<=','>='].includes(c.operator)||!Number.isFinite(c.value)||(c.weight!==undefined&&(!Number.isFinite(c.weight)||c.weight<0))) ||
    !Array.isArray(brief.seeds)||!brief.seeds.length||brief.seeds.length>8||brief.seeds.some(n=>!Number.isSafeInteger(n)||n<0)||new Set(brief.seeds).size!==brief.seeds.length||
    !Number.isSafeInteger(brief.max_runs)||brief.max_runs<1||brief.max_runs>32) throw new Error('Invalid design brief: check objective, chassis, parameter values, constraints and distinct seeds');
  if (['0.2.0','0.3.0'].includes(brief.brief_version) && (!Array.isArray(brief.result_constraints) ||
    brief.result_constraints.some(c=>!nonempty(c.id)||!['hard','soft'].includes(c.kind)||!metrics.includes(c.metric)||!nonempty(c.group_id)||!['<=','>='].includes(c.operator)||!Number.isFinite(c.value)) ||
    new Set(brief.result_constraints.map(c=>c.id)).size!==brief.result_constraints.length ||
    !Number.isSafeInteger(brief.selection_policy?.min_repeats)||brief.selection_policy.min_repeats<2||brief.selection_policy.min_repeats>8||
    !Number.isFinite(brief.selection_policy?.min_control_improvement)||brief.selection_policy.min_control_improvement<0)) throw new Error('Invalid result constraints or selection policy');
  return brief;
}
export function validateDesignDocument(design) {
  if (!design || !['0.1.0','0.2.0','0.3.0'].includes(design.design_version) || !nonempty(design.id) || !Array.isArray(design.candidates) || !Array.isArray(design.excluded) ||
    !design.settings || !Number.isFinite(design.settings.dt_s)||design.settings.dt_s<=0||!Number.isSafeInteger(design.settings.steps)||design.settings.steps<1 ||
    !design.budget || ['candidate_count','repeats','total_runs','total_steps'].some(k=>!Number.isSafeInteger(design.budget[k])||design.budget[k]<0)) throw new Error('Invalid design document structure');
  validateDesignBrief(design.brief);readWorkspace(design.baseline_project);
  const ids=new Set();for(const c of design.candidates){
    if(!nonempty(c.id)||ids.has(c.id)||!nonempty(c.name)||!['candidate','control'].includes(c.kind)||!nonempty(c.explanation)||!Number.isFinite(c.soft_penalty)||!Array.isArray(c.overrides)||c.overrides.some(o=>!nonempty(o.node_id)||!nonempty(o.parameter)||!Number.isFinite(o.value)))throw new Error('Invalid design candidate');
    ids.add(c.id);readWorkspace(c.project);
  }
  if(design.excluded.some(c=>!nonempty(c.id)||!nonempty(c.name)||!Array.isArray(c.reasons)||c.reasons.some(r=>!nonempty(r))))throw new Error('Invalid excluded candidate');
  return design;
}

export function readWorkspace(document) {
  const version = document?.workspace_format_version;
  if (version && version !== WORKSPACE_VERSION) throw new Error('Unsupported workspace version');
  const project = structuredClone(version ? document.project : document);
  if (!project || project.project_version !== '0.6.0' || project.execution_profile !== 'modular-spatial-v1' || typeof project.id !== 'string' ||
      !Number.isSafeInteger(project.random_seed) || project.random_seed < 0 ||
      !vector(project.domain?.counts_xyz, true) || !project.domain.counts_xyz.every(Number.isInteger) ||
      !vector(project.domain.spacing_um_xyz, true) || !['thin_layer', 'volume'].includes(project.domain.geometry) ||
      !project.groups || !project.species || !project.controls || !Array.isArray(project.graph?.nodes) ||
      !Array.isArray(project.graph?.edges) || !Array.isArray(project.run?.groups) || !project.run.channels ||
      project.graph.protocol_version !== '0.2.0' || project.run.protocol_version !== '0.1.0') throw new Error('Only modular Project 0.6 / Workspace 0.7 is supported; check project structure');
  const nodeIds = new Set();
  for (const node of project.graph.nodes) {
    if (!node || typeof node.id !== 'string' || nodeIds.has(node.id) || typeof node.module_id !== 'string' ||
        typeof node.module_version !== 'string' || !['population','environment','source'].includes(node.owner?.kind) ||
        typeof node.owner.id !== 'string' || !node.parameters || typeof node.parameters !== 'object') throw new Error('Invalid graph node');
    nodeIds.add(node.id);
  }
  const edgeIds = new Set();
  for (const edge of project.graph.edges) {
    if (!edge || typeof edge.id !== 'string' || edgeIds.has(edge.id) || !nodeIds.has(edge.from?.node) ||
        !nodeIds.has(edge.to?.node) || typeof edge.from.port !== 'string' || typeof edge.to.port !== 'string' ||
        !['same_step','previous_step'].includes(edge.timing)) throw new Error('Invalid graph connection');
    edgeIds.add(edge.id);
  }
  if (project.domain.geometry === 'thin_layer' && project.domain.counts_xyz[2] !== 1) throw new Error('Thin layers need one Z grid cell');
  const ids = new Set();
  for (const group of Object.values(project.groups)) {
    if (!Array.isArray(group.ids) || !Array.isArray(group.positions_um) || !Array.isArray(group.orientation_xyzw) ||
        group.ids.length !== group.positions_um.length || group.ids.length !== group.orientation_xyzw.length ||
        (group.initial_geometry && group.initial_geometry.length !== group.ids.length)) throw new Error('Invalid population arrays');
    group.ids.forEach((id, i) => {
      const q = group.orientation_xyzw[i], g = group.initial_geometry?.[i];
      if (typeof id !== 'string' || ids.has(id) || !vector(group.positions_um[i]) ||
          !Array.isArray(q) || q.length !== 4 || !q.every(Number.isFinite) ||
          (g && (!Number.isFinite(g.length_um) || !Number.isFinite(g.diameter_um) || g.length_um < g.diameter_um || g.diameter_um <= 0))) {
        throw new Error('Invalid cell data');
      }
      ids.add(id);
    });
  }
  if (ids.size > 2000) throw new Error('Local viewer supports at most 2000 cells');
  const blocks = structuredClone(version ? document.population_blocks : blocksFromProject(project));
  if (!Array.isArray(blocks) || new Set(blocks.map(b => b.id)).size !== blocks.length) throw new Error('Invalid population blocks');
  for (const b of blocks) {
    if (!/^[a-z][a-z0-9_]*$/.test(b.id) || typeof b.name !== 'string' || !vector(b.center) || !vector(b.size, true) ||
        !Number.isInteger(b.count) || b.count < 1 || b.count > 2000 || !Number.isInteger(b.seed) ||
        !Number.isFinite(b.length) || !Number.isFinite(b.diameter) || b.length < b.diameter || b.diameter <= 0 ||
        (b.rotation && !vector(b.rotation))) throw new Error('Invalid population block');
    b.rotation ??= [0, 0, 0]; b.hidden = Boolean(b.hidden); b.locked = Boolean(b.locked);
    if (b.object_type !== undefined && (typeof b.object_type !== 'string' || !b.object_type)) throw new Error('Invalid object type');
    if (b.binding && (!Array.isArray(b.binding.data_nodes) || b.binding.data_nodes.some(id => typeof id !== 'string'))) throw new Error('Invalid object binding');
  }
  const layout = structuredClone(document.graph_layout ?? {});
  for (const point of Object.values(layout)) if (![point.x, point.y].every(n => Number.isFinite(n) && n >= 0 && n <= 100000) ||
      (point.collapsed !== undefined && typeof point.collapsed !== 'boolean')) throw new Error('Invalid graph layout');
  const settings = structuredClone(document.run_settings ?? {dt_s:.1,steps:20,frame_every_steps:1,include_fields:true});
  if (!Number.isFinite(settings.dt_s) || settings.dt_s <= 0 || !Number.isSafeInteger(settings.steps) || settings.steps < 1 || settings.steps > 4320000) throw new Error('Invalid run settings');
  if (settings.seed !== undefined && (!Number.isSafeInteger(settings.seed) || settings.seed < 0)) throw new Error('Invalid execution seed');
  if (settings.include_fields !== undefined && typeof settings.include_fields !== 'boolean') throw new Error('Invalid field output setting');
  if (settings.frame_every_steps !== undefined && (!Number.isSafeInteger(settings.frame_every_steps) || settings.frame_every_steps < 1 || settings.frame_every_steps > 4320000)) throw new Error('Invalid frame interval');
  if(settings.backend!==undefined&&!['numpy-cpu','numpy-cupy-cuda'].includes(settings.backend))throw Error('Invalid execution backend');
  if(settings.include_final_fields!==undefined&&typeof settings.include_final_fields!=='boolean')throw Error('Invalid final field setting');
  if(settings.field_stride_xyz!==undefined&&(!Array.isArray(settings.field_stride_xyz)||settings.field_stride_xyz.length!==3||settings.field_stride_xyz.some((s,i)=>!Number.isSafeInteger(s)||s<1||project.domain.counts_xyz[i]%s)))throw Error('Invalid field preview stride');
  const design = version ? structuredClone(document.design ?? null) : null;
  const designBrief = version ? structuredClone(document.design_brief ?? null) : null;
  if (design) validateDesignDocument(design);
  if (designBrief) validateDesignBrief(designBrief);
  const managedTemplates=structuredClone(document.managed_templates??{}),draftLinks=structuredClone(document.draft_links??[]),viewState=structuredClone(document.view_state??null);
  if(!managedTemplates||Array.isArray(managedTemplates)||!Array.isArray(draftLinks))throw Error('Invalid workspace metadata');
  if(Object.values(managedTemplates).some(r=>!r||!['generated_by','owner_object_id','template_version'].every(k=>nonempty(r[k]))||!r.overrides||typeof r.overrides!=='object'||!Array.isArray(r.base_nodes))||draftLinks.some(e=>!nonempty(e.id)||!nonempty(e.from?.node)||!nonempty(e.from?.port)||!nonempty(e.to?.node)||!nonempty(e.to?.port)||!['same_step','previous_step'].includes(e.timing)))throw Error('Invalid managed template or connection draft');
  if(viewState&&(viewState.version!==1||!['space','workflow','results','design'].includes(viewState.view)||viewState.camera&&(!vector(viewState.camera.position)||!vector(viewState.camera.target)||!Number.isFinite(viewState.camera.zoom)||viewState.camera.zoom<=0)||Object.values(viewState.scroll??{}).some(v=>!Number.isFinite(v)||v<0)))throw Error('Invalid ViewState');
  return { project, blocks, layout, settings, design, designBrief, managedTemplates, draftLinks, viewState };
}

export function writeWorkspace({ project, blocks, layout, settings, design, designBrief, managedTemplates={}, draftLinks=[],viewState=null }) {
  return structuredClone({ workspace_format_version: WORKSPACE_VERSION, project,
    managed_templates:managedTemplates,draft_links:draftLinks,...(viewState?{view_state:viewState}:{}),population_blocks: blocks, graph_layout: layout, run_settings: settings, ...(design ? {design} : {}), ...(designBrief ? {design_brief:designBrief} : {}) });
}

export function draftSnapshot(project) {
  return { frame: { frame_version: '0.2.0', protocol_version: '0.1.0', run_id: project.run.run_id,
    frame_index: 0, time_s: 0, events: [], cells: Object.entries(project.groups).flatMap(([group_id, group]) =>
      group.ids.map((id, i) => ({ id, group_id, position_um: group.positions_um[i],
        orientation_xyzw: group.orientation_xyzw[i], geometry: group.initial_geometry?.[i] ?? null, channels: {} }))) }, concentrations: {} };
}

export function blankProject(template) {
  const project = structuredClone(template);
  project.id = 'untitled-project'; project.groups = {}; project.controls = {};
  project.graph.nodes = []; project.graph.edges = [];
  project.run.groups = []; project.run.channels = {}; project.run.run_id = 'draft';
  return project;
}

export function deletePopulation(project, id) {
  const nodes = new Set(project.graph.nodes.filter(n => n.owner.kind === 'population' && n.owner.id === id).map(n => n.id));
  delete project.groups[id];
  project.graph.nodes = project.graph.nodes.filter(n => !nodes.has(n.id));
  project.graph.edges = project.graph.edges.filter(e => !nodes.has(e.from.node) && !nodes.has(e.to.node));
  project.run.groups = project.run.groups.filter(g => g !== id);
  for (const [key, channel] of Object.entries(project.run.channels)) if (nodes.has(channel.node)) delete project.run.channels[key];
}

export function exportRun(record) {
  return { result_format_version: '0.1.0', status: 'completed', project: record.project,
    run_settings: record.settings, execution: record.replay.execution, replay: record.replay };
}

export function metricsCSV(replay) {
  if (replay.snapshots.some(s => s.metrics)) {
    const radial=radialMetricColumns(replay),values=replay.snapshots.flatMap(s=>Object.values(s.metrics?.by_group??{}));
    return [['frame_index','time_s','observation_id','group_id',...METRIC_KEYS,...radial.map(c=>c.label)],...metricRows(replay).map((row,i)=>[...row,...radial.map(c=>c.value(values[i].radial))])].map(row => row.map(csvCell).join(',')).join('\n')+'\n';
  }
  const rows = ['frame_index,time_s,cell_count,event_count'];
  for (const { frame } of replay.snapshots) rows.push([frame.frame_index, frame.time_s, frame.cells.length, frame.events.length].join(','));
  return rows.join('\n') + '\n';
}
