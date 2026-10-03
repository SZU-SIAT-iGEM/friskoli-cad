// Replicates are independent seeded runs, never individual cells or time points.
export function meanInterval(values){
 if(values.length<2||values.some(v=>!Number.isFinite(v)))return null;
 const n=values.length,mean=values.reduce((a,b)=>a+b,0)/n,sd=Math.sqrt(values.reduce((a,b)=>a+(b-mean)**2,0)/(n-1));
 const critical=[null,12.706,4.303,3.182,2.776,2.571,2.447,2.365,2.306,2.262,2.228,2.201,2.179,2.16,2.145,2.131,2.12,2.11,2.101,2.093,2.086,2.08,2.074,2.069,2.064,2.06,2.056,2.052,2.048,2.045,2.042];
 const df=n-1;if(df>30)return null;const half=critical[df]*sd/Math.sqrt(n);
 return {n,mean,sd,lower:mean-half,upper:mean+half,method:'two-sided Student t, 95%, independent normal replicate means; complete cases only'};
}
export function parameterComparison(records){
 const maps=records.map(r=>Object.fromEntries(r.project.graph.nodes.flatMap(n=>Object.entries(n.parameters).map(([p,v])=>[`${n.id}.${p}`,v]))));
 const keys=[...new Set(maps.flatMap(Object.keys))].sort();return keys.map(key=>({key,values:maps.map(m=>m[key]??null),common:maps.every(m=>JSON.stringify(m[key])===JSON.stringify(maps[0][key]))}));
}
export function filteredEvents(replay,{start=0,end=Infinity,type='all'}={}){return replay.snapshots.flatMap((s,index)=>s.frame.time_s>=start&&s.frame.time_s<=end?s.frame.events.filter(e=>type==='all'||e.type===type).map(event=>({index,time_s:s.frame.time_s,event})):[]);}
export function trajectorySegments(replay,id,start=0,end=Infinity){
 const segments=[];let segment=[];for(const {frame}of replay.snapshots){const cell=frame.cells.find(c=>c.id===id);if(!cell||frame.time_s<start||frame.time_s>end){if(segment.length>1)segments.push(segment);segment=[];continue;}segment.push(cell.position_um);}
 if(segment.length>1)segments.push(segment);return segments;
}
export function renderParameterComparison(parent,records){
 const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Candidate parameters · common / differences';details.append(summary);
 for(const common of [false,true]){const title=document.createElement('h4');title.textContent=common?'Common parameters':'Differences';details.append(title);for(const item of parameterComparison(records).filter(x=>x.common===common)){const row=document.createElement('p');row.textContent=`${item.key}: `+item.values.map((v,i)=>`${records[i].project.id} = ${v?JSON.stringify(v.value)+' '+(v.unit??''):'unknown'}`).join(' · ');details.append(row);}}
 parent.append(details);
}
