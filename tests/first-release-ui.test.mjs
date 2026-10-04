import test from 'node:test';
import assert from 'node:assert/strict';
import {validateMetrics, compareRuns} from '../src/friskoli_cad/web/metrics.mjs';
import {LinkedReplay, exportPagedResult} from '../src/friskoli_cad/web/paged-replay.mjs';

const project={id:'formal',execution_profile:'modular-spatial-v1',random_seed:1,domain:{geometry:'thin_layer',counts_xyz:[1,1,1],spacing_um_xyz:[100,100,2]},groups:{cells:{ids:['a','b']}},run:{run_id:'formal-run',channels:{}}};
const metrics={metric_version:'0.1.0',observation_id:'whole_domain',by_group:{cells:{initial_count:2,live_count:2,mean_position_um:50,drift_um_s:null,mean_displacement_um:1,region_fraction:.5,ever_arrived_fraction:1,mean_residence_s:2,cumulative_degradation_molecules:100,degradation_per_initial_cell_molecules:50}}};
const item=index=>({step_index:index,frame:{frame_index:index,time_s:index,cells:[],events:[]},metrics,concentrations:{},object_states:{}});

test('Task 0.6 metric history and streamed export preserve solver metrics and frozen provenance',async()=>{
  const record={runId:'task1',project,task:{task_contract_version:'0.6.0',status:'completed',result:{completeness:'complete'}},submission:{task_contract_version:'0.6.0',execution:{dt_s:1}}};
  const manifest={run_id:'task1',status:'completed',completeness:'complete',input_snapshot:{scientific_sha256:'abc'},chunks:[0,1,2].map(index=>({chunk_id:String(index),first_step:index,bytes:100}))};
  let fetches=0;
  const client={taskChunk:async(id,descriptor)=>{fetches++;return {run_id:id,frames:[item(descriptor.first_step)]};}};
  const pager=await LinkedReplay.create(client,record,manifest);
  const history=await pager.metricHistory();assert.equal(history.length,3);assert.deepEqual(history[1].metrics,metrics);
  await pager.metricHistory(history);assert.equal(fetches,3);
  const exported=JSON.parse(await (await exportPagedResult(pager)).text());
  assert.equal(exported.task.status,'completed');assert.equal(exported.completeness,'complete');
  assert.deepEqual(exported.replay.execution.input_snapshot,manifest.input_snapshot);
  assert.equal(exported.replay.snapshots.length,3);assert.deepEqual(exported.replay.snapshots[2].metrics,metrics);
});

test('multi-seed comparison treats each run as the independent replicate',()=>{
  const run=seed=>({id:`r${seed}`,status:'completed',project:{...project,random_seed:seed},settings:{dt_s:1,steps:2},submission:{task_contract_version:'0.6.0',version_lock:{registry_sha256:'a'.repeat(64)},execution:{seed,backend:'numpy-cpu'}},replay:{snapshots:[{...item(2),metrics:structuredClone(metrics)}]}});
  const records=[run(1),run(2)];records[1].replay.snapshots[0].metrics.by_group.cells.region_fraction=1;
  const [variant]=compareRuns(records);assert.equal(variant.byGroup.cells.region_fraction.n,2);assert.equal(variant.byGroup.cells.region_fraction.mean,.75);
  assert.ok(variant.byGroup.cells.region_fraction.interval);
  assert.throws(()=>compareRuns([run(1),run(1)]),/comparisonDuplicateSeed/);
  assert.deepEqual(validateMetrics(metrics,project),metrics);
  const bad=structuredClone(metrics);bad.by_group.cells.region_fraction=1.1;assert.throws(()=>validateMetrics(bad,project));
});
