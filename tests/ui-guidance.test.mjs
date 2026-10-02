import test from 'node:test';
import assert from 'node:assert/strict';
import {diagnosticTarget,moduleFolders} from '../src/friskoli_cad/web/ui-guidance.mjs';
const project={graph:{nodes:[{id:'n-source'},{id:'n-reserve'}],edges:[{id:'edge'}]},groups:{'cells/a':{}}};
test('diagnostics resolve exact graph indexes, escaped groups and parameters without guessing from prose',()=>{
  assert.deepEqual(diagnosticTarget({path:'/project/graph/nodes/1/parameters/maintenance_molecules_s'},project),{kind:'node',id:'n-reserve',parameter:'maintenance_molecules_s'});
  assert.deepEqual(diagnosticTarget({path:'/nodes/0'},project),{kind:'node',id:'n-source',parameter:undefined});
  assert.deepEqual(diagnosticTarget({path:'/groups/cells~1a/positions_um/0'},project),{kind:'population',id:'cells/a'});
  assert.equal(diagnosticTarget({path:'/edges/0'},project).kind,'edge');
  assert.equal(diagnosticTarget({path:'/nodes/99'},project).kind,'graph');
  assert.equal(diagnosticTarget({message:'Failed node n-source somewhere'},project),null);
  assert.equal(diagnosticTarget({message:'n-source: invalid release'},project).id,'n-source');
  assert.equal(diagnosticTarget({code:'valid',path:'/domain'},project),null);
});
test('module folders keep generic uptake separate from sensing and preserve unknown registrations',()=>{
  const modules=new Map(['uptake.saturating_request','signal.mcp_adaptation','metabolism.reserve_balance','new.extension'].map(id=>[id,{id,description:'readable'}]));
  const before=structuredClone(modules),folders=moduleFolders(modules,'zh-CN');
  assert.deepEqual(folders.map(f=>f.id),['uptake','signals','life','other']);
  assert.equal(moduleFolders(modules,'en','mcp')[0].entries[0][0],'signal.mcp_adaptation');
  assert.deepEqual(modules,before);
});

test('population parameter bindings preserve the actual nodes, owners and unknown modules', async()=>{
  const {populationModuleBindings,populationNodeLocked}=await import('../src/friskoli_cad/web/ui-guidance.mjs');
  const p={graph:{nodes:[{id:'motility',module_id:'motion.hazard_walk',module_version:'1',owner:{kind:'population',id:'pts'},parameters:{speed_um_s:{value:5}}},{id:'control',module_id:'motion.hazard_walk',module_version:'1',owner:{kind:'population',id:'random'},parameters:{speed_um_s:{value:5}}},{id:'source',module_id:'source.finite_local',module_version:'1',owner:{kind:'environment',id:'world'},parameters:{}},{id:'unknown',module_id:'extension',module_version:'1',owner:{kind:'population',id:'pts'},parameters:{}}]}};
  const manifest={id:'motion.hazard_walk',parameters:{speed_um_s:{type:'number',unit:'um/s'}}};
  const bindings=populationModuleBindings(p,new Map([['motion.hazard_walk@1',manifest]]),'pts');
  assert.deepEqual(bindings.map(x=>x.node.id),['motility','unknown']);assert.equal(bindings[0].node,p.graph.nodes[0]);assert.equal(bindings[0].manifest,manifest);assert.equal(bindings[1].manifest,null);
  bindings[0].node.parameters.speed_um_s.value=7;assert.equal(p.graph.nodes[0].parameters.speed_um_s.value,7);assert.equal(p.graph.nodes[1].parameters.speed_um_s.value,5);
  assert.equal(populationNodeLocked(bindings[0].node,[{id:'pts',locked:true}]),true);assert.equal(populationNodeLocked(p.graph.nodes[1],[{id:'pts',locked:true}]),false);
});
