import test from 'node:test';
import assert from 'node:assert/strict';
import {CommandRegistry,graphMutationReason,settingControl} from '../src/friskoli_cad/web/commands.mjs';
import {diagnosticTarget} from '../src/friskoli_cad/web/ui-guidance.mjs';
test('commands recheck changed capabilities before all dispatches',()=>{
 const state={locked:false,count:0};const commands=new CommandRegistry(()=>state);
 commands.register('change',{reason:s=>s.locked?'Locked':'',run:s=>++s.count});
 assert.equal(commands.execute('change'),1);state.locked=true;
 assert.equal(commands.inspect('change').reason,'Locked');assert.equal(commands.execute('change'),false);assert.equal(state.count,1);
});
test('locked population blocks deleting both the node and attached edges',()=>{
 const p={graph:{nodes:[{id:'a',owner:{kind:'population',id:'cells'}},{id:'b',owner:{kind:'environment',id:'world'}}],edges:[{id:'e',from:{node:'a'},to:{node:'b'}}]},groups:{cells:{}}};
 const blocks=[{id:'cells',locked:true}];
 for(const selection of [{kind:'node',id:'a'},{kind:'edge',id:'e'}])assert.equal(graphMutationReason(p,blocks,selection),'locked');
 assert.equal(graphMutationReason(p,blocks,{kind:'node',id:'b'}),'');
 blocks[0].locked=false;assert.equal(graphMutationReason(p,blocks,{kind:'node',id:'a'},()=> 'geometry'),'role:geometry');
 for(const [path,id] of [['/output_plan/field_stride_xyz/2','setting-field_stride_xyz-2'],['/execution/backend','setting-backend'],['/output_plan/include_fields','setting-include_fields'],['/execution/dt_s','dt-input']])assert.equal(settingControl(diagnosticTarget({path},p)),id);
});
