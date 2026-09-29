import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {readWorkspace, writeWorkspace, blankProject, draftSnapshot, deletePopulation, exportRun} from '../src/friskoli_cad/web/workspace.mjs';
import {createBlock, scatterBlock, checkBlock, rotatePoint, rotationQuaternion} from '../src/friskoli_cad/web/population.mjs';
const template = JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/workspace_3d.project.json', import.meta.url)));
const staticModule = {id:'population.static', version:'1.0.0', scope:'population'};

test('workspace round trip preserves unsolved edits, rotations, layout and solver settings', () => {
  const state = readWorkspace(template);
  state.blocks[0].dirty = true; state.blocks[0].rotation = [0,0,10];
  state.settings = {dt_s:.1, steps:20}; state.layout = {elongation:{x:42,y:56}};
  const loaded = readWorkspace(writeWorkspace(state));
  assert.deepEqual(loaded, state);
  assert.equal(draftSnapshot(loaded.project).frame.cells.length, 8);
});

test('legacy workspace migrates without running and future or malformed documents are rejected', () => {
  const state = readWorkspace(template), legacy = writeWorkspace(state);
  legacy.workspace_format_version = '0.1.0'; delete legacy.run_settings;
  assert.equal(readWorkspace(legacy).settings.steps, 8);
  assert.throws(() => readWorkspace({...legacy,workspace_format_version:'9.0.0'}));
  const bad = structuredClone(legacy); bad.population_blocks[0].size[0] = NaN;
  assert.throws(() => readWorkspace(bad));
  const badGroup = structuredClone(template); badGroup.groups.group_1.orientation_xyzw.pop();
  assert.throws(() => readWorkspace(badGroup));
});

test('blank draft and scattered population are independent of solved result', () => {
  const project = blankProject(template), run = {project:structuredClone(template),settings:{dt_s:.5,steps:8},replay:{snapshots:[]}};
  const block = createBlock(project,[18,12,6]); block.count = 12;
  scatterBlock(project,block,staticModule);
  assert.equal(draftSnapshot(project).frame.cells.length,12);
  assert.equal(project.graph.nodes[0].module_id,'population.static');
  assert.equal(exportRun(run).project.groups.group_1.ids.length,8);
  assert.equal(template.groups.group_1.ids.length,8);
});

test('population deletion removes owned nodes, edges and channel references', () => {
  const project = structuredClone(template); deletePopulation(project,'group_1');
  assert.deepEqual(project.groups,{}); assert.deepEqual(project.graph.nodes,[]);
  assert.deepEqual(project.graph.edges,[]); assert.deepEqual(project.run.channels,{});
});

test('rotated seeded scatter stays within volume and rejected scatter leaves project unchanged', () => {
  const project = blankProject(template), block = createBlock(project,[18,12,6]);
  block.rotation = [0,0,35]; block.size = [10,10,8];
  scatterBlock(project,block,staticModule);
  const first = structuredClone(project.groups[block.id]); scatterBlock(project,block,staticModule);
  assert.deepEqual(project.groups[block.id],first);
  for (const p of first.positions_um) assert.ok(p.every((v,i) => v >= 0 && v <= [36,24,12][i]));
  const before = structuredClone(project); block.center = [0,0,0];
  assert.throws(() => scatterBlock(project,block,staticModule)); assert.deepEqual(project,before);
  const turned = rotatePoint([1,0,0],rotationQuaternion([0,0,90]));
  assert.ok(Math.abs(turned[0]) < 1e-10 && Math.abs(turned[1]-1) < 1e-10);
});

test('tilting a thin-layer volume and scattering without a backend module are rejected atomically', () => {
  const project = blankProject(template), block = createBlock(project,[18,12,6]);
  const before = structuredClone(project);
  assert.throws(() => scatterBlock(project,block,null)); assert.deepEqual(project,before);
  assert.throws(() => checkBlock({...project.domain,geometry:'thin_layer'}, {...block,rotation:[1,0,0]}));
});
