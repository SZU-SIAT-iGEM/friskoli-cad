import test from 'node:test';
import assert from 'node:assert/strict';
import {planDesignBatch} from '../src/friskoli_cad/web/design-panel.mjs';
import {TaskStore,buildSubmission,TASK_STORAGE_KEY} from '../src/friskoli_cad/web/task-store.mjs';
const design={id:'d',brief:{seeds:[1,2,3]},candidates:[{id:'a',name:'A',kind:'candidate',project:{run:{channels:{}}}},{id:'b',name:'B',kind:'control',project:{run:{channels:{}}}}]};
const run=(seed,status='completed',candidate='a')=>({design_ref:{design_id:'d',candidate_id:candidate},submission:{execution:{seed}},status,completeness:status==='completed'?'complete':'none'});
test('bounded batches skip complete and uncertain repeats, retain failures, and select candidates/seeds',()=>{
  const history=[run(1),run(2,'failed'),run(3,'submission_unknown')],before=structuredClone(history),plan=planDesignBatch(design,history);
  assert.deepEqual(plan.queue.map(q=>[q.design_ref.candidate_id,q.seed]),[['a',2],['b',1],['b',2],['b',3]]);assert.equal(plan.completed,1);assert.equal(plan.active,1);assert.deepEqual(history,before);
  assert.deepEqual(planDesignBatch(design,history,{candidateIds:['a'],seeds:[2]}).queue.map(q=>q.seed),[2]);
  assert.equal(planDesignBatch(design,[],{batchSize:2}).remainingAfterBatch,4);
  assert.throws(()=>planDesignBatch(design,[],{seeds:[4]}),/outside/);
});
test('batch precheck preserves protected evidence and fails before adding any tasks',()=>{
  const store=new TaskStore({}, {maxRecords:2,maxBytes:40000,isProtected:()=>true,newId:()=>crypto.randomUUID()});
  const submission={project:{run:{}},execution:{seed:1},output_plan:{}};
  const saved=store.create(submission,{draftToken:'x',revision:1,idempotencyRetentionSeconds:60});store.replace(saved.localId,{status:'completed'});
  assert.throws(()=>store.assertBatchCapacity([submission,submission]),/history_full/);assert.equal(store.list().length,1);
  assert.throws(()=>store.assertBatchCapacity([{...submission,padding:'x'.repeat(40000)}]),/storage_limit/);assert.equal(store.list().length,1);
});
test('async durability failure prohibits network submission',async()=>{
  let submitted=0;const storage={setItem(){},flush:async()=>{throw Error('disk failed');}};
  const store=new TaskStore({submitTask:async()=>{submitted++;}}, {storage,newId:()=>crypto.randomUUID()});
  const record=store.create({project:{},execution:{},output_plan:{}},{draftToken:'x',revision:1,idempotencyRetentionSeconds:60});
  await store.submit(record.localId);assert.equal(submitted,0);assert.equal(store.get(record.localId).status,'submission_unknown');assert.match(store.persistenceError,/disk failed/);
});
test('submission waits for exact request and key durability before network acceptance',async()=>{
  let committed=false,release;const gate=new Promise(r=>release=r),writes=[],calls=[];
  const storage={setItem(k,v){writes.push(JSON.parse(v));},flush:async()=>{await gate;committed=true;}};
  const store=new TaskStore({submitTask:async(submission,key)=>{assert.equal(committed,true);calls.push({submission,key});throw Error('response lost');}},{storage,newId:()=>crypto.randomUUID()});
  const frozen={task_contract_version:'0.4.0',request_id:'original',project:{run:{}},version_lock:{registry_sha256:'a'.repeat(64)},execution:{seed:7},output_plan:{}};
  const record=store.create(frozen,{draftToken:'x',revision:1,idempotencyRetentionSeconds:60});const pending=store.submit(record.localId);assert.equal(calls.length,0);release();await pending;
  assert.deepEqual(calls[0].submission,writes[0].records[0].submission);assert.equal(calls[0].key,writes[0].records[0].idempotencyKey);
  await store.retry(record.localId);assert.deepEqual(calls[1],calls[0]);
});
test('32 frozen repeats plus retained failed attempts fit the bounded durable metadata store',()=>{
  let serial=0;const storage={setItem(k,v){this.value=v;},getItem(){return this.value;}},store=new TaskStore({}, {storage,newId:()=>String(++serial),isProtected:()=>true});
  for(let i=0;i<40;i++){const record=store.create({task_contract_version:'0.4.0',request_id:'request-'+i,project:{run:{}},execution:{seed:i%32,steps:2},output_plan:{}},{draftToken:'d',revision:i,idempotencyRetentionSeconds:60,design_ref:{design_id:'d',candidate_id:'a',candidate_name:'A'}});store.replace(record.localId,{status:i<8?'failed':'completed',completeness:i<8?'none':'complete'});}
  assert.equal(store.list().length,40);const restored=new TaskStore({}, {storage}).restore();assert.equal(restored.length,40);assert.equal(restored.filter(r=>r.status==='failed').length,8);assert.equal(JSON.parse(storage.value).records.some(r=>Object.hasOwn(r,'replay')),false);
});
test('candidate seeds are frozen into project as well as execution selection',()=>{
  const d=structuredClone(design);for(const c of d.candidates)c.project.random_seed=42;
  const result=planDesignBatch(d,[],{batchSize:2});assert.deepEqual(result.queue.map(x=>x.project.random_seed),[1,2]);assert.equal(d.candidates[0].project.random_seed,42);
});
