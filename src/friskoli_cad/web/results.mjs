import {filteredEvents} from './result-analysis.mjs';
// Read-only projections of recorded frames. No state evolution or interpolation.
import { renderMetrics } from './metric-results.mjs';
export function renderResultData(root, replay, selectedId, selectFrame, labels, options={}) {
  root.replaceChildren();
  if (!replay) { root.textContent = labels.empty; return; }
  const series = replay.snapshots.map(({frame}) => ({frame, cell:frame.cells.find(c => c.id === selectedId)}));
  const wrapper = document.createElement('div'); wrapper.className = 'result-data';
  if(root.resultRunId!==replay.execution?.task_run_id){root.resultFilters=null;root.resultRunId=replay.execution?.task_run_id;}
  const filters=root.resultFilters??={start:0,end:replay.snapshots.at(-1).frame.time_s,type:'all'};
  const controls=document.createElement('form');controls.className='result-filter';
  for(const key of ['start','end']){const label=document.createElement('label');label.textContent=key+' [s]';const input=document.createElement('input');input.type='number';input.min='0';input.step='any';input.value=filters[key];input.setAttribute('aria-label',`Event interval ${key}`);input.addEventListener('change',()=>{const value=Number(input.value);if(Number.isFinite(value)&&value>=0){filters[key]=value;renderResultData(root,replay,selectedId,selectFrame,labels,options);}});label.append(input);controls.append(label);}
  const type=document.createElement('select');type.setAttribute('aria-label','Event type');for(const name of ['all','division','birth','death',...new Set(replay.snapshots.flatMap(s=>s.frame.events.map(e=>e.type)))])type.add(new Option(name,name));type.value=filters.type;type.addEventListener('change',()=>{filters.type=type.value;renderResultData(root,replay,selectedId,selectFrame,labels,options);});controls.append(type);wrapper.append(controls);
  const events=filteredEvents(replay,filters),eventList=document.createElement('div');for(const item of events.slice(0,500)){const button=document.createElement('button');button.className='event-row';button.textContent=`${item.time_s} s · ${item.event.type} · ${item.event.cell_id??item.event.parent_id??''}`;button.addEventListener('click',()=>selectFrame(item.index));eventList.append(button);}if(events.length>500){const note=document.createElement('p');note.textContent=`${events.length} events; showing first 500. Narrow the interval.`;eventList.append(note);}wrapper.append(eventList);
  if(replay.paged&&options.scan){const scan=document.createElement('button');scan.type='button';scan.className='menu-button';scan.textContent='Scan saved interval / 扫描已保存时间区间';const progress=document.createElement('span');let cancelled=false;const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Cancel scan';cancel.hidden=true;cancel.addEventListener('click',()=>{cancelled=true;});scan.addEventListener('click',async()=>{scan.disabled=true;cancel.hidden=false;cancelled=false;try{const result=await options.scan(filters,selectedId,(n,total)=>{progress.textContent=`${n}/${total}`;},()=>cancelled||!root.contains(controls));eventList.replaceChildren();for(const item of result.events){const button=document.createElement('button');button.className='event-row';button.textContent=`${item.time_s} s · ${item.event.type} · ${item.event.cell_id??item.event.parent_id??''}`;button.addEventListener('click',()=>selectFrame(item.index));eventList.append(button);}progress.textContent=`${result.eventCount} events; displaying ${result.events.length}. Trajectory: ${result.points.length}/${result.pointCount} positions, stride ${result.pointStride}.`;options.trajectory?.(result.segments??(result.points.length>1?[result.points]:[]));}catch(error){progress.textContent=error.message;}finally{scan.disabled=false;cancel.hidden=true;}});wrapper.append(scan,cancel,progress);const note=document.createElement('p');note.className='task-note';note.textContent='The table and curve below show the loaded frame. Scan the selected saved interval for event filtering and selected-cell trajectory; at most 500 event rows and 2000 trajectory points are retained.';wrapper.append(note);}


  if(options.t) renderMetrics(wrapper,replay,selectedId,options.t,options.project,options.definitions);
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 360 100'); svg.setAttribute('role','img'); svg.setAttribute('aria-label',labels.curve);
  const max = Math.max(1, ...series.map(s => s.frame.cells.length)), end = series.at(-1).frame.time_s || 1;
  const path = document.createElementNS(svg.namespaceURI,'polyline');
  path.setAttribute('points',series.map(({frame})=>`${10+340*frame.time_s/end},${90-75*frame.cells.length/max}`).join(' '));
  path.setAttribute('fill','none'); path.setAttribute('stroke','#67d4b4'); path.setAttribute('stroke-width','2'); svg.append(path);
  const chart = document.createElement('div'); chart.className='result-chart';
  const caption=document.createElement('div');caption.textContent=`${labels.curve} · 0–${end} s · 0–${max}`;chart.append(caption,svg);
  const table = document.createElement('table'), head = table.createTHead().insertRow();
  const channels = selectedId ? Object.keys(replay.run.channels).filter(id => replay.run.channels[id].group_id === series.find(s=>s.cell)?.cell.group_id) : [];
  for (const label of ['t [s]',selectedId || labels.cells,labels.events,...channels.map(id=>`${id} [${replay.run.channels[id].unit}]`)]) {
    const th=document.createElement('th');th.textContent=label;head.append(th);
  }
  const body = table.createTBody();
  for (const [index,{frame,cell}] of series.entries()) {
    const row=body.insertRow();
    const button=document.createElement('button');button.textContent=String(frame.time_s);button.title=`${labels.frame} ${frame.frame_index}`;
    button.addEventListener('click',()=>selectFrame(index));row.insertCell().append(button);
    row.insertCell().textContent=selectedId ? cell ? cell.position_um.map(n=>Number(n.toFixed(3))).join(' / ')+' µm' : '—' : String(frame.cells.length);
    row.insertCell().textContent=String(frame.events.length);
    for (const id of channels) row.insertCell().textContent=cell?.channels[id] == null ? '—' : String(cell.channels[id]);
  }
  wrapper.append(chart,table);root.append(wrapper);
}
