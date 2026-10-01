import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {defaultParameters,missingParameters} from '../src/friskoli_cad/web/graph-edit.mjs';
import {readWorkspace,writeWorkspace,metricsCSV} from '../src/friskoli_cad/web/workspace.mjs';
import {compareRuns,validateMetrics} from '../src/friskoli_cad/web/metrics.mjs';
import {copyPopulationBranch} from '../src/friskoli_cad/web/templates.mjs';
import {scatterBlock} from '../src/friskoli_cad/web/population.mjs';
import {buildSubmission,TaskStore,validateTaskLifecycle} from '../src/friskoli_cad/web/task-store.mjs';
const template=JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/chemotaxis_pts_a.project.json',import.meta.url)));

test('scientific defaults require a declaration; numeric minima and ambiguous species are not defaults',()=>{
  const manifest={id:'test',version:'1',parameters:{rate:{type:'number',minimum:0},species:{type:'string'},calibrated:{type:'number',unit:'s'}},declaration:{default_parameters:{calibrated:2}}};
  const parameters=defaultParameters(manifest,['a','b']);
  assert.deepEqual(Object.keys(parameters),['calibrated']);assert.equal(parameters.calibrated.provenance.kind,'example');
  const graph={nodes:[{id:'n',module_id:'test',module_version:'1',parameters}]};
  assert.deepEqual(missingParameters(graph,new Map([['test@1',manifest]])).map(x=>x.parameter),['rate','species']);
});

const metric=(value=1)=>({metric_version:'0.1.0',observation_id:template.observation.id,by_group:Object.fromEntries(Object.entries(template.groups).map(([id,g])=>[id,{initial_count:g.ids.length,live_count:g.ids.length,mean_displacement_um:value,region_fraction:.5,ever_arrived_fraction:.5,mean_residence_s:value}]))});
function record(seed,value=1){return{id:`r${seed}`,project:structuredClone(template),settings:{seed,dt_s:.05,steps:200},status:'completed',replay:{snapshots:[{frame:{frame_index:200,time_s:10},metrics:metric(value)}]}};}
test('N3 workspace saves as current workspace with profile, observation and sparse output settings intact',()=>{
  const state=readWorkspace(template);state.settings={dt_s:.05,steps:200,frame_every_steps:7,include_fields:false};
  const saved=writeWorkspace(state);assert.equal(saved.workspace_format_version,'0.5.0');
  assert.deepEqual(readWorkspace(saved).project,template);assert.equal(readWorkspace(saved).settings.frame_every_steps,7);
});
test('comparison keeps variants separate, computes sample SD, and rejects partial, duplicate and incompatible runs',()=>{
  const a=record(1,2),b=record(2,4),group=Object.keys(template.groups)[0];
  const stats=compareRuns([a,b]);assert.equal(stats.length,1);assert.equal(stats[0].byGroup[group].mean_displacement_um.mean,3);assert.equal(stats[0].byGroup[group].mean_displacement_um.sd,Math.sqrt(2));
  assert.throws(()=>compareRuns([a,{...b,status:'cancelled'}]),/CompleteOnly/);
  assert.throws(()=>compareRuns([a,record(1)]),/DuplicateSeed/);
  const different=record(3);different.settings.dt_s=.1;assert.throws(()=>compareRuns([a,different]),/Mismatch/);
  const variant=record(3);variant.project.graph.nodes[0].parameters.test={value:2};assert.equal(compareRuns([a,variant]).length,2);
});
test('metrics validation rejects missing/nonfinite/fraction errors and CSV uses recorded values',()=>{
  const values=metric(2);validateMetrics(values,template);
  const group=Object.keys(values.by_group)[0],bad=structuredClone(values);bad.by_group[group].region_fraction=1.1;assert.throws(()=>validateMetrics(bad,template),/invalid_metrics/);
  const replay=record(2,2).replay,csv=metricsCSV(replay);assert.match(csv,/observation_id,group_id/);assert.match(csv,/mean_residence_s/);assert.match(csv,/^200,10,/m);
});
test('different implementation locks stay separate while same lock distinct seeds aggregate',()=>{
  const a=record(1),b=record(2),submission=seed=>({task_contract_version:'0.4.0',execution:{seed,backend:'numpy-cpu'},version_lock:{registry_sha256:'a'.repeat(64),implementations:[{id:'motion',version:'1',sha256:'b'.repeat(64)}]}});
  a.submission=submission(1);b.submission=submission(2);assert.equal(compareRuns([a,b]).length,1);
  b.submission.version_lock.implementations[0].sha256='c'.repeat(64);const result=compareRuns([a,b]);assert.equal(result.length,2);assert.ok(result.every(v=>v.provenance.includes('Task 0.4.0')));
});
test('death provenance is one-to-one with events and matches frozen health policy and sampling',()=>{
  const project={graph:{nodes:[{id:'health',module_id:'life.health_balance',owner:{kind:'population',id:'g'},parameters:{policy:{value:'simplified'}}}]}};
  const submission={task_contract_version:'0.4.0',project},detail={cell_id:'c',group_id:'g',time_s:.2,node_id:'health',module_id:'life.health_balance',policy:'simplified',health:.1,death_hazard_per_min:3,probability:.5,random_draw:.1};
  const item={time_s:.3,frame:{events:[{type:'death',cell_id:'c',time_s:.2}]},lifecycle_details:{lifecycle_version:'0.1.0',deaths:[detail]}};
  assert.equal(validateTaskLifecycle(item,submission).lifecycle_details.deaths.length,1);
  for(const change of [d=>{d.deaths=[];},d=>d.deaths.push(structuredClone(detail)),d=>d.deaths[0].time_s=.1,d=>d.deaths[0].node_id='missing',d=>d.deaths[0].group_id='other',d=>d.deaths[0].policy='rebuilt',d=>d.deaths[0].random_draw=.5,d=>d.deaths[0].random_draw=0]){
    const bad=structuredClone(item);change(bad.lifecycle_details);assert.throws(()=>validateTaskLifecycle(bad,submission),/invalid_lifecycle_details/);
  }
});
test('branch copy remaps ownership, ports and channels while preserving shared providers and timing',()=>{
  const project=structuredClone(template),source=Object.keys(project.groups)[0];project.groups.copy=structuredClone(project.groups[source]);
  const original=project.graph.nodes.filter(n=>n.owner.kind==='population'&&n.owner.id===source),before=structuredClone(project.graph);
  const ids=copyPopulationBranch(project,source,'copy');assert.equal(ids.length,original.length);
  assert.ok(ids.every(id=>project.graph.nodes.find(n=>n.id===id).owner.id==='copy'));
  for(const edge of before.edges.filter(e=>original.some(n=>n.id===e.to.node)))assert.ok(project.graph.edges.some(e=>e.id.startsWith('copy_')&&e.timing===edge.timing&&e.to.port===edge.to.port));
  assert.equal(project.graph.nodes.filter(n=>n.owner.kind==='environment').length,before.nodes.filter(n=>n.owner.kind==='environment').length);
  assert.ok(Object.values(project.run.channels).some(c=>c.group_id==='copy'));
});
test('spatial scatter preserves Project0.5 and rejects impossible packing atomically',()=>{
  const project=structuredClone(template),group=Object.keys(project.groups)[0];
  const block={id:group,center:[20,20,2],size:[12,12,4],length:2,diameter:.8,count:4,seed:9,rotation:[0,0,0],dirty:true};
  project.domain={geometry:'volume',counts_xyz:[20,20,2],spacing_um_xyz:[2,2,2]};project.graph.nodes=project.graph.nodes.filter(n=>n.owner.kind==='population');
  scatterBlock(project,block);assert.equal(project.project_version,'0.5.0');
  const positions=project.groups[group].positions_um;for(let i=0;i<positions.length;i++)for(let j=0;j<i;j++)assert.ok(Math.hypot(...positions[i].map((v,k)=>v-positions[j][k]))>=2);
  const before=structuredClone(project);assert.throws(()=>scatterBlock(project,{...block,count:500,size:[2,2,2]}),/scatterPackingFailed/);assert.deepEqual(project,before);
});

