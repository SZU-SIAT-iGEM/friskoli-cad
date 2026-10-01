// Read-only projections of recorded observables; no solver work belongs here.
import { METRIC_KEYS } from './metrics.mjs';
const el=(tag,text='',className='')=>{const node=document.createElement(tag);node.textContent=text;node.className=className;return node;};
const fmt=value=>Number.isFinite(value)?Number(value.toPrecision(5)).toString():'—';
const COLORS=['#67d4b4','#79b8ff','#f3ba78','#d0a1ed','#ed8199','#cedc79'];
export function metricChart(root,title,series) {
  const values=series.flatMap(s=>s.points.map(p=>p[1])).filter(Number.isFinite);
  if(!values.length)return;
  const lo=Math.min(0,...values),hi=Math.max(0,...values)===lo?lo+1:Math.max(0,...values),range=hi-lo,actualEnd=Math.max(...series.flatMap(s=>s.points.map(p=>p[0]))),end=actualEnd||1;
  const wrap=el('div','','result-chart'),caption=el('div',`${title} · ${fmt(lo)}–${fmt(hi)}`);
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 400 125');svg.setAttribute('role','img');svg.setAttribute('aria-label',title);
  for(const [index,item] of series.entries()) {
    let drawing='',connected=false;
    for(const [time,value] of item.points) {if(!Number.isFinite(value)){connected=false;continue;} drawing+=`${connected?'L':'M'}${12+376*time/end},${102-90*(value-lo)/range}`;connected=true;}
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',drawing);path.setAttribute('fill','none');path.setAttribute('stroke',COLORS[index%COLORS.length]);path.setAttribute('stroke-width','2');svg.append(path);
    const legend=el('span',item.label,'curve-legend');legend.style.color=COLORS[index%COLORS.length];caption.append(legend);
  }
  const axis=document.createElementNS(svg.namespaceURI,'text');axis.setAttribute('x','12');axis.setAttribute('y','120');axis.setAttribute('fill','currentColor');axis.setAttribute('font-size','10');axis.textContent=`0 — ${fmt(actualEnd)} s`;svg.append(axis);
  wrap.append(caption,svg);root.append(wrap);
}
function table(root,headers,rows) {
  const table=el('table'),head=table.createTHead().insertRow();for(const h of headers)head.append(el('th',h));
  const body=table.createTBody();for(const cells of rows){const row=body.insertRow();for(const value of cells)row.insertCell().textContent=String(value);}
  const scroll=el('div','','result-table-scroll');scroll.tabIndex=0;scroll.append(table);root.append(scroll);
}
export function renderMetrics(root,replay,selectedId,t,project,definitions=[]) {
  const snapshots=replay.snapshots,metrics=snapshots.at(-1).metrics;
  if(metrics) {
    root.append(el('strong',t('recordedMetrics')));
    const details=el('details','','metric-definitions');details.append(el('summary',t('metricDefinitions')));
    details.append(el('p',t('metricDenominators')));
    if(project?.observation) details.append(el('pre',JSON.stringify(project.observation,null,2),'parameter-record'));
    for(const definition of definitions) details.append(el('p',`${definition.label} [${definition.unit}] · ${definition.description}`));
    root.append(details);
    for(const key of METRIC_KEYS.slice(2)) metricChart(root,t(key),Object.keys(metrics.by_group).map(group=>({label:group,points:snapshots.map(s=>[s.frame.time_s,s.metrics?.by_group[group]?.[key]??null])})));
    table(root,[t('population'),...METRIC_KEYS.map(t)],Object.entries(metrics.by_group).map(([group,values])=>[group,...METRIC_KEYS.map(k=>fmt(values[k]))]));
    root.append(el('p',`${t('lastPublishedTime')}: ${fmt(snapshots.at(-1).frame.time_s)} s`,'task-note'));
  }
  const series=snapshots.map(({frame})=>({frame,cell:frame.cells.find(c=>c.id===selectedId)}));
  const channels=selectedId?Object.keys(replay.run.channels).filter(id=>replay.run.channels[id].group_id===series.find(s=>s.cell)?.cell.group_id):[];
  for(const id of channels)metricChart(root,`${id} [${replay.run.channels[id].unit}]`,[{label:selectedId,points:series.map(({frame,cell})=>[frame.time_s,cell?.channels[id]??null])}]);
  const inventories=Object.keys(snapshots.at(-1).object_states??{});
  if(inventories.length)metricChart(root,t('remainingNutrient'),inventories.map(id=>({label:id,points:snapshots.map(s=>[s.frame.time_s,s.object_states?.[id]?.remaining_molecules??null])})));
  const deaths=snapshots.flatMap(s=>s.lifecycle_details?.deaths??[]);
  if(deaths.length){root.append(el('p',t('deathRuleHint'),'task-note'));table(root,['t [s]','Cell','Node','Policy','Health','Hazard [1/min]','P','Draw'],deaths.map(d=>[fmt(d.time_s),d.cell_id,`${d.node_id} · ${d.module_id}`,d.policy,fmt(d.health),fmt(d.death_hazard_per_min),fmt(d.probability),fmt(d.random_draw)]));}
}
export function renderComparison(root,variants,t) {
  root.replaceChildren();root.append(el('p',t('comparisonStatistics'),'task-note'));
  for(const variant of variants) {
    root.append(el('strong',`${variant.label} · seed ${variant.seeds.join(', ')}`));
    const provenance=el('details','','metric-definitions');provenance.append(el('summary',variant.provenance),el('pre',JSON.stringify(variant.versionLock??{kind:'legacy-sync'},null,2),'parameter-record'));root.append(provenance);
    for(const [group,metrics] of Object.entries(variant.byGroup)) {
      root.append(el('p',group));
      table(root,[t('metric'), 'n', t('mean'), 'SD'],METRIC_KEYS.map(key=>[t(key),metrics[key].n,fmt(metrics[key].mean),fmt(metrics[key].sd)]));
    }
    table(root,['Run','seed',t('population'),...METRIC_KEYS.map(t)],variant.runs.flatMap(run=>Object.entries(run.replay.snapshots.at(-1).metrics.by_group).map(([group,values])=>[run.id,run.submission?.execution.seed??run.project.random_seed,group,...METRIC_KEYS.map(key=>fmt(values[key]))])));
  }
  for(const metric of METRIC_KEYS.slice(2)) metricChart(root,t(metric),variants.flatMap(variant=>variant.runs.flatMap(run=>Object.keys(run.project.groups).map(group=>({label:`${variant.label} · ${group} · seed ${run.submission?.execution.seed??run.project.random_seed}`,points:run.replay.snapshots.map(s=>[s.frame.time_s,s.metrics?.by_group[group]?.[metric]??null])})))));
}
