import assert from 'node:assert/strict';
import test from 'node:test';
import { createHash } from 'node:crypto';
import { KernelClient } from '../src/friskoli_cad/web/kernel-client.mjs';
import { TaskStore, buildSubmission, matchesDraft, supportsTasks, TASK_STORAGE_KEY } from '../src/friskoli_cad/web/task-store.mjs';

const hash = 'a'.repeat(64);
const cap = {api_version:'0.2.0', placeables:[], task:{task_contract_version:'0.1.0',
  version_lock:{registry_sha256:hash, implementations:[{id:'core', version:'0.1.0', sha256:hash}]},
  execution:{semantics:'legacy-explicit-v1', backend:'numpy-cpu', default_seed:0},
  limits:{idempotency_retention_seconds:60}}};
const project = () => ({id:'draft', run:{protocol_version:'0.1.0', run_id:'legacy_metadata', channels:{length:{}, sample:{}}},
  domain:{counts_xyz:[1,1,1], spacing_um_xyz:[1,1,1]}, graph:{nodes:[], edges:[]}});
const submission = () => buildSubmission(cap, project(), {dt_s:.5, steps:2}, 'draft-a:4', 'request-original');
const memory = () => ({value:null, setItem(key,value) {this.value=value;}, getItem() {return this.value;}});
const makeStore = (client, options = {}) => {let n=0; return new TaskStore(client, {now:()=>1000, newId:()=>String(++n), ...options});};
const create = store => store.create(submission(), {draftToken:'draft-a', revision:4, idempotencyRetentionSeconds:60});
function task(status='queued', seq=1, step=0, completeness='none') {
  return {task_contract_version:'0.1.0', run_id:'task_123', status, last_event_seq:seq, cancel_requested:status==='cancelled',
    progress:{committed_step:step, simulation_time_s:step*.5},
    input_snapshot:{edit_revision:'draft-a:4',registry_sha256:hash,document_sha256:hash,scientific_sha256:hash,plan_sha256:hash},
    result:{href:'/api/runs/task_123/result',completeness},issues:[]};
}
function published(current) {
  const frames = Array.from({length:current.progress.committed_step+1}, (_,index)=>({sequence:index,step_index:index,time_s:index*.5,grid_revision:'grid-0',
    frame:{protocol_version:'0.1.0',frame_version:'0.2.0',run_id:'legacy_metadata',frame_index:index,time_s:index*.5,cells:[],events:[]}}));
  const body={task_contract_version:'0.1.0',run_id:'task_123',chunk_id:'chunk_0',frames};
  const raw=JSON.stringify(body), checksum=createHash('sha256').update(raw).digest('hex');
  const manifest={task_contract_version:'0.1.0',run_id:'task_123',status:current.status,completeness:current.result.completeness,
    input_snapshot:current.input_snapshot,progress:current.progress,issues:[],chunks:[{chunk_id:'chunk_0',href:'/api/runs/task_123/chunks/chunk_0',
      media_type:'application/json',sha256:checksum,bytes:Buffer.byteLength(raw),first_step:0,last_step:current.progress.committed_step}]};
  return {body,manifest,raw};
}
function service() {
  const value={current:task(), history:[task()], resultCalls:0, submissions:[], eventsCalls:[]};
  value.move = current => {value.current=current; value.history.push(current);};
  value.submitTask=async(s,key)=>{value.submissions.push({s:JSON.stringify(s),key}); return value.current;};
  value.task=async()=>value.current;
  value.taskEvents=async(id,after)=>{value.eventsCalls.push(after); return {run_id:id,events:value.history.filter(t=>t.last_event_seq>after).map(t=>({seq:t.last_event_seq,task:t})),next_after:value.current.last_event_seq,has_more:false,latest_seq:value.current.last_event_seq};};
  value.taskResult=async()=>{value.resultCalls++; return published(value.current).manifest;};
  value.taskChunk=async()=>published(value.current).body;
  value.cancelTask=async()=>{value.move(task('cancelled',value.current.last_event_seq+1)); return value.current;};
  return value;
}

