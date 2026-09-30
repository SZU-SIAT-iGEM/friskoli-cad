import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {buildSubmission, taskCapability, TaskStore} from '../src/friskoli_cad/web/task-store.mjs';

const profile = 'spatial-unbiased-v1';
const project = JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/spatial_baseline.project.json', import.meta.url)));
const hash = 'a'.repeat(64);
const cap = {task_contract_version:'0.3.0',version_lock:{registry_sha256:hash,implementations:[]},
  execution:{semantics:profile,backend:'numpy-cpu',default_seed:0}};
const caps = {task_profiles:{[profile]:cap}};

test('spatial capability gating and seeded field defaults are exact', () => {
  const input=buildSubmission(caps,project,{dt_s:.01,steps:1},'r1','request');
  assert.equal(input.task_contract_version,'0.3.0');
  assert.equal(input.execution.seed,project.random_seed);
  assert.equal(input.output_plan.include_fields,true);
  const override=buildSubmission(caps,project,{dt_s:.01,steps:1,seed:42,include_fields:false},'r1','request');
  assert.equal(override.execution.seed,42);assert.equal(override.output_plan.include_fields,false);
  assert.equal(override.project.random_seed,project.random_seed);
  for(const p of [{...project,project_version:'0.3.0'},{...project,execution_profile:'unknown'}]) assert.equal(taskCapability(caps,p),null);
  assert.equal(taskCapability({task_profiles:{[profile]:{...cap,task_contract_version:'0.2.0'}}},project),null);
  assert.throws(()=>buildSubmission(caps,project,{seed:-1},'r1','request'),/invalid_seed/);
  assert.throws(()=>buildSubmission(caps,project,{include_fields:'true'},'r1','request'),/invalid_output_plan/);
});

function resultFixture(mutate=()=>{}) {
  const submission=buildSubmission(caps,project,{dt_s:.01,steps:1},'r1','request');
  const input_snapshot={document_sha256:hash,scientific_sha256:hash,registry_sha256:hash,plan_sha256:hash,edit_revision:'r1'};
  const task={task_contract_version:'0.3.0',run_id:'spatial_task',status:'completed',last_event_seq:1,
    progress:{committed_step:1,simulation_time_s:.01},result:{completeness:'complete'},input_snapshot};
  const [nx,ny,nz]=project.domain.counts_xyz;
  const concentrations=Object.fromEntries(project.graph.nodes.filter(n=>n.module_id==='field.diffusive_local').map(n=>
    [n.parameters.species.value,{unit:'uM',values_zyx:Array.from({length:nz},()=>Array.from({length:ny},()=>Array(nx).fill(1)))}]));
  const object_states=Object.fromEntries(project.graph.nodes.filter(n=>['material.degradable_box','source.finite_local'].includes(n.module_id))
    .map(n=>[n.id,{object_type:n.module_id==='material.degradable_box'?'material.degradable_box':'source.attractant',remaining_molecules:n.parameters.initial_molecules.value}]));
  const chunks=[0,1].map(i=>({chunk_id:`c${i}`,href:`/api/runs/spatial_task/chunks/c${i}`,sha256:hash,bytes:100,media_type:'application/json',first_step:i,last_step:i}));
  const bodies=chunks.map((chunk,i)=>({task_contract_version:'0.3.0',run_id:'spatial_task',chunk_id:chunk.chunk_id,frames:[{
    sequence:i,step_index:i,time_s:i*.01,grid_revision:'grid',object_states:structuredClone(object_states),concentrations:structuredClone(concentrations),
    frame:{protocol_version:'0.1.0',frame_version:'0.2.0',frame_index:i,time_s:i*.01,run_id:project.run.run_id,cells:[],events:[]}}]}));
  mutate(bodies[1].frames[0]);
  const manifest={task_contract_version:'0.3.0',run_id:'spatial_task',input_snapshot,chunks,status:'completed',completeness:'complete',progress:task.progress};
  const client={async taskResult(){return manifest;},async taskChunk(_id,chunk){return bodies.find(b=>b.chunk_id===chunk.chunk_id);}};
  const store=new TaskStore(client,{newId:()=> 'x'});
  const record=store.create(submission,{draftToken:'spatial',revision:1,idempotencyRetentionSeconds:60});
  store.applyTask(record.localId,task);
  return {store,id:record.localId,concentrations};
}

test('complete task fields reach normalizeReplay without synthetic empty fields', async()=>{
  const {store,id,concentrations}=resultFixture();await store.published(id);
  assert.deepEqual(store.get(id).replay.snapshots[1].concentrations,concentrations);
  assert.equal(store.get(id).replay.execution.include_fields,true);
});

for(const [name,mutate] of [
  ['missing',item=>delete item.concentrations],['empty',item=>item.concentrations={}],
  ['unit',item=>Object.values(item.concentrations)[0].unit='mM'],
  ['shape',item=>Object.values(item.concentrations)[0].values_zyx=[]],
  ['negative',item=>Object.values(item.concentrations)[0].values_zyx[0][0][0]=-1],
  ['nonfinite',item=>Object.values(item.concentrations)[0].values_zyx[0][0][0]=NaN],
]) test(`requested field ${name} is rejected before replay is published`,async()=>{
  const {store,id}=resultFixture(mutate);await assert.rejects(store.published(id),/task\.(fields_missing|invalid_field)/);
  assert.equal(store.get(id).replay,null);
});

for(const [name,mutate] of [
  ['missing',item=>delete item.object_states],['empty',item=>item.object_states={}],
  ['type',item=>Object.values(item.object_states)[0].object_type='unregistered'],
  ['negative',item=>Object.values(item.object_states)[0].remaining_molecules=-1],
  ['overflow',item=>Object.values(item.object_states)[0].remaining_molecules=Number.MAX_VALUE],
  ['nonfinite',item=>Object.values(item.object_states)[0].remaining_molecules=NaN],
]) test(`object inventory ${name} is rejected before replay is published`,async()=>{
  const {store,id}=resultFixture(mutate);await assert.rejects(store.published(id),/task\.(objects_missing|invalid_object_state)/);
  assert.equal(store.get(id).replay,null);
});
