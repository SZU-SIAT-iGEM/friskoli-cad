import {currentLanguage,t} from './i18n.mjs';
const text=(en,zh)=>currentLanguage()==='zh-CN'?zh:en;
const el=(tag,value='',className='')=>{const n=document.createElement(tag);n.textContent=value;n.className=className;return n;};
const batchPreferenceKey=design=>`friskoli-design-batch:${design.id}`;
export function restoreDesignBatchOptions(design,storage){
  let saved;try{saved=JSON.parse(storage?.getItem(batchPreferenceKey(design))??'null');}catch{}
  return {candidateId:design.candidates.some(c=>c.id===saved?.candidateId)?saved.candidateId:'',seed:saved?.seed!==''&&saved?.seed!==undefined&&design.brief.seeds.includes(Number(saved.seed))?String(saved.seed):'',batchSize:Number.isSafeInteger(saved?.batchSize)&&saved.batchSize>=1&&saved.batchSize<=8?saved.batchSize:4};
}
export function persistDesignBatchOptions(design,options,storage){
  try{storage?.setItem(batchPreferenceKey(design),JSON.stringify(options));return true;}catch{return false;}
}
export function numericDesignParameters(project,modules){
  return (project?.graph.nodes??[]).flatMap(node=>Object.entries(node.parameters).filter(([key,p])=>{
    const def=modules.get(`${node.module_id}@${node.module_version}`)?.parameters[key];return ['number','integer'].includes(def?.type)&&!def.enum&&Number.isFinite(p.value);
  }).map(([parameter,p])=>({node_id:node.id,parameter,value:p.value,unit:p.unit??'',label:`${node.id} · ${parameter} = ${p.value} ${p.unit??''}`})));
}
export function designRunQueue(design){
  if(!design?.candidates?.some(c=>c.kind==='candidate'))throw new Error('No feasible candidates');
  return design.candidates.flatMap(candidate=>design.brief.seeds.map(seed=>{const project=structuredClone(candidate.project);if(Object.hasOwn(project,'random_seed'))project.random_seed=seed;return {project,seed,design_ref:{design_id:design.id,candidate_id:candidate.id,candidate_name:candidate.name}};}));
}
export function planDesignBatch(design,runs=[],{candidateIds=null,seeds=null,batchSize=4}={}){
  if(!Number.isSafeInteger(batchSize)||batchSize<1||batchSize>8)throw Error('Batch size must be between 1 and 8');
  if(!design.candidates.some(c=>c.kind==='candidate'))return {queue:[],eligible:0,remainingAfterBatch:0,missing:0,completed:0,active:0,total:0};
  const candidates=new Set(candidateIds??design.candidates.map(c=>c.id)),selectedSeeds=new Set(seeds??design.brief.seeds);
  if([...candidates].some(id=>!design.candidates.some(c=>c.id===id))||[...selectedSeeds].some(seed=>!design.brief.seeds.includes(seed)))throw Error('Selection is outside the frozen design');
  const planned=designRunQueue(design),matching=runs.filter(r=>r.design_ref?.design_id===design.id);
  const successful=new Set(),active=new Set();
  for(const run of matching){const token=JSON.stringify([run.design_ref.candidate_id,run.submission?.execution?.seed??run.settings?.seed]);
    if(run.status==='completed'&&run.completeness==='complete')successful.add(token);
    else if(!['failed','cancelled','interrupted','rejected','unavailable'].includes(run.status))active.add(token);
  }
  const missing=planned.filter(item=>{const token=JSON.stringify([item.design_ref.candidate_id,item.seed]);return !successful.has(token)&&!active.has(token);});
  const selected=missing.filter(item=>candidates.has(item.design_ref.candidate_id)&&selectedSeeds.has(item.seed));
  return {queue:selected.slice(0,batchSize),eligible:selected.length,remainingAfterBatch:selected.length-Math.min(batchSize,selected.length),
    missing:missing.length,completed:successful.size,active:active.size,total:planned.length};
}
export function designPackagePayload(state,workspace,registry){return {package_version:'0.1.0',design:structuredClone(state.design),workspace,runs:structuredClone(state.runs.filter(r=>r.design_ref?.design_id===state.design?.id)),registry};}
export function defaultAssemblyMetadata(project,groupId){
  const name=project?.groups?.[groupId]?.name??groupId??project?.id??'population';
  return {id:`${String(name).toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')||'assembly'}-assembly`,name:`${name} mechanism`,version:'1.0.0',kind:'part',biological_role:'population mechanism',provenance:{kind:'user',reference:'Friskoli-CAD workspace'}};
}
export function enableResultEvaluation(brief){
  if(brief.brief_version==='0.2.0')return false;
  brief.brief_version='0.2.0';brief.result_constraints=[];brief.selection_policy={min_repeats:2,min_control_improvement:0};return true;
}
const evaluationReasons={
  selection_policy_not_configured:['This saved design has no evaluation policy. Edit its brief and generate a new design.','此设计未配置评价规则。编辑目标表单并生成新设计后再评价。'],
  insufficient_candidate_count:['At least two feasible candidates are required.','至少需要两个可行候选。'],
  unique_control_required:['One named control is required.','需要唯一的具名对照。'],
  incomplete_candidate_evidence:['Some candidate runs are missing or incomplete.','部分候选的运行缺失或未完成。'],
  incompatible_series:['Runs do not share compatible observations, seeds or implementation versions.','运行的观测定义、seed 或实现版本不兼容。'],
  insufficient_repeats:['The minimum number of distinct repeats has not been reached.','独立重复次数未达到要求。'],
  no_eligible_candidate:['No candidate satisfies the evaluation rules.','没有候选满足评价规则。'],
  tied_objective:['The objective has no unique best candidate.','主目标没有唯一最佳候选。'],
  missing_seed:['A required seed is missing.','缺少指定 seed 的运行。'],
  duplicate_seed:['A seed appears more than once.','存在重复 seed。'],
  result_constraint_unknown:['A result threshold cannot be evaluated.','结果阈值缺少可评价的数值。'],
  hard_result_constraint_failed:['A hard result threshold failed in at least one repeat.','至少一次重复未达到结果硬约束。'],
  control_unavailable:['A complete matching control is unavailable.','缺少完整且匹配的对照。'],
  control_improvement_not_met:['Not every paired repeat exceeds the control improvement threshold.','并非每次配对重复都超过对照改善阈值。'],
};
export const evaluationReason=code=>evaluationReasons[code]?text(...evaluationReasons[code]):String(code);
const resultNumber=value=>Number.isFinite(value)?Number(value.toPrecision(5)).toString():'—';
export function renderDesignEvaluation(root,evaluation){
  if(!evaluation)return;
  const panel=el('section','','design-evaluation');panel.setAttribute('aria-live','polite');
  panel.append(el('h3',evaluation.status==='recommended'?text('Exploratory recommendation','探索性推荐'):text('No recommendation','未给出推荐')));
  if(evaluation.recommended_candidate_id){const chosen=evaluation.candidates.find(c=>c.candidate_id===evaluation.recommended_candidate_id);panel.append(el('p',`${chosen?.name??''} · ${evaluation.recommended_candidate_id}`,'design-recommendation'));}
  for(const reason of evaluation.reasons??[])panel.append(el('p',evaluationReason(reason),'task-note'));
  panel.append(el('p',text('Uses complete, matched seed repeats. A recommendation requires every paired improvement to exceed the declared threshold. These simulations do not establish statistical significance or experimental performance.','使用完整且 seed 匹配的重复。推荐要求每次配对改善都超过声明的阈值；仿真结果不代表统计显著性或实验性能。'),'task-note'));
  const scroll=el('div','','design-table-scroll'),table=el('table','','design-evaluation-table'),head=el('tr');
  for(const title of [text('Candidate / ID','候选 / ID'),text('Decision','评价'),text('Objective mean ± SD (n)','目标均值 ± SD（n）'),text('Paired improvement mean ± SD [min, max]','配对改善均值 ± SD［最小, 最大］')])head.append(el('th',title));
  const thead=el('thead');thead.append(head);table.append(thead);const body=el('tbody');
  for(const candidate of evaluation.candidates??[]){const row=el('tr');row.append(el('td',`${candidate.name} · ${candidate.candidate_id}`));
    const decision=el('td',text(candidate.status,{eligible:'满足条件',excluded:'排除',incomplete:'证据不完整'}[candidate.status]??candidate.status));
    for(const reason of candidate.reasons??[])decision.append(el('p',evaluationReason(reason),'task-note'));row.append(decision);
    const objective=candidate.objective;row.append(el('td',`${resultNumber(objective?.mean)} ± ${resultNumber(objective?.sample_sd)} (${objective?.n??0})`));
    const delta=candidate.control_comparison;row.append(el('td',delta?`${resultNumber(delta.mean)} ± ${resultNumber(delta.sample_sd)} [${resultNumber(delta.minimum)}, ${resultNumber(delta.maximum)}] (${delta.n})`:'—'));body.append(row);
    for(const constraint of candidate.constraints??[]){const line=el('tr','','design-constraint-result'),cell=el('td');cell.colSpan=4;cell.append(el('span',`${constraint.id} · ${constraint.kind} · ${constraint.metric?t(constraint.metric):''} ${constraint.group_id??''} ${constraint.operator??''} ${constraint.value??''}: `),el('strong',text(constraint.status,{pass:'通过',fail:'未通过',unknown:'未知'}[constraint.status]??constraint.status),`result-${constraint.status}`),el('span',` · [${resultNumber(constraint.minimum)}, ${resultNumber(constraint.maximum)}]`));line.append(cell);body.append(line);}
  }
  table.append(body);scroll.append(table);panel.append(scroll);
  if(evaluation.limitations?.length){const details=el('details'),summary=el('summary',text('Evaluation limitations / original notes','评价限制 / 原始说明'));details.append(summary);for(const note of evaluation.limitations)details.append(el('p',note,'task-note'));panel.append(details);}
  for(const candidate of evaluation.candidates??[]){
    if(!candidate.excluded_attempts?.length)continue;
    const details=el('details');details.append(el('summary',`${candidate.name} · ${text('Earlier attempts','历史尝试')}`));
    for(const attempt of candidate.excluded_attempts)details.append(el('p',`${attempt.run_id} · seed ${attempt.seed} · ${attempt.status} · ${attempt.reason}${attempt.superseded_by_complete_repeat?text(' · a complete repeat is available',' · 已有完整重复'):''}`,'task-note'));
    panel.append(details);
  }
  root.append(panel);
}
export function renderDesignPanel(root,state,actions){
  root.replaceChildren();
  const heading=el('header','', 'design-heading');heading.append(el('h2',text('Design & compare','设计与比较')),el('p',text('Define a parameter scan from the current project. Generated candidates preserve the actual mechanism graph.','基于当前项目定义参数扫描。候选保留实际机制图。')));root.append(heading);
  const toolbar=el('div','','design-actions');const button=(parent,en,zh,fn,disabled=false)=>{const b=el('button',text(en,zh),'inspector-action');b.type='button';b.disabled=disabled;b.addEventListener('click',()=>Promise.resolve().then(fn).catch(actions.error));parent.append(b);return b;};
  button(toolbar,'Import .friskoli','导入 .friskoli',actions.import);root.append(toolbar);
  if(state.project?.execution_profile!=='chemotaxis-spatial-v1'){root.append(el('p',text('Open an N3 chemotaxis template to start a design.','先打开 N3 趋化模板再建立设计。')));return;}
  const groupIds=Object.keys(state.project.groups??{});
  if(groupIds.length){
    const fallback=defaultAssemblyMetadata(state.project,groupIds[0]);
    const draft=state.assemblyDraft??=({...structuredClone(fallback),group_id:groupIds[0],provenance_kind:fallback.provenance.kind,provenance_reference:fallback.provenance.reference});
    if(!groupIds.includes(draft.group_id))draft.group_id=groupIds[0];
    draft.provenance??=structuredClone(fallback.provenance);
    const panel=el('details','','design-assemblies');panel.open=true;panel.append(el('summary',text('Biological chassis / component assembly','生物底盘 / 元件 assembly')));
    panel.append(el('p',text('A reusable assembly contains one population and its owned workflow branch. External environment providers are checked when it is applied.','可复用 assembly 包含一个菌群及其所属 workflow 分支；应用时会检查外部环境提供者。'),'task-note'));
    const fields=el('div','','design-assembly-form');
    const field=(label,key,type='text')=>{const row=el('label','','design-field');row.append(el('span',label));const input=document.createElement('input');input.type=type;input.value=draft[key]??'';input.required=true;input.addEventListener('change',()=>{draft[key]=input.value;});row.append(input);fields.append(row);return input;};
    const choice=(label,key,options)=>{const row=el('label','','design-field');row.append(el('span',label));const select=document.createElement('select');for(const [value,name] of options)select.append(new Option(name,value));select.value=draft[key]??options[0][0];select.addEventListener('change',()=>{draft[key]=select.value;});row.append(select);fields.append(row);return select;};
    choice(text('Population','菌群'), 'group_id', groupIds.map(id=>[id,id]));
    field(text('Assembly ID','assembly ID'),'id');field(text('Name','名称'),'name');field(text('Version','版本'),'version');
    choice(text('Kind','类型'),'kind',[['part',text('Part · mechanism branch','元件 · 机制分支')],['chassis',text('Chassis · mechanism + geometry','底盘 · 机制与几何')]]);
    field(text('Biological role','生物学作用'),'biological_role');
    choice(text('Provenance kind','来源类型'),'provenance_kind',[['user',text('User','用户')],['example',text('Example','示例')],['literature',text('Literature','文献')],['measurement',text('Measurement','测量')],['calibration',text('Calibration','标定')]]);
    field(text('Provenance reference','来源说明'),'provenance_reference');
    // Keep the compact form flat for keyboard and small-screen use, then rebuild the nested contract on submit.
    panel.append(fields);const actionsRow=el('div','','design-actions');
    const metadata=()=>({id:draft.id,name:draft.name,version:draft.version,kind:draft.kind,biological_role:draft.biological_role,provenance:{kind:draft.provenance_kind??draft.provenance?.kind??'user',reference:draft.provenance_reference??draft.provenance?.reference??''}});
    button(actionsRow,'Export biological assembly','导出生物 assembly',()=>actions.exportAssembly({groupId:draft.group_id,metadata:metadata()}),Boolean(state.assemblyBusy));
    const fileInput=document.createElement('input');fileInput.type='file';fileInput.accept='.json,.assembly,.friskoli-assembly.json';fileInput.hidden=true;fileInput.addEventListener('change',()=>{const file=fileInput.files?.[0];if(file)actions.importAssembly(file,draft.group_id).catch(actions.error);fileInput.value='';});panel.append(fileInput);
    button(actionsRow,'Apply assembly…','应用 assembly…',()=>fileInput.click(),Boolean(state.assemblyBusy));panel.append(actionsRow);
    if(state.assemblyBusy)panel.append(el('p',text('Assembly request in progress…','assembly 请求进行中…'),'task-note'));
    if(state.assemblyNotice)panel.append(el('p',state.assemblyNotice,'task-note'));
    if(state.assemblyError)panel.append(el('p',state.assemblyError,'error'));
    root.append(panel);
  }
  const params=numericDesignParameters(state.project,state.modules),first=params.find(p=>/copies|bias|speed/.test(p.parameter))??params[0];
  if(!first){root.append(el('p',text('No registered numeric parameters are available.','没有可用的已注册数值参数。')));return;}
  const brief=state.designBrief??=structuredClone(state.design?.brief??{brief_version:'0.2.0',id:`design-${Date.now()}`,name:'Parameter scan',goal:{metric:'mean_displacement_um',direction:'maximize',group_id:Object.keys(state.project.groups)[0]},chassis:{name:state.project.id,provenance:'User-defined exploratory design'},variables:[{node_id:first.node_id,parameter:first.parameter,values:[first.value,first.value===0?1:first.value*1.2]}],constraints:[],result_constraints:[],selection_policy:{min_repeats:2,min_control_improvement:0},seeds:[0,1,2],max_runs:32});
  const markEdited=()=>{const upgraded=enableResultEvaluation(brief);actions.changed();if(upgraded)actions.render();};
  const form=el('form','','design-form');form.addEventListener('submit',event=>{event.preventDefault();actions.generate(brief).catch(actions.error);});root.append(form);
  const input=(parent,label,value,change,type='text')=>{const row=el('label','','design-field');row.append(el('span',label));const i=el('input');i.type=type;if(type==='number')i.step='any';i.value=value;i.required=true;i.addEventListener('change',()=>{change(i.value);markEdited();});row.append(i);parent.append(row);return i;};
  const select=(parent,label,options,value,change)=>{const row=el('label','','design-field');row.append(el('span',label));const s=el('select');for(const [key,name] of options)s.append(new Option(name,key));s.value=value;s.addEventListener('change',()=>{change(s.value);markEdited();});row.append(s);parent.append(row);return s;};
  input(form,text('Design name','设计名称'),brief.name,v=>brief.name=v);input(form,text('Design ID','设计 ID'),brief.id,v=>brief.id=v);
  input(form,text('Chassis / strain','底盘 / 菌株说明'),brief.chassis.name,v=>brief.chassis.name=v);input(form,text('Chassis source / rationale','底盘来源 / 依据'),typeof brief.chassis.provenance==='string'?brief.chassis.provenance:JSON.stringify(brief.chassis.provenance),v=>brief.chassis.provenance=v);
  select(form,text('Objective metric','目标指标'),['mean_displacement_um','region_fraction','ever_arrived_fraction','mean_residence_s'].map(k=>[k,t(k)]),brief.goal.metric,v=>brief.goal.metric=v);
  select(form,text('Direction','优化方向'),[['maximize',text('Maximize','最大化')],['minimize',text('Minimize','最小化')]],brief.goal.direction,v=>brief.goal.direction=v);
  select(form,text('Population','菌群'),Object.keys(state.project.groups).map(k=>[k,k]),brief.goal.group_id,v=>brief.goal.group_id=v);
  input(form,text('Execution seeds (comma-separated)','执行 seed（逗号分隔）'),brief.seeds.join(', '),v=>brief.seeds=v.split(/[,，\s]+/).filter(Boolean).map(Number));
  input(form,text('Maximum runs','运行预算上限'),brief.max_runs,v=>brief.max_runs=Number(v),'number');
  const scans=el('section','','design-wide');scans.append(el('h3',text('Parameter scan','参数扫描')));form.append(scans);
  brief.variables.forEach((variable,index)=>{const row=el('div','','design-row');const opts=params.map((p,i)=>[String(i),p.label]);const selected=params.findIndex(p=>p.node_id===variable.node_id&&p.parameter===variable.parameter);
    select(row,text('Node · parameter','节点 · 参数'),opts,String(Math.max(0,selected)),v=>{variable.node_id=params[v].node_id;variable.parameter=params[v].parameter;});input(row,text('Values (comma-separated)','枚举值（逗号分隔）'),variable.values.join(', '),v=>variable.values=v.split(/[,，\s]+/).filter(Boolean).map(Number));button(row,'Remove','移除',()=>{brief.variables.splice(index,1);actions.changed();actions.render();},brief.variables.length===1);scans.append(row);});
  button(scans,'Add parameter','添加参数',()=>{brief.variables.push({node_id:first.node_id,parameter:first.parameter,values:[first.value]});actions.changed();actions.render();});
  const constraints=el('section','','design-wide');constraints.append(el('h3',text('Constraints','约束')));form.append(constraints);
  brief.constraints.forEach((constraint,index)=>{const row=el('div','','design-row');select(row,text('Kind','类型'),[['hard',text('Hard / exclude','硬约束 / 排除')],['soft',text('Soft / penalty','软约束 / 惩罚')]],constraint.kind,v=>constraint.kind=v);
    select(row,text('Parameter','参数'),params.map((p,i)=>[String(i),p.label]),String(Math.max(0,params.findIndex(p=>p.node_id===constraint.node_id&&p.parameter===constraint.parameter))),v=>{constraint.node_id=params[v].node_id;constraint.parameter=params[v].parameter;});
    select(row,text('Operator','关系'),[['<=','≤'],['>=','≥']],constraint.operator,v=>constraint.operator=v);input(row,text('Limit','界限'),constraint.value,v=>constraint.value=Number(v),'number');input(row,text('Weight (soft)','权重（软约束）'),constraint.weight??1,v=>constraint.weight=Number(v),'number');button(row,'Remove','移除',()=>{brief.constraints.splice(index,1);actions.changed();actions.render();});constraints.append(row);});
  button(constraints,'Add constraint','添加约束',()=>{brief.constraints.push({id:`constraint-${Date.now()}`,kind:'hard',node_id:first.node_id,parameter:first.parameter,operator:'>=',value:0,weight:1});actions.changed();actions.render();});
  const criteria=el('section','','design-wide');criteria.append(el('h3',text('Result thresholds & selection','结果阈值与推荐规则')));form.append(criteria);
  if(brief.brief_version==='0.1.0'){
    criteria.append(el('p',text('This legacy brief keeps its original settings. Editing the brief enables version 0.2 evaluation rules; generate a new design before applying them to runs.','旧目标表单保留原始设置。编辑表单将启用 0.2 评价规则；请生成新设计后再运行。'),'task-note'));
    button(criteria,'Enable result evaluation','启用结果评价',()=>{markEdited();actions.render();});
  }else{
    input(criteria,text('Minimum distinct repeats (2–8)','最少独立重复次数（2–8）'),brief.selection_policy.min_repeats,v=>brief.selection_policy.min_repeats=Number(v),'number');
    input(criteria,text('Minimum improvement over control, in objective units, for every seed','每个 seed 相对对照的最小改善（目标指标单位）'),brief.selection_policy.min_control_improvement,v=>brief.selection_policy.min_control_improvement=Number(v),'number');
    criteria.append(el('p',text('Hard thresholds must pass in every repeat. Soft thresholds are reported separately and never override hard failures.','每次重复都须满足硬阈值。软阈值单独展示，不会覆盖硬约束失败。'),'task-note'));
    brief.result_constraints.forEach((constraint,index)=>{const row=el('div','','design-row');
      input(row,text('Threshold ID','阈值 ID'),constraint.id,v=>constraint.id=v);
      select(row,text('Kind','类型'),[['hard',text('Hard','硬约束')],['soft',text('Soft','软约束')]],constraint.kind,v=>constraint.kind=v);
      select(row,text('Metric','指标'),['mean_displacement_um','region_fraction','ever_arrived_fraction','mean_residence_s'].map(k=>[k,t(k)]),constraint.metric,v=>constraint.metric=v);
      select(row,text('Population','菌群'),Object.keys(state.project.groups).map(k=>[k,k]),constraint.group_id,v=>constraint.group_id=v);
      select(row,text('Operator','关系'),[['<=','≤'],['>=','≥']],constraint.operator,v=>constraint.operator=v);
      input(row,text('Limit','界限'),constraint.value,v=>constraint.value=Number(v),'number');
      button(row,'Remove','移除',()=>{brief.result_constraints.splice(index,1);markEdited();actions.render();});criteria.append(row);
    });
    button(criteria,'Add result threshold','添加结果阈值',()=>{brief.result_constraints.push({id:`result-${Date.now()}`,kind:'hard',metric:brief.goal.metric,group_id:brief.goal.group_id,operator:'>=',value:0});markEdited();actions.render();});
  }
  const generate=el('button',text('Generate candidates','生成候选'),'run-button');generate.type='submit';generate.disabled=Boolean(state.batch||state.designBusy);form.append(generate);
  if(state.designNotice)root.append(el('p',state.designNotice,'task-note'));
  if(state.designImported)root.append(el('p',text('Imported history is read-only. A saved browser archive can restore it after refresh; keep the original package as a portable backup.','导入历史只读。已保存的浏览器归档可在刷新后恢复；原生包仍应保留为可携带备份。'),'task-note'));
  if(state.designError)root.append(el('p',state.designError,'error'));
  if(!state.design)return;
  const design=state.design,result=el('section','','design-results');root.append(result);result.append(el('h3',`${design.brief.name} · ${design.id}`));
  result.append(el('p',text('Frozen design input. Changing the editor or brief requires generating a new design.','设计输入已冻结。修改编辑器或目标表单后，需要重新生成设计。')));
  result.append(el('p',`${text('Budget','预算')}: ${design.budget.candidate_count} ${text('branches','分支')} × ${design.budget.repeats} seeds = ${design.budget.total_runs} ${text('runs','次运行')} · ${design.budget.total_steps} steps`));
  if(Number.isFinite(design.budget.max_memory_bytes)&&Number.isFinite(design.budget.total_output_bytes))result.append(el('p',`${text('Estimated peak memory','预计峰值内存')}: ${(design.budget.max_memory_bytes/1048576).toFixed(1)} MiB · ${text('Estimated total output','预计总输出')}: ${(design.budget.total_output_bytes/1048576).toFixed(1)} MiB`));
  if(design.budget.feasible_candidate_count<2)result.append(el('p',text('Fewer than two feasible candidates. This design cannot support a candidate recommendation.','可行候选不足两项，本设计无法支持候选推荐。'),'task-note'));
  const actionsRow=el('div','','design-actions');result.append(actionsRow);
  const runs=state.runs.filter(r=>r.design_ref?.design_id===design.id);const completed=planDesignBatch(design,runs).completed;
  result.append(el('p',`${text('Completed','已完成')}: ${completed}/${design.budget.total_runs}${state.designBatchStatus?' · '+state.designBatchStatus:''}`));
  let preferenceStorage;try{preferenceStorage=globalThis.localStorage;}catch{}
  if(state.designBatchOptionsId!==design.id){state.designBatchOptions=restoreDesignBatchOptions(design,preferenceStorage);state.designBatchOptionsId=design.id;}
  const batchOptions=state.designBatchOptions;
  const saveBatchOptions=()=>{persistDesignBatchOptions(design,batchOptions,preferenceStorage);actions.render();};
  if(batchOptions.candidateId&&!design.candidates.some(c=>c.id===batchOptions.candidateId))batchOptions.candidateId='';
  if(batchOptions.seed!==''&&batchOptions.seed!==undefined&&!design.brief.seeds.includes(Number(batchOptions.seed)))batchOptions.seed='';
  const candidateSelect=el('select');candidateSelect.add(new Option(text('All candidates and control','全部候选及对照'),''));for(const c of design.candidates)candidateSelect.add(new Option(c.name,c.id));candidateSelect.value=batchOptions.candidateId??'';candidateSelect.addEventListener('change',()=>{batchOptions.candidateId=candidateSelect.value;saveBatchOptions();});
  const seedSelect=el('select');seedSelect.add(new Option(text('All planned seeds','全部计划 seed'),''));for(const seed of design.brief.seeds)seedSelect.add(new Option(String(seed),String(seed)));seedSelect.value=batchOptions.seed??'';seedSelect.addEventListener('change',()=>{batchOptions.seed=seedSelect.value;saveBatchOptions();});
  const batchInput=el('input');batchInput.type='number';batchInput.min='1';batchInput.max='8';batchInput.value=batchOptions.batchSize??4;batchInput.addEventListener('change',()=>{batchOptions.batchSize=Number(batchInput.value);saveBatchOptions();});
  for(const [label,control] of [[text('Candidate selection','候选选择'),candidateSelect],[text('Seed selection','seed 选择'),seedSelect],[text('Runs per batch (1–8)','每批运行数（1–8）'),batchInput]]){const row=el('label',label,'design-field');control.setAttribute('aria-label',label);row.append(control);result.append(row);}
  const selection={candidateIds:batchOptions.candidateId?[batchOptions.candidateId]:null,seeds:batchOptions.seed!==''&&batchOptions.seed!==undefined?[Number(batchOptions.seed)]:null,batchSize:batchOptions.batchSize??4};
  let plan;try{plan=planDesignBatch(design,runs,selection);}catch(error){result.append(el('p',error.message,'error'));}
  result.append(el('p',plan?`${text('Eligible missing/failed repeats','可继续的缺失/失败重复')}: ${plan.eligible} · ${text('This batch','本批')}: ${plan.queue.length} · ${text('Remaining after this batch','本批后剩余')}: ${plan.remainingAfterBatch}`:''));
  button(actionsRow,'Continue selected missing/failed runs','继续所选缺失/失败运行',()=>actions.run(selection),Boolean(state.batch||state.designBusy||!plan?.queue.length));
  if(state.batch?.designId===design.id)button(actionsRow,'Stop batch','停止批次',actions.stop);
  button(actionsRow,'Compare completed runs','比较已完成运行',actions.compare,completed<2);
  button(actionsRow,state.designEvaluating?'Evaluating…':'Evaluate results',state.designEvaluating?'评价中…':'评价结果',actions.evaluate,Boolean(state.batch||state.designBusy||state.designEvaluating));
  button(actionsRow,'Export .friskoli','导出 .friskoli',()=>actions.export('package'));
  button(actionsRow,'Clear exported design history','清理已导出的设计历史',actions.clear,Boolean(state.batch)||!runs.length);
  button(actionsRow,'HTML report','HTML 报告',()=>actions.export('html'));button(actionsRow,'CSV report','CSV 报告',()=>actions.export('csv'));
  const formats=el('details','','standard-export');formats.append(el('summary',text('Standard exchange','标准交换')));
  formats.append(el('p',text('OMEX preserves the native design and a loss report. It does not claim an executable SBML/SED-ML simulation.','OMEX 保存原生设计和格式损失报告，不声明包含可执行的 SBML/SED-ML 仿真。'),'task-note'));
  button(formats,'Export OMEX','导出 OMEX',()=>actions.export('omex'),Boolean(state.designExporting));
  button(formats,'Download loss report','下载损失报告',()=>actions.export('loss-report'),Boolean(state.designExporting));
  formats.append(el('p',text('SBOL3 Component export requires explicit biological identities and evidence; use the documented CLI or API with a component source file. Sequence and SBML/SED-ML mappings are not available in this version.','SBOL3 Component 导出需要明确的生物标识与证据，请通过文档中的 CLI 或 API 提供元件来源文件。本版尚无序列和 SBML/SED-ML 映射。'),'task-note'));
  result.append(formats);
  if(state.designEvaluation?.design_id===design.id)renderDesignEvaluation(result,state.designEvaluation);
  for(const candidate of design.candidates){const card=el('article','','design-candidate');card.append(el('h4',`${candidate.name} · ${candidate.id}`),el('p',`${candidate.kind} · ${candidate.explanation} · ${text('soft penalty','软约束惩罚')}: ${candidate.soft_penalty}`));const ul=el('ul');for(const o of candidate.overrides)ul.append(el('li',`${o.node_id}.${o.parameter} → ${o.value} ${o.unit??''}`));card.append(ul);const own=runs.filter(r=>r.design_ref.candidate_id===candidate.id);for(const run of own){const b=button(card,`${run.settings?.seed??run.submission?.execution.seed}: ${t(run.status)} · ${run.id}`,`${run.settings?.seed??run.submission?.execution.seed}: ${t(run.status)} · ${run.id}`,()=>actions.view(run),!run.replay);b.classList.add('design-run');}button(card,'Open candidate graph','查看候选图',()=>actions.open(candidate));result.append(card);}
  if(design.excluded.length){result.append(el('h3',text('Excluded candidates','已排除候选')));for(const c of design.excluded)result.append(el('p',`${c.name} · ${c.id}: ${c.reasons.join('; ')}`));}
}
