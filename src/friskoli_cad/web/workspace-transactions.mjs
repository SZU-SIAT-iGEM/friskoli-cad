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
 return {generated_by:block.object_type??'population',owner_object_id:block.id,template_version:'1',overrides:{},base_nodes:structuredClone(nodes)};
}
export function templateUpgradePreview(record,project,incoming){
 const conflicts=[],updates=[];
 for(const proposed of incoming){const base=record.base_nodes.find(n=>n.id===proposed.id),current=project.graph.nodes.find(n=>n.id===proposed.id);if(!current){conflicts.push({id:proposed.id,reason:'removed'});continue;}
 for(const [key,value]of Object.entries(proposed.parameters)){const modified=JSON.stringify(current.parameters[key])!==JSON.stringify(base?.parameters[key]);if(modified&&JSON.stringify(current.parameters[key])!==JSON.stringify(value))conflicts.push({id:proposed.id,parameter:key,current:current.parameters[key],incoming:value});else updates.push({id:proposed.id,parameter:key,value:structuredClone(value)});}}
 return {conflicts,updates};
}
