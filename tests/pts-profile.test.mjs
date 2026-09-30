import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {readWorkspace, writeWorkspace} from '../src/friskoli_cad/web/workspace.mjs';
import {buildSubmission, taskCapability, TaskStore} from '../src/friskoli_cad/web/task-store.mjs';

const profile = 'conservative-pts-bulk-v1';
const project = JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/pts_bulk.project.json', import.meta.url)));
const legacy = JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/workspace_3d.project.json', import.meta.url)));
const hash = 'a'.repeat(64), other = 'b'.repeat(64);
const capability = (version, semantics, registryHash) => ({task_contract_version:version,
  version_lock:{registry_sha256:registryHash, implementations:[]},
  execution:{semantics, backend:'numpy-cpu', default_seed:0}, limits:{idempotency_retention_seconds:60}});
const caps = {task:capability('0.1.0','legacy-explicit-v1',hash),
  task_profiles:{[profile]:capability('0.2.0',profile,other)}};

test('profile selects an exact independent task contract and version lock', () => {
  const input=buildSubmission(caps,project,{dt_s:.01,steps:3},'pts:1','pts-request');
  assert.equal(input.task_contract_version,'0.2.0');
  assert.equal(input.version_lock.registry_sha256,other);
  assert.equal(input.execution.semantics,profile);
  const old=buildSubmission(caps,legacy,{dt_s:.5,steps:2},'old:1','old-request');
  assert.equal(old.task_contract_version,'0.1.0');
  assert.equal(old.version_lock.registry_sha256,hash);
  assert.throws(()=>buildSubmission({task:caps.task},project,{dt_s:.01,steps:2},'x'),/unsupported_contract/);
  assert.equal(taskCapability(caps,{...project,execution_profile:'unknown'}),null);
  assert.throws(()=>buildSubmission({task_profiles:{[profile]:{...caps.task_profiles[profile],task_contract_version:'0.3.0'}}},project,{},'x'),/unsupported_contract/);
});

test('project profile and source parameter references survive workspace save and reopen', () => {
  const state=readWorkspace(project), saved=writeWorkspace(state), reopened=readWorkspace(saved);
  assert.deepEqual(reopened.project,project);
  assert.equal(reopened.project.execution_profile,profile);
  assert.throws(()=>readWorkspace({...project,execution_profile:'unknown'}),/Invalid project/);
  assert.throws(()=>readWorkspace({...legacy,execution_profile:profile}),/Invalid project/);
});

test('restored PTS task queries its existing ID and rejects a cross-version server response', async () => {
  let stored=null, submissions=0, queries=0;
  const storage={setItem(_key,value){stored=value;},getItem(){return stored;}};
  const input=buildSubmission(caps,project,{dt_s:.01,steps:2},'pts:1','pts-request');
  let version='0.2.0';
  const task=()=>({task_contract_version:version,run_id:'pts_task',status:'queued',last_event_seq:1,
    progress:{committed_step:0,simulation_time_s:0},result:{completeness:'none'},issues:[],
    input_snapshot:{edit_revision:'pts:1',registry_sha256:other,document_sha256:hash,scientific_sha256:hash,plan_sha256:hash}});
  const client={async submitTask(){submissions++;return task();},async task(){queries++;return task();},
    async taskEvents(){return {run_id:'pts_task',events:[],has_more:false,next_after:1,latest_seq:1};}};
  const store=new TaskStore(client,{storage,newId:()=> 'local',now:()=>1000});
  const record=store.create(input,{draftToken:'pts',revision:1,idempotencyRetentionSeconds:60});
  await store.submit(record.localId);
  const recovered=new TaskStore(client,{storage,now:()=>1000});
  recovered.restore();
  await recovered.poll(record.localId);
  assert.equal(submissions,1); assert.ok(queries>0);
  assert.equal(recovered.get(record.localId).task.task_contract_version,'0.2.0');
  version='0.1.0';
  await recovered.poll(record.localId);
  assert.equal(recovered.get(record.localId).task.task_contract_version,'0.2.0');
  assert.match(recovered.get(record.localId).error,/invalid_snapshot/);
});
