// Display grouping and exact diagnostic references never alter execution order.
const categories = [
  ['geometry','Geometry & data','几何与数据'],['fields','Fields & sources','浓度场与来源'],
  ['materials','Materials & reactions','材料与反应'],['uptake','Uptake & settlement','摄取与结算'],
  ['signals','Sensing & adaptation','感知与适应'],['motion','Motion & contact','运动与接触'],
  ['life','Metabolism & lifecycle','代谢与生命周期'],['other','Other registered modules','其他注册模块']
];
export function moduleCategory(manifest) {
  const id=manifest.id??'';
  if(manifest.declaration?.category==='data'||/^(geometry|cell)\.|^pts\.capsule_area$/.test(id))return 'geometry';
  if(/^(field|environment|source)\./.test(id))return 'fields';
  if(/^(material|reaction|surface)\./.test(id))return 'materials';
  if(/^uptake\.|^pts\.capacity/.test(id))return 'uptake';
  if(/^(signal|memory)\./.test(id))return 'signals';
  if(/^(motion|collision)\./.test(id))return 'motion';
  if(/^(metabolism|growth|expression|life|division|intracellular)\./.test(id))return 'life';
  return 'other';
}
export function moduleFolders(modules,language='en',filter='') {
  const query=filter.toLowerCase();
  return categories.map(([id,en,zh])=>({id,label:language==='zh-CN'?zh:en,
    entries:[...modules].filter(([key,m])=>moduleCategory(m)===id&&(!query||`${key} ${JSON.stringify(m.declaration?.label??'')} ${m.description??''}`.toLowerCase().includes(query)))
  })).filter(folder=>folder.entries.length);
}
export function diagnosticTarget(issue,project,blocks=[]) {
  if(!project||issue.code==='valid')return null;
  const nodes=project.graph.nodes,edges=project.graph.edges;
  const path=String(issue.path??''),parts=path.split('/').filter(Boolean).map(p=>p.replace(/~1/g,'/').replace(/~0/g,'~'));
  for(const [name,items,kind] of [['nodes',nodes,'node'],['edges',edges,'edge']]){
    const index=parts.indexOf(name);if(index<0)continue;
    const key=parts[index+1],item=/^\d+$/.test(key??'')?items[Number(key)]:items.find(x=>x.id===key);
    if(item)return {kind,id:item.id,parameter:parts.includes('parameters')?parts[parts.indexOf('parameters')+1]:undefined};
  }
  const groupIndex=parts.indexOf('groups'),groupId=groupIndex>=0?parts[groupIndex+1]:null;
  if(groupId&&Object.hasOwn(project.groups,groupId))return {kind:'population',id:groupId};
  const block=blocks.find(b=>b.id===path);if(block)return {kind:'population',id:block.id};
  if(parts.includes('domain')||path==='/project')return {kind:'domain'};
  // Runtime contract errors prefix their exact node ID; do not guess from prose.
  const node=nodes.find(n=>String(issue.message??'').startsWith(n.id+':'));
  if(node)return {kind:'node',id:node.id};
  if(parts.includes('graph')||parts.includes('nodes')||parts.includes('edges'))return {kind:'graph'};
  if(parts.includes('execution')||parts.includes('output_plan'))return {kind:'settings',parameter:parts.at(-1)};
  return null;
}

// Return the real owner-bound nodes, never editable copies or inferred drivers.
export function populationModuleBindings(project,modules,groupId){
  return (project?.graph?.nodes??[]).filter(node=>node.owner?.kind==='population'&&node.owner.id===groupId)
    .map(node=>({node,manifest:modules.get(`${node.module_id}@${node.module_version}`)??null}));
}
export function populationNodeLocked(node,blocks=[]){
  return node?.owner?.kind==='population'&&Boolean(blocks.find(block=>block.id===node.owner.id)?.locked);
}