test('exact capability gate preserves legacy fallback and copies real version locks', () => {
  assert.equal(supportsTasks({}),false); assert.equal(supportsTasks({...cap,task:{task_contract_version:'0.1.0-draft.1'}}),false);
  assert.equal(supportsTasks({...cap,task:{task_contract_version:'0.2.0'}}),false); assert.equal(supportsTasks(cap),true);
  const input=submission(); assert.deepEqual(input.version_lock,cap.task.version_lock);
  assert.deepEqual(input.output_plan,{frame_every_steps:1,observables:['length','sample'],include_fields:false});
  assert.equal(input.project.run.run_id,'legacy_metadata'); assert.equal(input.execution.seed,0);
  assert.throws(()=>{input.project.id='mutated';},TypeError);
  assert.throws(()=>buildSubmission({...cap,task:{task_contract_version:'0.1.0'}},project(),{},'x'),/task.invalid_capabilities/);
});

test('completed async result keeps task and legacy frame identities separate', async () => {
  const client=service(),store=makeStore(client),record=create(store);
  await store.submit(record.localId); client.move(task('running',2)); client.move(task('completed',3,2,'complete'));
  await store.poll(record.localId); const final=store.get(record.localId);
  assert.equal(final.status,'completed'); assert.equal(final.completeness,'complete'); assert.equal(final.cursor,3);
  assert.equal(final.id,'task_123'); assert.equal(final.replay.run.run_id,'legacy_metadata');
  assert.equal(final.replay.execution.task_run_id,'task_123'); assert.equal(final.replay.snapshots.length,3);
  assert.deepEqual(final.replay.snapshots[0].concentrations,{}); assert.equal(final.replay.execution.include_fields,false);
  assert.equal(store.needsPoll(final),false); assert.equal(record.runId,null); assert.equal(Object.isFrozen(final),true);
});

test('failed and interrupted output remains partial and read-only', async () => {
  for (const status of ['failed','interrupted','cancelled']) {
    const client=service(),store=makeStore(client),record=create(store); await store.submit(record.localId);
    client.move(task('running',2)); client.move(task(status,3,1,'partial'));
    await store.poll(record.localId); const current=store.get(record.localId);
    assert.equal(current.status,status); assert.equal(current.completeness,'partial'); assert.equal(current.replay.snapshots.length,2);
    assert.equal(current.replay.execution.completeness,'partial'); assert.throws(()=>{current.replay.snapshots.pop();},TypeError);
  }
});

test('queued cancellation publishes no fake output and never calls result endpoint', async () => {
  const client=service(),store=makeStore(client),record=create(store); await store.submit(record.localId);
  await store.cancel(record.localId); await store.poll(record.localId);
  assert.equal(store.get(record.localId).status,'cancelled'); assert.equal(store.get(record.localId).replay,null);
  assert.equal(client.resultCalls,0); assert.equal(store.needsPoll(store.get(record.localId)),false);
});

test('running cancel remains a request and completed publication may win', async () => {
  const client=service(),store=makeStore(client),record=create(store); await store.submit(record.localId);
  client.move(task('running',2)); await store.poll(record.localId);
  client.cancelTask=async()=>{const next=task('running',3);next.cancel_requested=true;client.move(next);return next;};
  await store.cancel(record.localId); assert.equal(store.get(record.localId).status,'running'); assert.equal(store.get(record.localId).task.cancel_requested,true);
  client.move(task('completed',4,2,'complete')); await store.poll(record.localId); assert.equal(store.get(record.localId).status,'completed');
});

test('uncertain submission retries identical key and bytes, including after reload', async () => {
  const storage=memory(),client=service(); const original=client.submitTask;
  client.submitTask=async(s,key)=>{await original(s,key);throw new TypeError('network connection lost');};
  const store=makeStore(client,{storage}),record=create(store);await store.submit(record.localId);
  assert.equal(store.get(record.localId).status,'submission_unknown');
  const restored=makeStore(client,{storage});restored.restore();client.submitTask=original;
  await restored.retry(record.localId);
  assert.equal(restored.get(record.localId).runId,'task_123');assert.equal(client.submissions.length,2);
  assert.deepEqual(client.submissions[0],client.submissions[1]);
  assert.equal(JSON.parse(storage.value).records[0].idempotencyKey,record.idempotencyKey);
});

test('retention deadline prohibits even explicit retry of an uncertain acceptance', async () => {
  let now=1000,calls=0;const store=makeStore({submitTask:async()=>{calls++;throw new Error('offline');}},{now:()=>now}),record=create(store);
  await store.submit(record.localId);now=62000;await store.retry(record.localId);
  assert.equal(calls,1);assert.equal(store.get(record.localId).error,'task.idempotency_window_expired');assert.equal(store.get(record.localId).paused,true);
});

