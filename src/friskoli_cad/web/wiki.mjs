// Static reader only: every state and time step comes from saved historical frames.
// Shared rendering adapters are included; no kernel client or solver is loaded.
import {fieldDisplayDomain} from './field-slice.mjs';
const $ = id => document.getElementById(id);
const pretty = value => JSON.stringify(value, null, 2);
const number = value => typeof value === 'number' && Number.isFinite(value) ? Number(value.toPrecision(6)).toString() : value == null ? '未定义' : String(value);
let data, run, index=0, timer, viewport, catalog, registerCatalog, environmentObjects;
let selectedCell=null, selectedObject=null, objects=[], registry={modules:new Map(),objects:new Map()}, lastCellIds='';
const controls=['runs','camera','pan','fit','zoom-out','zoom-in','frame','field','cells','previous','play','next'];
const metricNames={initial_count:'初始菌体数',live_count:'存活菌体数',mean_displacement_um:'平均轴向位移 · μm',region_fraction:'区域内比例 · 1',ever_arrived_fraction:'曾到达比例 · 1',mean_residence_s:'平均停留时间 · s'};
function setStatus(text){$('status').textContent=text;}
function loading(title,detail='',fraction=null){$('loading').hidden=false;$('loading-title').textContent=title;$('loading-detail').textContent=detail;const p=$('load-progress');if(fraction===null)p.removeAttribute('value');else{p.max=1;p.value=fraction;}setStatus(title);}
function pause(){clearInterval(timer);timer=undefined;$('play').textContent='播放';$('play').setAttribute('aria-label','播放保存帧');}
function reading(root,key,value){const row=document.createElement('div');row.className='reading';const label=document.createElement('span'),text=document.createElement('span');label.textContent=key;text.textContent=value;row.append(label,text);root.append(row);}
function frame(){return run?.replay.snapshots[index];}
function fieldRange(field){let min=Infinity,max=-Infinity;for(const plane of field.values_zyx)for(const row of plane)for(const value of row){min=Math.min(min,value);max=Math.max(max,value);}return{min,max};}
function sliceOptions(){
  const axis={x:0,y:1,z:2}[$('normal').value],domain=fieldDisplayDomain(frame().concentrations[$('field').value],run.project.domain),old=Number($('slice').value)||0;
  $('slice').replaceChildren();for(let i=0;i<domain.counts_xyz[axis];i++)$('slice').add(new Option(`${number((i+.5)*domain.spacing_um_xyz[axis])}`,String(i)));
  $('slice').value=String(Math.min(old,domain.counts_xyz[axis]-1));
}
function renderSelection(){
  const root=$('selection');root.replaceChildren();
  if(selectedObject){const object=objects.find(item=>item.id===selectedObject);if(!object){root.textContent='该对象在当前帧不可显示。';return;}reading(root,'对象',object.id);reading(root,'类型',object.kind);if(object.center){reading(root,'中心 · μm',object.center.map(number).join(', '));reading(root,'作用半径 · μm',number(object.radius));}else{reading(root,'下界 · μm',object.lower.map(number).join(', '));reading(root,'上界 · μm',object.upper.map(number).join(', '));}if(object.remaining_molecules!==null)reading(root,'剩余分子数',number(object.remaining_molecules));return;}
  if(!selectedCell){root.textContent='点击菌体，或从记录面板选择对象。';return;}
  const cell=frame().frame.cells.find(item=>item.id===selectedCell);
  if(!cell){root.textContent=`${selectedCell} 不在当前保存帧中。`;return;}
  reading(root,'菌体 ID',cell.id);reading(root,'菌群',cell.group_id??'未记录');reading(root,'位置 XYZ · μm',cell.position_um.map(number).join(', '));
  if(cell.geometry){reading(root,'胶囊长度 · μm',number(cell.geometry.length_um));reading(root,'直径 · μm',number(cell.geometry.diameter_um));}else reading(root,'几何','未记录；线框仅作位置标记');
  const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='菌体完整记录与通道';pre.textContent=pretty(cell);details.append(summary,pre);root.append(details);
}
function renderMetrics(){const root=$('metrics');root.replaceChildren();const metrics=frame().metrics;if(!metrics){root.textContent='此记录未保存指标。';return;}reading(root,'观测定义',metrics.observation_id??'未记录');for(const [group,values] of Object.entries(metrics.by_group??{})){const title=document.createElement('p');title.className='reading-group';title.textContent=group;root.append(title);for(const [key,value] of Object.entries(values))reading(root,metricNames[key]??key,number(value));}}
function details(){if($('snapshot-details').open)$('snapshot').textContent=pretty(frame());if($('provenance-details').open)$('provenance').textContent=pretty({design_ref:run.design_ref,status:run.status,completeness:run.completeness,submission:run.submission,task:run.task,manifest:run.manifest,registry:data.registry,viewer_adapter_note:'viewer-catalog.json comes from the export software and is used only for known display adapters. It does not replace this frozen scientific provenance.'});}
function draw(){
  const snapshot=frame();if(!snapshot)return;
  const fieldId=$('field').value,field=snapshot.concentrations[fieldId],range=field?fieldRange(field):null;
  const normal=$('normal').value,slice=Number($('slice').value)||0;
  viewport.setSnapshot(snapshot,selectedCell,fieldId,slice,range,normal);
  objects=environmentObjects(run.project,registry.objects,registry.modules,snapshot.object_states??{});viewport.setObjects(objects,selectedObject);
  $('field-legend').hidden=!field;$('normal').disabled=!field;$('slice').disabled=!field;
  $('field-note').textContent=field?'当前帧全场范围；未插值、未重算。':'未叠加场；菌体位置来自当前保存帧。';
  if(field){$('field-range').textContent=`${number(range.min)} → ${number(range.max)} ${field.unit??'单位未记录'} · ${normal.toUpperCase()} = ${$('slice').selectedOptions[0]?.textContent} μm`;if(field.aggregation==='volume_mean')$('field-range').textContent+=` · 体积平均预览格距 ${field.field_domain.spacing_um_xyz.map(number).join(' × ')} μm · 计算格距 ${run.project.domain.spacing_um_xyz.map(number).join(' × ')} μm`;}
  $('frame').value=String(index);$('time').textContent=`${number(snapshot.frame.time_s)} s · ${index+1} / ${run.replay.snapshots.length}`;
  $('previous').disabled=index===0;$('next').disabled=index===run.replay.snapshots.length-1;
  $('previous').title=index===0?'已到第一保存帧':'上一保存帧';$('next').title=index===run.replay.snapshots.length-1?'已到最后保存帧':'下一保存帧';
  const ids=JSON.stringify(snapshot.frame.cells.map(cell=>cell.id));if(ids!==lastCellIds){$('cells').replaceChildren(new Option('未选择',''));for(const cell of snapshot.frame.cells)$('cells').add(new Option(cell.id,cell.id));lastCellIds=ids;}
  $('cells').value=selectedCell??'';$('counts').textContent=`${snapshot.frame.cells.length} 菌体 · ${run.replay.snapshots.length} 保存帧`;
  renderSelection();renderMetrics();details();
  try{localStorage.setItem('friskoli-wiki-position',JSON.stringify({record:data.record_id??data.design?.id,run:$('runs').value,index}));}catch{}
}
function selectCell(id){selectedCell=id||null;selectedObject=null;$('objects').value='';draw();if(matchMedia('(max-width:960px)').matches)openPanel('inspector',true);}
function selectObject(id){selectedObject=id||null;selectedCell=null;$('cells').value='';$('objects').value=id??'';draw();if(matchMedia('(max-width:960px)').matches)openPanel('inspector',true);}
function selectRun(value,position=0){
  pause();run=data.runs[Number(value)];index=Math.max(0,Math.min(position,run.replay.snapshots.length-1));selectedCell=null;selectedObject=null;lastCellIds='';$('runs').value=String(value);
  const profile=run.project.execution_profile??'modular-spatial-v1';registry={modules:new Map(),objects:new Map()};try{if(catalog.profiles[profile])registry=registerCatalog(catalog.profiles[profile]);}catch{setStatus('显示适配器不可读；仍保留菌体与原始记录。');}
  viewport.setDomain(run.project.domain);viewport.setMode('results');viewport.setTool('orbit');$('pan').setAttribute('aria-pressed','false');
  $('frame').max=run.replay.snapshots.length-1;for(const id of controls)$(id).disabled=false;
  $('field').replaceChildren(new Option('不显示场',''));for(const [id,field] of Object.entries(frame().concentrations))$('field').add(new Option(`${id} · ${field.unit??'单位未记录'}`,id));sliceOptions();
  $('objects').replaceChildren(new Option('未选择',''));objects=environmentObjects(run.project,registry.objects,registry.modules,frame().object_states??{});for(const object of objects)$('objects').add(new Option(`${object.id} · ${object.kind}`,object.id));$('objects').disabled=!objects.length;$('objects').title=objects.length?'选择环境对象':'记录中没有支持显示的环境对象';
  const summary=$('run-summary');summary.replaceChildren();reading(summary,'执行 seed',number(run.submission.execution.seed));reading(summary,'保存帧',String(run.replay.snapshots.length));reading(summary,'结束时间',`${number(run.replay.snapshots.at(-1).frame.time_s)} s`);reading(summary,'运行 ID',run.id??run.runId??run.localId);
  $('project-name').textContent=run.project.name??run.project.id;$('domain-label').textContent=run.project.domain.counts_xyz.map((n,i)=>number(n*run.project.domain.spacing_um_xyz[i])).join(' × ')+' μm';
  const unknown=run.project.graph.nodes.filter(node=>!registry.modules.has(`${node.module_id}@${node.module_version}`)).length;$('adapter-note').textContent=`显示适配器来自导出软件，仅用于几何显示，不替换冻结来源。${unknown?` ${unknown} 个未知模块未补造几何。`:''}`;
  draw();viewport.fit();$('loading').hidden=true;performance.measure('wiki-ready-from-navigation',{start:0,end:performance.now()});setStatus(`已载入 ${data.runs.length} 条完整历史运行 · 只读`);
}
function togglePlay(){if(!run)return;if(timer)return pause();if(index===run.replay.snapshots.length-1)index=0;$('play').textContent='暂停';$('play').setAttribute('aria-label','暂停回放');draw();timer=setInterval(()=>{if(index>=run.replay.snapshots.length-1)return pause();index++;draw();if(index===run.replay.snapshots.length-1)pause();},1000/Number($('speed').value));}
function step(delta){if(!run)return;pause();index=Math.max(0,Math.min(run.replay.snapshots.length-1,index+delta));draw();}
function openPanel(name,force=false){const panel=$(name==='records'?'records-panel':'inspector-panel'),button=$(`${name}-toggle`);const open=force||!panel.classList.contains('open');panel.classList.toggle('open',open);button.setAttribute('aria-expanded',String(open));if(open)panel.scrollIntoView({block:'start'});}
$('records-toggle').addEventListener('click',()=>openPanel('records'));$('inspector-toggle').addEventListener('click',()=>openPanel('inspector'));
$('runs').addEventListener('change',()=>selectRun($('runs').value));$('frame').addEventListener('input',()=>{pause();index=Number($('frame').value);draw();});$('play').addEventListener('click',togglePlay);$('previous').addEventListener('click',()=>step(-1));$('next').addEventListener('click',()=>step(1));$('speed').addEventListener('change',()=>{if(timer){pause();togglePlay();}});
$('camera').addEventListener('change',()=>viewport?.setCamera($('camera').value));$('fit').addEventListener('click',()=>viewport?.fit());$('zoom-in').addEventListener('click',()=>viewport?.zoom(1.3));$('zoom-out').addEventListener('click',()=>viewport?.zoom(1/1.3));$('pan').addEventListener('click',()=>{const on=$('pan').getAttribute('aria-pressed')!=='true';$('pan').setAttribute('aria-pressed',String(on));viewport.setTool(on?'hand':'orbit');$('view-note').textContent=on?'单指 / 左键拖动平移 · 双指 / 滚轮缩放':'单指 / 左键拖动旋转 · 双指 / 滚轮缩放';});
$('field').addEventListener('change',()=>{sliceOptions();draw();});$('normal').addEventListener('change',()=>{sliceOptions();draw();});$('slice').addEventListener('change',draw);$('cells').addEventListener('change',()=>selectCell($('cells').value));$('objects').addEventListener('change',()=>selectObject($('objects').value));$('snapshot-details').addEventListener('toggle',()=>{if(run)details();});$('provenance-details').addEventListener('toggle',()=>{if(run)details();});
document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});document.addEventListener('keydown',event=>{if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName)||event.ctrlKey||event.metaKey||event.altKey)return;if(event.key==='ArrowLeft'){event.preventDefault();step(-1);}if(event.key==='ArrowRight'){event.preventDefault();step(1);}if(event.code==='Space'&&run&&document.activeElement?.tagName!=='BUTTON'){event.preventDefault();togglePlay();}if(event.key.toLowerCase()==='f')viewport?.fit();});
async function fetchRecord(path,expectedBytes){
  const response=await fetch(path);if(!response.ok)throw Error('记录不可读');const total=Number(response.headers.get('content-length'))||expectedBytes;
  const description=total>20_000_000?`完整记录约 ${(total/1e6).toFixed(1)} MB，正在读取全部保存帧；不调用计算内核。`:'正在读取保存帧，不调用计算内核。';loading('正在下载完整历史记录',description,0);
  let text='';if(response.body){const reader=response.body.getReader(),decoder=new TextDecoder();let received=0,last=0;while(true){const {done,value}=await reader.read();if(done)break;received+=value.length;text+=decoder.decode(value,{stream:true});if(performance.now()-last>100){loading(`正在读取 ${(received/1e6).toFixed(1)}${total?' / '+(total/1e6).toFixed(1):''} MB`,description,total?received/total:null);last=performance.now();}}text+=decoder.decode();}else text=await response.text();
  performance.mark('wiki-record-received');
  loading('正在解析已保存的时间帧','完整记录已下载。解析期间不运行或改写科学模型。',1);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const parsed=JSON.parse(text);performance.mark('wiki-record-parsed');performance.measure('wiki-json-parse','wiki-record-received','wiki-record-parsed');return parsed;
}
try{
  const response=await fetch('./examples.json');if(!response.ok)throw Error('示例清单不可读');const examples=await response.json();if(examples.mode!=='read-only-past-computation'||examples.solver_included!==false)throw Error('未知 Wiki 模式');
  if(examples.download_url){const url=new URL(examples.download_url);if(url.protocol!=='https:')throw Error('下载地址无效');$('download').href=url.href;$('download').textContent='下载完整版 ↗';}
  if(!['design.friskoli','run.result.json'].includes(examples.original_file))throw Error('原始文件路径无效');$('original').href=examples.original_file;$('report').hidden=!examples.has_design_report;
  for(const item of examples.examples)$('examples').add(new Option(item.name,item.record));$('examples').disabled=examples.examples.length<2;$('examples').title=examples.examples.length<2?'此发布目录包含一个示例':'选择保存示例';
  loading('正在准备三维回放','加载与完整版 CAD 共用的只读三维显示组件。');
  const [scene,adapters,placeables,displayResponse]=await Promise.all([import('./scene3d.mjs'),import('./catalog.mjs'),import('./placeables.mjs'),fetch('./viewer-catalog.json')]);if(!displayResponse.ok)throw Error('显示适配器清单不可读');catalog=await displayResponse.json();registerCatalog=adapters.registerCatalog;environmentObjects=placeables.environmentObjects;
  viewport=new scene.SpatialViewport($('scene'),$('annotations'),{selectCell,selectEnvironment:selectObject,markerLabel:()=> '细胞位置标记最小 4 px；几何按真实比例显示'});viewport.setMode('results');
  async function load(){pause();for(const id of controls)$(id).disabled=true;const name=$('examples').value;if(name!=='record.json')throw Error('未知静态记录路径');data=await fetchRecord('./'+name,examples.record_bytes);if(!Array.isArray(data.runs)||!data.runs.length)throw Error('没有可播放的完整运行');$('runs').replaceChildren();data.runs.forEach((r,i)=>$('runs').add(new Option(`${r.design_ref?.candidate_name??r.design_ref?.candidate_id??'独立科学运行'} · seed ${r.submission.execution.seed} · ${r.id??r.runId??r.localId}`,String(i))));let saved;try{saved=JSON.parse(localStorage.getItem('friskoli-wiki-position'));}catch{}const valid=saved?.record===(data.record_id??data.design?.id)&&Number.isInteger(saved.index)&&saved.index>=0&&data.runs[Number(saved.run)];selectRun(valid?saved.run:'0',valid?saved.index:0);}
  $('examples').addEventListener('change',()=>load().catch(fail));await load();
}catch(error){fail(error);}
function fail(error){pause();loading('无法读取回放',`${error.message}。请通过静态 HTTP 服务打开此目录。`);$('load-progress').hidden=true;setStatus(`读取失败：${error.message}`);}
