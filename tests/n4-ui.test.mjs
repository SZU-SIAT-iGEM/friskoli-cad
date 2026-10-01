import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {numericDesignParameters,designRunQueue,designPackagePayload} from '../src/friskoli_cad/web/design-panel.mjs';
import {readWorkspace,writeWorkspace} from '../src/friskoli_cad/web/workspace.mjs';
import {TaskStore,TASK_STORAGE_KEY,buildSubmission} from '../src/friskoli_cad/web/task-store.mjs';
const project=JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/chemotaxis_pts_a.project.json',import.meta.url)));
const brief={brief_version:'0.1.0',id:'d',name:'Design',goal:{metric:'mean_displacement_um',direction:'maximize',group_id:'cells'},chassis:{name:'Test',provenance:'User'},variables:[{node_id:'n',parameter:'p',values:[1,2]}],constraints:[],seeds:[2,5],max_runs:32};
const design={design_version:'0.1.0',id:'d',brief,baseline_project:project,settings:{dt_s:.05,steps:2},budget:{candidate_count:3,repeats:2,total_runs:6,total_steps:12},excluded:[],candidates:[{id:'a',name:'A',kind:'candidate',project,explanation:'Scan',soft_penalty:0,overrides:[]},{id:'b',name:'B',kind:'candidate',project,explanation:'Scan',soft_penalty:0,overrides:[]},{id:'control',name:'Control',kind:'control',project,explanation:'Control',soft_penalty:0,overrides:[]}]};
test('design scan only exposes actual registered finite number/integer parameters',()=>{
 const p={graph:{nodes:[{id:'n',module_id:'m',module_version:'1',parameters:{a:{value:1},b:{value:'x'},c:{value:2},d:{value:3},e:{value:Infinity}}}]}};
 const m=new Map([['m@1',{parameters:{a:{type:'number'},b:{type:'string'},c:{type:'integer'},d:{type:'number',enum:[3]},e:{type:'number'}}}]]);
 assert.deepEqual(numericDesignParameters(p,m).map(p=>p.parameter),['a','c']);
});
test('design batch enumerates every candidate and control across seeds with independent frozen inputs',()=>{
 const queue=designRunQueue(design);assert.equal(queue.length,6);assert.deepEqual(queue.map(q=>[q.design_ref.candidate_id,q.seed]),[['a',2],['a',5],['b',2],['b',5],['control',2],['control',5]]);
 queue[0].project.id='changed';assert.notEqual(queue[1].project.id,'changed');assert.notEqual(project.id,'changed');
 assert.throws(()=>designRunQueue({...design,candidates:[design.candidates[2]]}),/No feasible/);
});
test('Workspace0.5 preserves design and editable brief separately without modifying scientific Project',()=>{
 const state=readWorkspace(project);state.design=design;state.designBrief=brief;const document=writeWorkspace(state),loaded=readWorkspace(document);
 assert.equal(document.workspace_format_version,'0.5.0');assert.deepEqual(loaded.design,design);assert.deepEqual(loaded.designBrief,brief);assert.deepEqual(loaded.project,project);assert.equal(loaded.project.design,undefined);
 const old={...document,workspace_format_version:'0.4.0'};delete old.design;delete old.design_brief;assert.equal(readWorkspace(old).design,null);
});
test('design package contains only records linked to the selected design',()=>{
 const runs=[{id:'r',design_ref:{design_id:'d'}},{id:'unrelated',design_ref:{design_id:'other'}},{id:'ordinary'}];
 const payload=designPackagePayload({design,runs},{workspace_format_version:'0.5.0'},{catalog_version:'0.4.0'});assert.deepEqual(payload.runs,[runs[0]]);payload.runs[0].id='copy';assert.equal(runs[0].id,'r');
});
test('TaskStore design_ref persists independently of scientific submission and survives restore',()=>{
 let serial=0;const data=new Map(),storage={setItem:(k,v)=>data.set(k,v),getItem:k=>data.get(k)};
 const store=new TaskStore({}, {storage,newId:()=>String(++serial)}),submission={task_contract_version:'0.4.0',project,execution:{seed:2,steps:2},output_plan:{}},design_ref={design_id:'d',candidate_id:'a',candidate_name:'A'};
 const record=store.create(submission,{draftToken:'draft',revision:1,idempotencyRetentionSeconds:60,design_ref});assert.deepEqual(record.design_ref,design_ref);assert.equal(record.submission.design_ref,undefined);
 assert.ok(JSON.parse(data.get(TASK_STORAGE_KEY)).records[0].design_ref);const restored=new TaskStore({}, {storage}).restore();assert.deepEqual(restored[0].design_ref,design_ref);assert.ok(Object.isFrozen(record.design_ref));
});

test('malformed saved design and brief fail before UI rendering',()=>{
 const state=readWorkspace(project);state.design=design;state.designBrief=brief;const saved=writeWorkspace(state);
 for(const mutate of [x=>delete x.design.brief,x=>delete x.design.budget,x=>x.design.candidates[0].project={},x=>x.design_brief.seeds=[0,0],x=>x.design_brief.chassis.provenance=null]){const bad=structuredClone(saved);mutate(bad);assert.throws(()=>readWorkspace(bad),/Invalid/);}
});
test('explicit design history removal rejects active records and restores history after storage failure',()=>{
 let fail=false,serial=0;const store=new TaskStore({}, {newId:()=>String(++serial),storage:{setItem(){if(fail)throw new Error('quota');}}});
 const r=store.create({project,execution:{seed:1},output_plan:{}},{draftToken:'d',revision:1,idempotencyRetentionSeconds:60,design_ref:{design_id:'d',candidate_id:'a',candidate_name:'A'}});
 assert.throws(()=>store.removeDesignRecords('d'),/design_active/);store.replace(r.localId,{status:'completed'});store.cache.set(r.localId,'result');fail=true;assert.throws(()=>store.removeDesignRecords('d'),/quota/);assert.ok(store.get(r.localId));assert.equal(store.cache.get(r.localId),'result');fail=false;store.removeDesignRecords('d');assert.equal(store.list().length,0);assert.equal(store.cache.size,0);
});

test('short design runs retain a longer saved-frame interval and still request a final frame',()=>{
 const state=readWorkspace(project);state.settings={dt_s:.05,steps:2,frame_every_steps:7};assert.equal(readWorkspace(writeWorkspace(state)).settings.frame_every_steps,7);
 const hash='a'.repeat(64),cap={task_contract_version:'0.4.0',version_lock:{registry_sha256:hash,implementations:[]},execution:{semantics:project.execution_profile,backend:'numpy-cpu',default_seed:0}};
 const submission=buildSubmission({task_profiles:{[project.execution_profile]:cap}},project,state.settings,'revision','request');assert.equal(submission.output_plan.frame_every_steps,7);assert.equal(submission.execution.steps,2);
});
