import {boundedResponseBytes} from './kernel-client.mjs';
import {metricsCSV} from './workspace.mjs';
const sha=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
export class PagedReplay {
 constructor(client,record,manifest,{maxBytes=64*1024*1024,maxArrayBytes=32*1024*1024,maxChunkBytes=16*1024*1024}={}){Object.assign(this,{client,record,manifest,maxBytes,maxArrayBytes,maxChunkBytes});this.cache=new Map();this.bytes=0;this.inflight=null;this.pages=new Map([[manifest.chunk_offset??0,manifest.chunks]]);this.frameCount=manifest.total_chunks??manifest.chunks.length;}
 async decode(field){
  const a=field.array;if(!a)return field;
  if(a.dtype!=='<f8'||a.order!=='C'||a.axis_order!=='zyx'||!Array.isArray(a.shape)||a.shape.length!==3||a.shape.some(n=>!Number.isSafeInteger(n)||n<1)||a.bytes!==a.shape.reduce((v,n)=>v*n,8)||a.bytes>this.maxArrayBytes)throw Error('Array exceeds browser decode budget or has invalid shape/dtype');
  const data=new Uint8Array(a.bytes);let offset=0;
  for(const segment of a.segments){if(segment.offset!==offset||!Number.isSafeInteger(segment.bytes)||segment.bytes<1||segment.bytes>this.maxChunkBytes||offset+segment.bytes>a.bytes||!segment.href.startsWith(`/api/runs/${encodeURIComponent(this.record.runId)}/artifacts/`))throw Error('Invalid array segment');
   const response=await this.client.response(segment.href),bytes=await boundedResponseBytes(response,segment.bytes,this.maxChunkBytes);if(bytes.length!==segment.bytes||await sha(bytes)!==segment.sha256)throw Error('Array segment checksum/length mismatch');data.set(bytes,offset);offset+=bytes.length;}
  if(offset!==a.bytes||await sha(data)!==a.sha256)throw Error('Array checksum/length mismatch');
  const flat=new Float64Array(data.buffer);for(const value of flat)if(!Number.isFinite(value)||value<0)throw Error('Invalid concentration value');
  const [nz,ny,nx]=a.shape;if(field.field_domain&&JSON.stringify(field.field_domain.counts_xyz)!==JSON.stringify([nx,ny,nz]))throw Error('Array shape disagrees with field domain');return {...field,values_zyx:Array.from({length:nz},(_,z)=>Array.from({length:ny},(_,y)=>flat.subarray((z*ny+y)*nx,(z*ny+y+1)*nx)))};
 }
 async frame(index){
  if(!Number.isSafeInteger(index)||index<0||index>=this.frameCount)throw Error('Frame outside published range');
  if(this.cache.has(index)){const entry=this.cache.get(index);this.cache.delete(index);this.cache.set(index,entry);return entry.value;}
  // Serialize fetches so dragging a slider cannot create an unbounded decode queue.
  if(this.inflight)await this.inflight;
  const operation=this.load(index);this.inflight=operation;try{return await operation;}finally{if(this.inflight===operation)this.inflight=null;}
 }
 async descriptor(index){
  const offset=Math.floor(index/1024)*1024;let page=this.pages.get(offset);
  if(!page){const result=await this.client.request(`/api/runs/${encodeURIComponent(this.record.runId)}/result?offset=${offset}&limit=1024`);if(result.run_id!==this.record.runId||result.chunk_offset!==offset||!Array.isArray(result.chunks)||result.chunks.length>1024)throw Error('Invalid result index page');page=result.chunks;this.pages.set(offset,page);while(this.pages.size>2)this.pages.delete(this.pages.keys().next().value);}
  const chunk=page[index-offset];if(!chunk)throw Error('Missing published frame index');return chunk;
 }
 async scan({start=0,end=Infinity,type='all'}={},cellId=null,onProgress=()=>{},cancelled=()=>false){
  if(!Number.isFinite(start)||start<0||end<start)throw Error('Invalid result interval');
  const events=[],points=[];let eventCount=0,pointCount=0,stride=1;const dt=this.record.submission?.execution.dt_s;
  for(let index=0;index<this.frameCount;index++){if(cancelled())throw Error('Scan cancelled');const chunk=await this.descriptor(index);if(dt&&(chunk.first_step*dt<start||chunk.first_step*dt>end))continue;if(chunk.bytes>this.maxChunkBytes)throw Error('Frame exceeds scan budget');const body=await this.client.taskChunk(this.record.runId,chunk),frame=body.frames?.[0]?.frame;if(!frame||frame.time_s<start||frame.time_s>end)continue;
   for(const event of frame.events??[])if(type==='all'||event.type===type){eventCount++;if(events.length<500)events.push({index,time_s:frame.time_s,event});}
   const cell=cellId?frame.cells.find(c=>c.id===cellId):null;if(cell){if(pointCount++%stride===0)points.push(cell.position_um);if(points.length>=2000){points.splice(0,points.length,...points.filter((_,i)=>i%2===0));stride*=2;}}
   onProgress(index+1,this.frameCount);
  }
  return {events,eventCount,points,pointCount,pointStride:stride};
 }
 async *frames(){for(let index=0;index<this.frameCount;index++)yield await this.frame(index);}
 async load(index){
  const chunk=await this.descriptor(index);if(chunk.bytes>this.maxChunkBytes)throw Error('Frame chunk exceeds browser decode budget');
  const body=await this.client.taskChunk(this.record.runId,chunk);
  if(body.run_id!==this.record.runId||body.frames?.length!==1||body.frames[0].step_index!==chunk.first_step)throw Error('Invalid indexed frame');
  const item=body.frames[0];if(item.frame?.frame_index!==item.step_index||item.time_s!==undefined&&item.frame.time_s!==item.time_s||!Number.isFinite(item.frame.time_s)||!Array.isArray(item.frame.cells)||!Array.isArray(item.frame.events)||item.frame.cells.some(c=>!c.id||!Array.isArray(c.position_um)||c.position_um.length!==3||c.position_um.some(v=>!Number.isFinite(v))))throw Error('Invalid frame payload');
  const arrayBytes=Object.values(item.concentrations??{}).reduce((n,f)=>n+(f.array?.bytes??0),0),bytes=chunk.bytes*4+arrayBytes;
  if(bytes>this.maxBytes)throw Error('Frame exceeds browser cache budget');
  while(this.cache.size&&this.bytes+bytes>this.maxBytes){const key=this.cache.keys().next().value;this.bytes-=this.cache.get(key).bytes;this.cache.delete(key);}
  const concentrations={};for(const [species,field]of Object.entries(item.concentrations??{}))concentrations[species]=await this.decode(field);
  const value={frame:item.frame,concentrations,...(item.metrics?{metrics:item.metrics}:{}),object_states:item.object_states??{},lifecycle_details:item.lifecycle_details};
  this.cache.set(index,{bytes,value});this.bytes+=bytes;return value;
 }
 replay(snapshot,index){return {replay_format_version:'0.1.0',project_id:this.record.project.id,run:this.record.project.run,domain:this.record.project.domain,execution:{task_contract_version:'0.6.0',task_run_id:this.record.runId,status:this.manifest.status,completeness:this.manifest.completeness},snapshots:[snapshot],paged:{frame_count:this.frameCount,index,cache_bytes:this.bytes,max_bytes:this.maxBytes}};}
}

