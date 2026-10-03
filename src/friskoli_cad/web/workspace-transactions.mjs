import {connectionProblem,setParameter} from './graph-edit.mjs';
import {rotationQuaternion,rotatePoint} from './population.mjs';
export function orientedBounds(block){
 const q=rotationQuaternion(block.rotation??[0,0,0]);const radius=[0,0,0];
 for(let bits=0;bits<8;bits++){const corner=rotatePoint(block.size.map((v,i)=>(bits&(1<<i)?1:-1)*v/2),q);corner.forEach((v,i)=>radius[i]=Math.max(radius[i],Math.abs(v)));}
 return {lower:block.center.map((v,i)=>v-radius[i]),upper:block.center.map((v,i)=>v+radius[i]),radius};
}
export function alignBlock(domain,block,position){
 const copy=structuredClone(block),size=domain.counts_xyz.map((n,i)=>n*domain.spacing_um_xyz[i]);
 if(position==='center')copy.center=size.map(n=>n/2);
 else {const {radius}=orientedBounds(copy);copy.center[2]=position==='bottom'?radius[2]:size[2]-radius[2];}
 copy.dirty=true;return copy;
}
export function deletionPreview(project,nodeIds){
 const ids=new Set(nodeIds);return {nodes:project.graph.nodes.filter(n=>ids.has(n.id)).map(n=>n.id),edges:project.graph.edges.filter(e=>ids.has(e.from.node)||ids.has(e.to.node)).map(e=>structuredClone(e)),channels:Object.entries(project.run.channels).filter(([,c])=>ids.has(c.node)).map(([id,value])=>({id,...structuredClone(value)}))};
}
export function managedRecord(block,project){
 const nodes=project.graph.nodes.filter(n=>n.owner.kind==='population'&&n.owner.id===block.id);
 return {generated_by:block.object_type??'population',owner_object_id:block.id,template_version:'1',overrides:{},initial_distribution:structuredClone(block),initial_group:structuredClone(project.groups[block.id]),base_nodes:structuredClone(nodes),base_edges:structuredClone(project.graph.edges.filter(e=>nodes.some(n=>n.id===e.from.node||n.id===e.to.node)))};
}
// Three-way merge uses stable IDs. A local edit wins by default; accepting an
// incoming conflict is an explicit policy. Manual edges are never overwritten.
export function templateUpgradePreview(record,project,incoming,incomingEdges=null,modules=null,policy='keep'){
 const conflicts=[],updates=[],structural=[],changes=[],draftLinks=[];
 const stable=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
 const equal=(a,b)=>stable(a)===stable(b),clone=value=>value===undefined?undefined:structuredClone(value);
 const mergeValue=(base,current,next,id,parameter)=>{if(equal(next,base)||equal(current,next))return clone(current);if(equal(current,base))return clone(next);conflicts.push({id,parameter,current:clone(current),incoming:clone(next),reason:'both local and template changed'});return clone(policy==='incoming'?next:current);};
 const nextProject=structuredClone(project),baseNodes=new Map(record.base_nodes.map(n=>[n.id,n])),currentNodes=new Map(project.graph.nodes.map(n=>[n.id,n])),incomingNodes=new Map();
 for(const node of incoming){if(!node?.id||incomingNodes.has(node.id)||!node.parameters||typeof node.parameters!=='object'){structural.push({id:node?.id??'unknown',reason:'invalid or duplicate node'});continue;}incomingNodes.set(node.id,node);if(record.owner_object_id&&node.owner?.id!==record.owner_object_id)structural.push({id:node.id,reason:'template node owner differs from managed object'});}
 const mergedNodes=new Map(currentNodes);
 for(const id of new Set([...baseNodes.keys(),...incomingNodes.keys()])){
  const base=baseNodes.get(id),current=currentNodes.get(id),next=incomingNodes.get(id);let value;
  if(base&&current&&next){value={};for(const field of new Set([...Object.keys(base),...Object.keys(current),...Object.keys(next)])){if(field==='parameters'){value.parameters={};for(const parameter of new Set([...Object.keys(base.parameters),...Object.keys(current.parameters),...Object.keys(next.parameters)])){const entry=mergeValue(base.parameters[parameter],current.parameters[parameter],next.parameters[parameter],id,parameter);if(entry!==undefined)value.parameters[parameter]=entry;if(!equal(entry,current.parameters[parameter]))updates.push({id,parameter,value:entry});}}else{const entry=mergeValue(base[field],current[field],next[field],id,field);if(entry!==undefined)value[field]=entry;}}}
  else value=mergeValue(base,current,next,id,'node');
  if(value===undefined)mergedNodes.delete(id);else mergedNodes.set(id,value);
  if(!equal(current,value))changes.push(`${current?value?'Update':'Remove':'Add'} node ${id}`);
 }
 // Observations are not an implicit part of a node/edge template. Keep local
 // referenced nodes until the user repairs or removes those observations.
 for(const [name,channel]of Object.entries(project.run?.channels??{}))if(!mergedNodes.has(channel.node)){
  conflicts.push({id:channel.node,reason:`observation ${name} references removed node`});
  if(policy==='keep')mergedNodes.set(channel.node,clone(currentNodes.get(channel.node)));else structural.push({id:channel.node,reason:`repair observation ${name} before removing its node`});
 }
 nextProject.graph.nodes=[...mergedNodes.values()];
 const baseEdges=new Map((record.base_edges??[]).map(e=>[e.id,e])),currentEdges=new Map((project.graph.edges??[]).map(e=>[e.id,e])),mergedEdges=new Map(currentEdges),incomingMap=new Map();
 if(incomingEdges!==null){if(!Array.isArray(incomingEdges))structural.push({id:'edges',reason:'edges must be an array'});else for(const edge of incomingEdges){if(!edge?.id||incomingMap.has(edge.id)||!edge.from?.node||!edge.to?.node){structural.push({id:edge?.id??'edge',reason:'invalid or duplicate edge'});continue;}incomingMap.set(edge.id,edge);}
 for(const id of new Set([...baseEdges.keys(),...incomingMap.keys()])){const base=baseEdges.get(id),current=currentEdges.get(id),next=incomingMap.get(id);if(!base&&current&&!equal(current,next)){conflicts.push({id,reason:'manual connection ID conflict; manual connection preserved'});continue;}const value=mergeValue(base,current,next,id,'edge');if(value===undefined)mergedEdges.delete(id);else mergedEdges.set(id,value);if(!equal(current,value))changes.push(`${current?value?'Update':'Remove':'Add'} edge ${id}`);}}
 const manualEdges=(project.graph.edges??[]).filter(edge=>!equal(baseEdges.get(edge.id),edge)).map(edge=>edge.id);
 if(modules)for(const node of nextProject.graph.nodes){const manifest=modules.get(`${node.module_id}@${node.module_version}`);if(!manifest){structural.push({id:node.id,reason:`missing implementation ${node.module_id}@${node.module_version}`});continue;}if(manifest.scope!==node.owner?.kind)structural.push({id:node.id,reason:'owner scope is incompatible'});for(const [name,entry]of Object.entries(node.parameters)){const check=structuredClone(node),problem=setParameter(check,manifest,name,entry?.value);if(problem)structural.push({id:node.id,reason:`${name}: ${problem}`});}}
 nextProject.graph.edges=[];
 // Validate old/manual edges first; an incoming edge must not displace one.
 const sorted=[...mergedEdges.values()].sort((a,b)=>Number(manualEdges.includes(b.id))-Number(manualEdges.includes(a.id)));
 for(const edge of sorted){const problem=!mergedNodes.has(edge.from.node)||!mergedNodes.has(edge.to.node)?'edge.node':modules?connectionProblem(nextProject.graph,modules,edge.from,edge.to,edge.timing):null;if(problem){conflicts.push({id:edge.id,reason:`${problem}; connection retained as repair draft`});draftLinks.push(clone(edge));}else nextProject.graph.edges.push(clone(edge));}
 for(const [name,channel]of Object.entries(nextProject.run?.channels??{})){const node=mergedNodes.get(channel.node),manifest=node&&modules?.get(`${node.module_id}@${node.module_version}`);if(modules&&node&&!manifest?.outputs[channel.port])structural.push({id:node.id,reason:`observation ${name} port is absent in the requested module version`});}
 return {conflicts,updates,structural,manualEdges,changes,draftLinks,project:nextProject,valid:!structural.length};
}
