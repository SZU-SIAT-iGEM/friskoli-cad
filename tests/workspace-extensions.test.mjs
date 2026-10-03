import test from 'node:test';
import assert from 'node:assert/strict';
import {WorkspaceSession} from '../src/friskoli_cad/web/workspace-session.mjs';
import {alignBlock,templateUpgradePreview} from '../src/friskoli_cad/web/workspace-transactions.mjs';
import {meanInterval,trajectorySegments,filteredEvents} from '../src/friskoli_cad/web/result-analysis.mjs';
import {PagedReplay,LinkedReplay,exportPagedResult} from '../src/friskoli_cad/web/paged-replay.mjs';
test('cached catalog and multiple recent projects survive a cold session independently',()=>{
 const entries=new Map(),storage={getItem:k=>entries.get(k),setItem:(k,v)=>entries.set(k,v)};
 const session=new WorkspaceSession(storage,{maxProjects:2});session.cacheCatalog({registries:{legacy:{}},capabilities:{version:1},template:{id:'demo'}});
 for(const id of ['a','b','c'])session.remember({project:{id}}, {camera:{zoom:2}});
 const reopened=new WorkspaceSession(storage);assert.equal(reopened.cachedCatalog().template.id,'demo');assert.deepEqual(reopened.recent().map(v=>v.id),['c','b']);assert.equal(reopened.recent()[0].view_state.camera.zoom,2);
});
test('linked checkpoint replay includes parent history once and exports every saved frame',async()=>{
 const project={id:'p',domain:{counts_xyz:[1,1,1],spacing_um_xyz:[1,1,1],geometry:'volume'},run:{run_id:'original'}};
 const submission={task_contract_version:'0.6.0',project,execution:{dt_s:1}};
 const manifests={parent:{run_id:'parent',chunks:[0,1,2].map(n=>({first_step:n,bytes:100,chunk_id:String(n)}))},child:{run_id:'child',parent_run_id:'parent',chunks:[2,3].map(n=>({first_step:n,bytes:100,chunk_id:String(n)}))}};
 const client={taskResult:async id=>manifests[id],request:async()=>submission,taskChunk:async(id,c)=>({run_id:id,frames:[{step_index:c.first_step,frame:{frame_index:c.first_step,time_s:c.first_step,cells:[],events:[]},concentrations:{}}]})};
 const linked=await LinkedReplay.create(client,{runId:'child',project,submission},manifests.child);assert.equal(linked.frameCount,4);
 const result=[];for await(const frame of linked.frames())result.push([frame.frame.frame_index,frame.segment_run_id]);assert.deepEqual(result,[[0,'parent'],[1,'parent'],[2,'child'],[3,'child']]);
 const exported=JSON.parse(await (await exportPagedResult(linked)).text());assert.deepEqual(exported.replay.snapshots.map(s=>s.frame.frame_index),[0,1,2,3]);assert.equal(exported.manifest.parent_run_id,'parent');
});
test('rotated top alignment uses oriented outer bounds and upgrade keeps conflicts explicit',()=>{
 const block={center:[10,10,10],size:[8,2,2],rotation:[0,90,0]};
 const aligned=alignBlock({counts_xyz:[20,20,20],spacing_um_xyz:[1,1,1]},block,'top');assert.ok(Math.abs(aligned.center[2]-16)<1e-10);assert.equal(block.center[2],10);
 const record={base_nodes:[{id:'n',parameters:{p:{value:1}}}]},project={graph:{nodes:[{id:'n',parameters:{p:{value:2}}}]}};
 const preview=templateUpgradePreview(record,project,[{id:'n',parameters:{p:{value:3}}}]);assert.equal(preview.conflicts.length,1);assert.equal(preview.updates.length,0);assert.equal(project.graph.nodes[0].parameters.p.value,2);
});
test('intervals use run-level sampling error and missing values suppress inference',()=>{
 const interval=meanInterval([1,2,3]);assert.equal(interval.mean,2);assert.ok(interval.lower<0&&interval.upper>4);assert.equal(meanInterval([1]),null);assert.equal(meanInterval([1,null,3]),null);
 const replay={snapshots:[{frame:{time_s:0,cells:[{id:'a',position_um:[0,0,0]}],events:[]}},{frame:{time_s:1,cells:[{id:'a',position_um:[1,0,0]}],events:[{type:'death',cell_id:'b'}]}}]};
 assert.equal(trajectorySegments(replay,'a')[0].length,2);assert.equal(filteredEvents(replay,{start:1,end:1,type:'death'}).length,1);assert.equal(filteredEvents(replay,{type:'division'}).length,0);
});
test('binary fields decode typed rows with verified bounds and hashes, and cache evicts',async()=>{
 const bytes=new Uint8Array(new Float64Array([1,2,3,4]).buffer),hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
 const field={unit:'uM',array:{dtype:'<f8',order:'C',axis_order:'zyx',shape:[1,2,2],bytes:32,sha256:hash,segments:[{offset:0,bytes:32,sha256:hash,href:'/api/runs/r/artifacts/array'}]}};
 let calls=0;const client={response:async()=>({arrayBuffer:async()=>bytes.buffer}),taskChunk:async(_,chunk)=>{calls++;return {run_id:'r',frames:[{step_index:chunk.first_step,frame:{frame_index:chunk.first_step,cells:[],events:[],time_s:chunk.first_step},concentrations:{s:field}}]};}};
 const pager=new PagedReplay(client,{runId:'r',project:{id:'p',run:{},domain:{}}},{chunks:[0,1].map(i=>({first_step:i,bytes:20,chunk_id:String(i)}))},{maxBytes:120,maxArrayBytes:40,maxChunkBytes:64});
 assert.deepEqual(Array.from((await pager.frame(0)).concentrations.s.values_zyx[0][1]),[3,4]);await pager.frame(1);assert.equal(pager.cache.size,1);await pager.frame(0);assert.equal(calls,3);assert.ok(pager.bytes<=120);
 await assert.rejects(pager.decode({...field,array:{...field.array,bytes:80}}));await assert.rejects(pager.decode({...field,array:{...field.array,sha256:'0'.repeat(64)}}));
});
