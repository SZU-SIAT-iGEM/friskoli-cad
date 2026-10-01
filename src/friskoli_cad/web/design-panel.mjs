import {currentLanguage,t} from './i18n.mjs';
const text=(en,zh)=>currentLanguage()==='zh-CN'?zh:en;
const el=(tag,value='',className='')=>{const n=document.createElement(tag);n.textContent=value;n.className=className;return n;};
export function numericDesignParameters(project,modules){
  return (project?.graph.nodes??[]).flatMap(node=>Object.entries(node.parameters).filter(([key,p])=>{
    const def=modules.get(`${node.module_id}@${node.module_version}`)?.parameters[key];return ['number','integer'].includes(def?.type)&&!def.enum&&Number.isFinite(p.value);
  }).map(([parameter,p])=>({node_id:node.id,parameter,value:p.value,unit:p.unit??'',label:`${node.id} · ${parameter} = ${p.value} ${p.unit??''}`})));
}
export function designRunQueue(design){
  if(!design?.candidates?.some(c=>c.kind==='candidate'))throw new Error('No feasible candidates');
  return design.candidates.flatMap(candidate=>design.brief.seeds.map(seed=>({project:structuredClone(candidate.project),seed,design_ref:{design_id:design.id,candidate_id:candidate.id,candidate_name:candidate.name}})));
}
export function designPackagePayload(state,workspace,registry){return {package_version:'0.1.0',design:structuredClone(state.design),workspace,runs:structuredClone(state.runs.filter(r=>r.design_ref?.design_id===state.design?.id)),registry};}
export function renderDesignPanel(root,state,actions){
  root.replaceChildren();
  const heading=el('header','', 'design-heading');heading.append(el('h2',text('Design & compare','设计与比较')),el('p',text('Define a parameter scan from the current project. Generated candidates preserve the actual mechanism graph.','基于当前项目定义参数扫描。候选保留实际机制图。')));root.append(heading);
  const toolbar=el('div','','design-actions');const button=(parent,en,zh,fn,disabled=false)=>{const b=el('button',text(en,zh),'inspector-action');b.type='button';b.disabled=disabled;b.addEventListener('click',()=>Promise.resolve(fn()).catch(actions.error));parent.append(b);return b;};
  button(toolbar,'Import .friskoli','导入 .friskoli',actions.import);root.append(toolbar);
  if(state.project?.execution_profile!=='chemotaxis-spatial-v1'){root.append(el('p',text('Open an N3 chemotaxis template to start a design.','先打开 N3 趋化模板再建立设计。')));return;}
  const params=numericDesignParameters(state.project,state.modules),first=params.find(p=>/copies|bias|speed/.test(p.parameter))??params[0];
  if(!first){root.append(el('p',text('No registered numeric parameters are available.','没有可用的已注册数值参数。')));return;}
  const brief=state.designBrief??=structuredClone(state.design?.brief??{brief_version:'0.1.0',id:`design-${Date.now()}`,name:'Parameter scan',goal:{metric:'mean_displacement_um',direction:'maximize',group_id:Object.keys(state.project.groups)[0]},chassis:{name:state.project.id,provenance:'User-defined exploratory design'},variables:[{node_id:first.node_id,parameter:first.parameter,values:[first.value,first.value===0?1:first.value*1.2]}],constraints:[],seeds:[0,1,2],max_runs:32});
  const form=el('form','','design-form');form.addEventListener('submit',event=>{event.preventDefault();actions.generate(brief).catch(actions.error);});root.append(form);
  const input=(parent,label,value,change,type='text')=>{const row=el('label','','design-field');row.append(el('span',label));const i=el('input');i.type=type;if(type==='number')i.step='any';i.value=value;i.required=true;i.addEventListener('change',()=>{change(i.value);actions.changed();});row.append(i);parent.append(row);return i;};
  const select=(parent,label,options,value,change)=>{const row=el('label','','design-field');row.append(el('span',label));const s=el('select');for(const [key,name] of options)s.append(new Option(name,key));s.value=value;s.addEventListener('change',()=>{change(s.value);actions.changed();});row.append(s);parent.append(row);return s;};
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
  const generate=el('button',text('Generate candidates','生成候选'),'run-button');generate.type='submit';generate.disabled=Boolean(state.batch||state.designBusy);form.append(generate);
  if(state.designNotice)root.append(el('p',state.designNotice,'task-note'));
  if(state.designImported)root.append(el('p',text('Imported run history is read-only in this tab. Keep the .friskoli package to reopen these records after refresh.','导入的运行历史在当前标签页中只读显示。刷新后请重新打开 .friskoli 包；请保留原包。'),'task-note'));
  if(state.designError)root.append(el('p',state.designError,'error'));
  if(!state.design)return;
  const design=state.design,result=el('section','','design-results');root.append(result);result.append(el('h3',`${design.brief.name} · ${design.id}`));
  result.append(el('p',text('Frozen design input. Changing the editor or brief requires generating a new design.','设计输入已冻结。修改编辑器或目标表单后，需要重新生成设计。')));
  result.append(el('p',`${text('Budget','预算')}: ${design.budget.candidate_count} ${text('branches','分支')} × ${design.budget.repeats} seeds = ${design.budget.total_runs} ${text('runs','次运行')} · ${design.budget.total_steps} steps`));
  if(Number.isFinite(design.budget.max_memory_bytes)&&Number.isFinite(design.budget.total_output_bytes))result.append(el('p',`${text('Estimated peak memory','预计峰值内存')}: ${(design.budget.max_memory_bytes/1048576).toFixed(1)} MiB · ${text('Estimated total output','预计总输出')}: ${(design.budget.total_output_bytes/1048576).toFixed(1)} MiB`));
  if(design.budget.feasible_candidate_count<2)result.append(el('p',text('Fewer than two feasible candidates. This design cannot support a candidate recommendation.','可行候选不足两项，本设计无法支持候选推荐。'),'task-note'));
  const actionsRow=el('div','','design-actions');result.append(actionsRow);
  const runs=state.runs.filter(r=>r.design_ref?.design_id===design.id);const completed=runs.filter(r=>r.status==='completed').length;
  result.append(el('p',`${text('Completed','已完成')}: ${completed}/${design.budget.total_runs}${state.designBatchStatus?' · '+state.designBatchStatus:''}`));
  button(actionsRow,'Run all candidates × seeds','运行全部候选 × seed',actions.run,Boolean(state.batch||state.designBusy||!design.candidates.some(c=>c.kind==='candidate')||runs.length));
  if(state.batch?.designId===design.id)button(actionsRow,'Stop batch','停止批次',actions.stop);
  button(actionsRow,'Compare completed runs','比较已完成运行',actions.compare,completed<2);
  button(actionsRow,'Export .friskoli','导出 .friskoli',()=>actions.export('package'));
  button(actionsRow,'Clear exported design history','清理已导出的设计历史',actions.clear,Boolean(state.batch)||!runs.length);
  button(actionsRow,'HTML report','HTML 报告',()=>actions.export('html'));button(actionsRow,'CSV report','CSV 报告',()=>actions.export('csv'));
  for(const candidate of design.candidates){const card=el('article','','design-candidate');card.append(el('h4',`${candidate.name} · ${candidate.id}`),el('p',`${candidate.kind} · ${candidate.explanation} · ${text('soft penalty','软约束惩罚')}: ${candidate.soft_penalty}`));const ul=el('ul');for(const o of candidate.overrides)ul.append(el('li',`${o.node_id}.${o.parameter} → ${o.value} ${o.unit??''}`));card.append(ul);const own=runs.filter(r=>r.design_ref.candidate_id===candidate.id);for(const run of own){const b=button(card,`${run.settings?.seed??run.submission?.execution.seed}: ${t(run.status)} · ${run.id}`,`${run.settings?.seed??run.submission?.execution.seed}: ${t(run.status)} · ${run.id}`,()=>actions.view(run),!run.replay);b.classList.add('design-run');}button(card,'Open candidate graph','查看候选图',()=>actions.open(candidate));result.append(card);}
  if(design.excluded.length){result.append(el('h3',text('Excluded candidates','已排除候选')));for(const c of design.excluded)result.append(el('p',`${c.name} · ${c.id}: ${c.reasons.join('; ')}`));}
}
