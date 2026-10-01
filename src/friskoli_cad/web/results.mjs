// Read-only projections of recorded frames. No state evolution or interpolation.
import { renderMetrics } from './metric-results.mjs';
export function renderResultData(root, replay, selectedId, selectFrame, labels, options={}) {
  root.replaceChildren();
  if (!replay) { root.textContent = labels.empty; return; }
  const series = replay.snapshots.map(({frame}) => ({frame, cell:frame.cells.find(c => c.id === selectedId)}));
  const wrapper = document.createElement('div'); wrapper.className = 'result-data';
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