test('Task0.4 publishes sparse final frame with accumulated lifecycle events and metrics',async()=>{
  const project=structuredClone(template);project.graph.nodes=[];project.graph.edges=[];project.run.channels={};
  const group=Object.keys(project.groups)[0];project.graph.nodes=[{id:'health',module_id:'life.health_balance',owner:{kind:'population',id:group},parameters:{policy:{value:'simplified'}}}];project.groups={[group]:{...project.groups[group],ids:['a'],positions_um:[[1,1,1]],orientation_xyzw:[[0,0,0,1]],initial_geometry:[project.groups[group].initial_geometry[0]]}};project.run.groups=[group];
  const hash='a'.repeat(64),cap={task_contract_version:'0.4.0',version_lock:{registry_sha256:hash,implementations:[]},execution:{semantics:project.execution_profile,backend:'numpy-cpu',default_seed:0}};
  const submission=buildSubmission({task_profiles:{[project.execution_profile]:cap}},project,{dt_s:.1,steps:5,frame_every_steps:3,include_fields:false},'r','req');
  assert.equal(submission.output_plan.frame_every_steps,3);
  const input_snapshot={document_sha256:hash,scientific_sha256:hash,registry_sha256:hash,plan_sha256:hash,edit_revision:'r'};
  const task={task_contract_version:'0.4.0',run_id:'n3',status:'completed',last_event_seq:1,progress:{committed_step:5,simulation_time_s:.5},result:{completeness:'complete'},input_snapshot};
  const chunks=[0,3,5].map((step,i)=>({chunk_id:`c${i}`,href:`/api/runs/n3/chunks/c${i}`,sha256:hash,bytes:100,media_type:'application/json',first_step:step,last_step:step}));
  const cell={id:'a',group_id:group,position_um:[1,1,1],orientation_xyzw:[0,0,0,1],geometry:project.groups[group].initial_geometry[0],channels:{}};
  const bodies=chunks.map((c,i)=>({task_contract_version:'0.4.0',run_id:'n3',chunk_id:c.chunk_id,frames:[{sequence:i,step_index:c.first_step,time_s:c.first_step*.1,grid_revision:'g',object_states:{},lifecycle_details:{lifecycle_version:'0.1.0',deaths:i===1?[{cell_id:'a',group_id:group,time_s:.2,node_id:'health',module_id:'life.health_balance',policy:'simplified',health:0,death_hazard_per_min:100,probability:.5,random_draw:.1}]:[]},metrics:{metric_version:'0.1.0',observation_id:project.observation.id,by_group:{[group]:{initial_count:1,live_count:i?0:1,mean_displacement_um:i?null:0,region_fraction:i?null:0,ever_arrived_fraction:0,mean_residence_s:0}}},frame:{frame_version:'0.2.0',protocol_version:'0.1.0',frame_index:c.first_step,time_s:c.first_step*.1,run_id:project.run.run_id,cells:i?[]:[cell],events:i===1?[{type:'death',cell_id:'a',time_s:.2}]:[]}}]}));
  const client={async taskResult(){return{...task,completeness:'complete',chunks};},async taskChunk(_id,c){return bodies.find(b=>b.chunk_id===c.chunk_id);}};
  const store=new TaskStore(client,{newId:()=> 'x'}),r=store.create(submission,{draftToken:'draft',revision:1,idempotencyRetentionSeconds:60});store.applyTask(r.localId,task);await store.published(r.localId);
  const replay=store.get(r.localId).replay;assert.deepEqual(replay.snapshots.map(s=>s.frame.frame_index),[0,3,5]);assert.equal(replay.snapshots[1].frame.events[0].time_s,.2);assert.equal(replay.snapshots[2].metrics.by_group[group].live_count,0);
  const running={...task,status:'running',progress:{committed_step:4,simulation_time_s:.4},result:{completeness:'partial'}};
  const partialClient={...client,async taskResult(){return{...running,completeness:'partial',chunks:chunks.slice(0,2)};}};
  const partialStore=new TaskStore(partialClient,{newId:()=> 'partial'}),pr=partialStore.create(submission,{draftToken:'draft',revision:1,idempotencyRetentionSeconds:60});
  partialStore.applyTask(pr.localId,running);await partialStore.published(pr.localId);
  assert.deepEqual(partialStore.get(pr.localId).replay.snapshots.map(s=>s.frame.frame_index),[0,3]);

});