test('event cursor expiry recovers current task and records incomplete history', async () => {
  const storage=memory(),client=service(),store=makeStore(client,{storage}),record=create(store);await store.submit(record.localId);
  client.move(task('running',2));client.move(task('completed',3,2,'complete'));
  client.taskEvents=async()=>{throw Object.assign(new Error('expired cursor'),{status:410,issue:{code:'task.cursor_expired'}});};
  await store.poll(record.localId); const current=store.get(record.localId);
  assert.equal(current.cursor,3);assert.equal(current.eventsComplete,false);assert.equal(current.replay.snapshots.length,3);
  assert.equal(JSON.parse(storage.value).records[0].eventsComplete,false);
});

test('page cursor is saved only after every event passes validation', async () => {
  const client=service(),store=makeStore(client),record=create(store);await store.submit(record.localId);
  client.taskEvents=async()=>({run_id:'task_123',events:[{seq:1,task:task()},{seq:3,task:task('running',3)}],next_after:3,latest_seq:3,has_more:false});
  await store.poll(record.localId);assert.equal(store.get(record.localId).cursor,0);assert.match(store.get(record.localId).error,/event_gap/);
});

test('bounded retries stop repeated errors and explicit reconnect resumes queries only', async () => {
  const client=service(),store=makeStore(client),record=create(store);await store.submit(record.localId);
  let calls=0;client.task=async()=>{calls++;throw new Error('offline');};
  for(let n=0;n<3;n++) await store.poll(record.localId);
  await store.tick();assert.equal(calls,3);assert.equal(store.get(record.localId).paused,true);
  client.task=async()=>task();await store.retry(record.localId);assert.equal(store.get(record.localId).paused,false);assert.equal(client.submissions.length,1);
});

test('known run restoration queries the saved ID and cursor without submitting', async () => {
  const storage=memory(),client=service(),store=makeStore(client,{storage}),record=create(store);
  await store.submit(record.localId);await store.poll(record.localId);
  const restored=makeStore(client,{storage});restored.restore();await restored.tick();
  assert.equal(client.submissions.length,1);assert.equal(client.eventsCalls.at(-1),1);
  assert.equal(restored.get(record.localId).restored,true);assert.equal(storage.value.includes('snapshots'),false);
});

test('late responses cannot match a newer draft or a newly opened document', async () => {
  const client=service(),store=makeStore(client),record=create(store);
  let finish; client.submitTask=()=>new Promise(resolve=>{finish=resolve;});
  const pending=store.submit(record.localId);const edited={draftToken:'draft-a',revision:5};const opened={draftToken:'draft-b',revision:4};
  finish(task());await pending;
  assert.equal(matchesDraft(store.get(record.localId),edited),false);assert.equal(matchesDraft(store.get(record.localId),opened),false);
  assert.equal(matchesDraft(store.get(record.localId),{draftToken:'draft-a',revision:4}),true);
});

test('stale task responses cannot roll terminal status backward', async () => {
  const client=service(),store=makeStore(client),record=create(store);await store.submit(record.localId);
  store.applyTask(record.localId,task('completed',3,2,'complete'));store.applyTask(record.localId,task('running',2));
  assert.equal(store.get(record.localId).status,'completed');
  assert.throws(()=>store.applyTask(record.localId,task('failed',4,1,'partial')),/terminal_changed/);
});

test('storage is bounded and does not evict unresolved active tasks', () => {
  const store=makeStore(service(),{maxRecords:1});create(store);
  assert.throws(()=>store.create(submission(),{draftToken:'other',revision:1,idempotencyRetentionSeconds:60}),/history_full/);
  const small=makeStore(service(),{maxBytes:20});assert.throws(()=>create(small),/storage_limit/);assert.equal(small.list().length,0);
});

test('unpublished or corrupt chunk metadata never replaces a previous readable result', async () => {
  const client=service(),store=makeStore(client),record=create(store);await store.submit(record.localId);
  client.move(task('running',2,1,'partial'));await store.poll(record.localId);const partial=store.get(record.localId).replay;
  client.move(task('completed',3,2,'complete'));
  client.taskResult=async()=>{const {manifest}=published(client.current);manifest.chunks[0].sha256='b'.repeat(64);return manifest;};
  await store.poll(record.localId);assert.equal(store.get(record.localId).replay,partial);assert.match(store.get(record.localId).error,/chunk_changed/);
});

