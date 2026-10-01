import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import {registerCatalog, resolveGraph, readableManifest, unavailableModules} from '../src/friskoli_cad/web/catalog.mjs';
import {availablePlaceables, initializeObject} from '../src/friskoli_cad/web/placeables.mjs';
import {createBlock} from '../src/friskoli_cad/web/population.mjs';
import {blankProject,readWorkspace,writeWorkspace} from '../src/friskoli_cad/web/workspace.mjs';
import {adaptWorkspace} from '../src/friskoli_cad/web/migration.mjs';
import {autoLayout} from '../src/friskoli_cad/web/workflow.mjs';
import katex from '../src/friskoli_cad/web/vendor/katex/katex.mjs';
const read = path => JSON.parse(readFileSync(new URL('../' + path, import.meta.url)));
const manifests = readdirSync(new URL('../src/friskoli_cad/engine/manifests/', import.meta.url))
  .filter(name => name.endsWith('.json')).map(name => read('src/friskoli_cad/engine/manifests/' + name));
const object = read('src/friskoli_cad/engine/declarations/population.object.json');
const entries = manifests.map(manifest => ({key:manifest.id + '@' + manifest.version,
  category:['geometry.capsule_readout','population.static'].includes(manifest.id) ? 'data' : manifest.scientific_role}));
const payload = {catalog_version:'0.1.0',execution_semantics:'legacy-explicit-v1',modules:manifests,entries,objects:[object]};
const registry = registerCatalog(payload);
const template = read('src/friskoli_cad/examples/workspace_3d.project.json');

test('registered objects and modules appear without fixed IDs; unsupported adapters stay disabled', () => {
  const custom = structuredClone(payload); custom.objects[0].id = 'example.custom_population';
  const r = registerCatalog(custom);
  assert.equal(availablePlaceables(r.modules,null,r.objects)[0].id,'example.custom_population');
  assert.equal(availablePlaceables(r.modules,null,r.objects)[0].status,'ready');
  custom.objects[0].initializer.adapter = 'unknown@9';
  const unsupported = registerCatalog(custom);
  assert.equal(availablePlaceables(unsupported.modules,null,unsupported.objects)[0].status,'unsupported');
  assert.ok(r.modules.has('geometry.capsule_readout@1.0.0'));
  assert.throws(() => registerCatalog({...payload,catalog_version:'9.0.0'}));
  assert.throws(() => registerCatalog({...payload,entries:entries.slice(1)}));
});

test('scatter and automatic readers survive undo, redo, save and reopen without duplication', () => {
  let state = readWorkspace(blankProject(template));
  const block = createBlock(state.project,[18,12,6]); block.count=4;
  state.blocks.push(block);
  const before = structuredClone(state);
  initializeObject(state.project,block,object,registry.modules);
  state.layout=autoLayout(state.project.graph,registry.modules);
  assert.equal(state.project.graph.nodes.length,2);
  assert.equal(block.binding.data_nodes.length,1);
  const id = block.binding.data_nodes[0];
  assert.equal(state.layout[id].collapsed,true);
  const after = structuredClone(state);
  state=structuredClone(before); assert.equal(state.project.graph.nodes.length,0);
  state=structuredClone(after);
  const reopened=readWorkspace(writeWorkspace(state));
  assert.deepEqual(reopened,state);
  initializeObject(reopened.project,reopened.blocks[0],object,registry.modules);
  assert.equal(reopened.project.graph.nodes.length,2);
  assert.deepEqual(reopened.project.groups,state.project.groups);
  const originalNodes=structuredClone(reopened.project.graph.nodes);
  reopened.project.graph.nodes=reopened.project.graph.nodes.filter(node => node.id!==id);
  initializeObject(reopened.project,reopened.blocks[0],object,registry.modules);
  assert.equal(reopened.project.graph.nodes.length,1,'manual reader deletion must remain deleted');
  assert.equal(originalNodes.find(node=>node.id===id).owner.id,block.id);
});

test('unavailable initializer rejects the whole operation without changing input', () => {
  const state=readWorkspace(blankProject(template));const block=createBlock(state.project,[18,12,6]);
  const before=structuredClone({project:state.project,block});
  const modules=new Map(registry.modules); modules.delete('geometry.capsule_readout@1.0.0');
  assert.throws(()=>initializeObject(state.project,block,object,modules));
  assert.deepEqual({project:state.project,block},before);
});

test('unknown modules remain readable with original edges and parameter data but cannot run', () => {
  const document=structuredClone(template);document.graph.nodes[0].module_version='99.0.0';
  const before=structuredClone(document);
  const {state,report}=adaptWorkspace(document,registry.modules);
  assert.equal(report.issues[0].code,'module.missing');
  assert.equal(report.issues[0].path,'/graph/nodes/0');
  assert.equal(unavailableModules(state.project.graph,registry.modules).length,1);
  assert.throws(()=>resolveGraph(state.project.graph,registry.modules));
  const resolved=resolveGraph(state.project.graph,registry.modules,{allowUnknown:true});
  assert.equal(resolved.nodes[0].manifest.unavailable,true);
  assert.ok(readableManifest(document.graph.nodes[0],document.graph,registry.modules).description);
  assert.deepEqual(writeWorkspace(state).project,before);
  assert.deepEqual(document,before);
});

test('legacy adapter reports metadata migration without changing graph timing or parameters', () => {
  for (const version of ['0.1.0','0.2.0']) {
    const workspace=writeWorkspace(readWorkspace(template));workspace.workspace_format_version=version;
    const before=structuredClone(workspace.project);
    const {state,report}=adaptWorkspace(workspace,registry.modules);
    assert.equal(report.target_version,'0.6.0');
    assert.equal(report.execution_semantics,'legacy-explicit-v1');
    assert.ok(report.changes.length);
    assert.deepEqual(state.project,before);
    assert.deepEqual(writeWorkspace(state).project,before);
  }
});

test('registered LaTeX renders offline, exposes MathML, and does not trust executable links', () => {
  for (const name of ['geometry.capsule_readout','field.sample_box_support.v2']) {
    const d=read('src/friskoli_cad/engine/declarations/'+name+'.json');
    for (const equation of d.mathematics.equations) {
      const html=katex.renderToString(equation.latex,{throwOnError:true,trust:false,strict:'error'});
      assert.ok(html.includes('<math'));
      assert.ok(!html.includes('katex-error'));
    }
  }
  const malicious=String.raw`\href{javascript:alert(1)}{click}`;
  const html=katex.renderToString(malicious,{trust:false,throwOnError:false});
  assert.ok(!html.includes('<a '));
});