test('two eight-seed batches retain every result and protected comparisons survive capacity pressure',()=>{
  let serial=0;const protectedIds=new Set(),store=new TaskStore({}, {maxRecords:32,newId:()=>String(++serial),isProtected:r=>protectedIds.has(r.localId)});
  const submission={project:template,execution:{steps:1},output_plan:{}},options={draftToken:'d',revision:1,idempotencyRetentionSeconds:600};
  const add=()=>{const r=store.create(submission,options);store.replace(r.localId,{status:'completed'});return r;};
  const first=[];for(let batch=0;batch<2;batch++){store.assertCapacity(8);for(let i=0;i<8;i++){const r=add();first.push(r.localId);protectedIds.add(r.localId);}}
  assert.equal(store.list().length,16);assert.ok(first.every(id=>store.get(id)));
  for(let i=0;i<20;i++)add();assert.equal(store.list().length,32);assert.ok(first.every(id=>store.get(id)));
  for(const r of store.list())protectedIds.add(r.localId);const before=store.list();
  assert.throws(()=>store.assertCapacity(8),/history_full/);assert.throws(()=>add(),/history_full/);assert.deepEqual(store.list(),before);
});

test('failed history persistence preserves evicted records and cached results atomically',()=>{
  let serial=0,fail=false;const store=new TaskStore({}, {maxRecords:1,newId:()=>String(++serial),storage:{setItem(){if(fail)throw new Error('quota');}}});
  const submission={project:template,execution:{steps:1},output_plan:{}},options={draftToken:'d',revision:1,idempotencyRetentionSeconds:600};
  const first=store.create(submission,options);store.replace(first.localId,{status:'completed'});store.cache.set(first.localId,'retained-result');
  const before=store.list();fail=true;assert.throws(()=>store.create(submission,options),/quota/);assert.deepEqual(store.list(),before);assert.equal(store.cache.get(first.localId),'retained-result');
  fail=false;store.maxBytes=1;assert.throws(()=>store.create(submission,options),/storage_limit/);assert.deepEqual(store.list(),before);assert.equal(store.cache.get(first.localId),'retained-result');
});