test('HTTP task client sends mandatory key, validates bytes and SHA-256, and preserves issue codes', async () => {
  const current=task('completed',3,2,'complete'),data=published(current);const calls=[];
  const client=new KernelClient(async(path,options)=>{calls.push({path,options});return new Response(data.raw,{status:200,headers:{'Content-Type':'application/json'}});});
  await client.submitTask(submission(),'key-1');assert.equal(calls[0].options.headers['Idempotency-Key'],'key-1');
  assert.throws(()=>client.submitTask(submission(),''),/Idempotency-Key/);
  assert.deepEqual(await client.taskChunk('task_123',data.manifest.chunks[0]),data.body);
  await assert.rejects(client.taskChunk('task_123',{...data.manifest.chunks[0],sha256:'b'.repeat(64)}),/checksum/);
  await assert.rejects(client.taskChunk('task_123',{...data.manifest.chunks[0],bytes:1}),/byte length/);
  const failed=new KernelClient(async()=>new Response(JSON.stringify({issues:[{code:'task.cursor_expired',message:'Expired'}]}),{status:410}));
  await assert.rejects(failed.taskEvents('task_123',0),error=>error.status===410&&error.issue.code==='task.cursor_expired');
});

test('legacy HTTP fallback retains original replay payload shape', async () => {
  let sent;const client=new KernelClient(async(path,options)=>{sent={path,body:JSON.parse(options.body)};return new Response('{}');});
  await client.run(project(),{dt_s:.5,steps:2},'legacy-request');
  assert.equal(sent.path,'/api/replay');assert.deepEqual(Object.keys(sent.body),['project','dt_s','steps','request_id']);
});


test('cancellation is a bodyless POST as required by the stable HTTP contract', async () => {
  let sent;const client=new KernelClient(async(path,options)=>{sent={path,options};return new Response(JSON.stringify(task('cancelled',2)));});
  await client.cancelTask('task_123');
  assert.equal(sent.path,'/api/runs/task_123/cancel');assert.equal(sent.options.method,'POST');
  assert.equal(Object.hasOwn(sent.options,'body'),false);assert.equal(Object.hasOwn(sent.options,'headers'),false);
});


test('unchanged polling does not rewrite storage or redraw the task panel', async () => {
  let changes=0;const client=service(),store=makeStore(client,{onChange:()=>changes++}),record=create(store);
  await store.submit(record.localId);await store.poll(record.localId);const previousChanges=changes;
  await store.poll(record.localId);assert.equal(changes,previousChanges);
});

test('multi-page events preserve order and only commit fully consumed cursors', async () => {
  const client=service(),store=makeStore(client),record=create(store);await store.submit(record.localId);
  client.move(task('running',2));client.move(task('completed',3,2,'complete'));
  client.taskEvents=async(id,after)=>({run_id:id,events:client.history.filter(t=>t.last_event_seq>after).slice(0,1).map(t=>({seq:t.last_event_seq,task:t})),
    next_after:Math.min(after+1,3),has_more:after<2,latest_seq:3});
  await store.poll(record.localId);assert.equal(store.get(record.localId).cursor,3);assert.equal(store.get(record.localId).status,'completed');
});

test('missing or expired run lookup never resubmits an accepted task', async () => {
  const client=service(),store=makeStore(client),record=create(store);await store.submit(record.localId);
  client.task=async()=>{throw Object.assign(new Error('task gone'),{status:410});};
  await store.poll(record.localId);await store.tick();
  assert.equal(store.get(record.localId).status,'unavailable');assert.equal(store.get(record.localId).paused,true);assert.equal(client.submissions.length,1);
});

test('published frame envelopes reject sampled gaps and mixed legacy identities', async () => {
  for(const mutate of [body=>{body.frames[1].sequence=5;},body=>{body.frames[1].frame.run_id='task_123';}]) {
    const client=service(),store=makeStore(client),record=create(store);await store.submit(record.localId);
    client.move(task('running',2));client.move(task('completed',3,2,'complete'));
    client.taskChunk=async()=>{const {body}=published(client.current);mutate(body);return body;};
    await store.poll(record.localId);assert.equal(store.get(record.localId).replay,null);assert.match(store.get(record.localId).error,/invalid_frame_sequence/);
  }
});

test('submission is persisted before the network request can be accepted', async () => {
  const storage=memory();let observed;const store=makeStore({submitTask:async(s,key)=>{observed=JSON.parse(storage.getItem(TASK_STORAGE_KEY));return task();}},{storage});
  const record=create(store);await store.submit(record.localId);
  assert.equal(observed.records[0].idempotencyKey,record.idempotencyKey);assert.deepEqual(observed.records[0].submission,record.submission);
});