const json=(value)=>JSON.stringify(value,(_,item)=>ArrayBuffer.isView(item)?Array.from(item):item);
export async function exportPagedResult(pager,{csv=false,writer=null,onProgress=()=>{}}={}){
 const pieces=[];let total=0;const maxFallback=16*1024*1024;
 const write=async text=>{if(writer){await writer.write(text);return;}total+=new TextEncoder().encode(text).length;if(total>maxFallback)throw Error('Export exceeds 16 MiB download buffer. Use the native file-save picker to stream it.');pieces.push(text);};
 try{
  if(!csv) {const base=pager.replay(null,0);delete base.paged;delete base.snapshots;await write('{"task_contract_version":"0.6.0","task_run_id":'+json(pager.record.runId)+',"submission":'+json(pager.record.submission)+',"manifest":'+json(pager.manifest)+',"replay":'+json(base).slice(0,-1)+',"snapshots":[');}
  let index=0;for await(const snapshot of pager.frames()){
   if(csv){const text=metricsCSV({...pager.replay(snapshot,index),snapshots:[snapshot]});await write(index?text.slice(text.indexOf('\n')+1):text);}
   else await write((index?',':'')+json(snapshot));index++;onProgress(index,pager.frameCount);
  }
  if(!csv)await write(']}}');if(writer){await writer.close();return null;}return new Blob(pieces,{type:csv?'text/csv':'application/json'});
 }catch(error){await writer?.abort?.();throw error;}
}
