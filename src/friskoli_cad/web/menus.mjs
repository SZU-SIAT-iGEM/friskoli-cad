import {currentLanguage, t} from './i18n.mjs';

// Menus only dispatch existing commands. The scientific editor owns all state.
const $ = id => document.getElementById(id);
const labels = {
  en: {file:'File',edit:'Edit',view:'View',help:'Help',shortcuts:'Keyboard & navigation',move:'Move',rotate:'Rotate',scale:'Scale',noUndo:'No edits to undo',noRedo:'No edits to redo',noResult:'Run a project before exporting results',select:'Select an unlocked, transformable object first',noPopulation:'Open Space and select an available population module',noSpatial:'Available in Space or Results',noFit:'No canvas in Design',library:'Library',close:'Close help',heading:'Workspace controls',keys:[['V / B','Select / place population'],['W / E / R','Move / rotate / scale selection'],['M','Measure two surface points'],['H / O','Pan / orbit camera'],['F','Fit the current view'],['G','Toggle the spatial grid'],['Ctrl / ⌘ + Z','Undo'],['Ctrl / ⌘ + Shift + Z','Redo'],['Ctrl / ⌘ + S','Save workspace'],['Ctrl / ⌘ + Enter','Run when checks allow'],['← / →','Previous / next replay frame']],navigation:'Space: drag to orbit; right-drag to pan; scroll or pinch to zoom. Workflow: drag cards to move them, drag the background to pan, and connect an output port to an input port. Arrange graph reorganizes the visible workflow; Undo restores the previous positions. Parameters and evidence are available in the right panel.'},
  'zh-CN': {file:'文件',edit:'编辑',view:'视图',help:'帮助',shortcuts:'快捷键与导航',move:'移动',rotate:'旋转',scale:'缩放',noUndo:'没有可撤销的修改',noRedo:'没有可重做的修改',noResult:'先运行项目，再导出结果',select:'请先选择未锁定且支持变换的对象',noPopulation:'请进入空间并选择可用的菌群模块',noSpatial:'在空间或结果页面中使用',noFit:'设计页面没有可适配的画布',library:'库',close:'关闭帮助',heading:'工作区操作',keys:[['V / B','选择 / 放置菌群'],['W / E / R','移动 / 旋转 / 缩放所选对象'],['M','测量两个表面点'],['H / O','平移 / 旋转相机'],['F','适合当前视图'],['G','切换空间网格'],['Ctrl / ⌘ + Z','撤销'],['Ctrl / ⌘ + Shift + Z','重做'],['Ctrl / ⌘ + S','保存工作区'],['Ctrl / ⌘ + Enter','检查允许时运行'],['← / →','上一帧 / 下一帧']],navigation:'空间：拖动旋转视角，右键拖动平移，滚轮或双指缩放。工作流：拖动卡片调整位置，拖动背景平移，从输出端口连接到输入端口。整理图形可重新排列工作流，撤销可恢复原位置。右侧面板提供参数与证据。'}
};
const words = () => labels[currentLanguage()] ?? labels.en;
const triggers = [...document.querySelectorAll('[data-menu-trigger]')];
let openTrigger = null;
function closeMenu(restoreFocus = false) {
  if (!openTrigger) return;
  const trigger = openTrigger;
  $(`menu-${trigger.dataset.menuTrigger}`).hidden = true;
  trigger.setAttribute('aria-expanded','false'); openTrigger = null;
  if (restoreFocus) trigger.focus();
}
function refreshCommands() {
  const w = words();
  const reasons = {'undo-button':w.noUndo,'redo-button':w.noRedo,'export-button':w.noResult};
  for (const [id,reason] of Object.entries(reasons)) {
    const target = $(id);
    target.title = target.disabled ? reason : t(id.split('-')[0]);
  }
  for (const button of document.querySelectorAll('[data-command]')) {
    const target = $(button.dataset.command);
    const disabled = !target || target.disabled;
    if (button.disabled !== disabled) button.disabled = disabled;
    button.title = target?.title ?? '';
  }
  $('population-tool').title = $('population-tool').disabled ? w.noPopulation : (currentLanguage()==='zh-CN'?'放置菌群':'Place population');
  for (const name of ['move','rotate','scale']) {
    const button = $(`${name}-tool`);
    button.title = button.disabled ? w.select : `${w[name]} (${({move:'W',rotate:'E',scale:'R'})[name]})`;
  }
}
function openMenu(trigger, focus = false) {
  closeMenu(); refreshCommands(); openTrigger = trigger;
  const menu = $(`menu-${trigger.dataset.menuTrigger}`); menu.hidden = false;
  trigger.setAttribute('aria-expanded','true');
  // A fixed popup remains inside the viewport even in phone landscape.
  const rect = trigger.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(rect.left,innerWidth-menu.offsetWidth-8))}px`;
  menu.style.top = `${rect.bottom+2}px`;
  if (focus) menu.querySelector('button:not(:disabled)')?.focus();
}
for (const trigger of triggers) {
  trigger.addEventListener('click', () => openTrigger === trigger ? closeMenu() : openMenu(trigger));
  trigger.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { event.preventDefault(); openMenu(trigger,true); }
  });
}
for (const menu of document.querySelectorAll('.application-dropdown')) {
  menu.addEventListener('click', event => {
    const command = event.target.closest('button');
    if (!command || command.disabled) return;
    closeMenu();
    if (command.dataset.command) $(command.dataset.command)?.click();
  });
}
document.addEventListener('pointerdown', event => {if (!event.target.closest('.application-menu')) closeMenu();});
document.addEventListener('keydown', event => {
  if (!openTrigger) return;
  if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); closeMenu(true); return; }
  if (event.key === 'Tab') { closeMenu(); return; }
  if (['ArrowDown','ArrowUp'].includes(event.key)) {
    event.preventDefault();
    const buttons = [...$(`menu-${openTrigger.dataset.menuTrigger}`).querySelectorAll('button:not(:disabled)')];
    const index = buttons.indexOf(document.activeElement);
    buttons[(index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length)%buttons.length]?.focus();
  }
  if (['ArrowRight','ArrowLeft'].includes(event.key)) {
    event.preventDefault(); const index = triggers.indexOf(openTrigger);
    openMenu(triggers[(index + (event.key === 'ArrowRight' ? 1 : -1)+triggers.length)%triggers.length],true);
  }
},true);
window.addEventListener('resize', () => closeMenu());
const help = document.createElement('dialog');help.className='shortcut-dialog';help.id='shortcut-dialog';help.setAttribute('aria-labelledby','shortcut-heading');document.body.append(help);
function localize() {
  const w=words();
  for (const trigger of triggers) trigger.textContent=w[trigger.dataset.menuTrigger];
  $('shortcut-help').textContent=w.shortcuts;
  for (const name of ['move','rotate','scale']) $(`${name}-tool`).textContent=w[name];
  const library=document.querySelector('[data-left="modules"]');library.removeAttribute('data-i18n');library.textContent=w.library;
  help.replaceChildren();
  const head=document.createElement('div');head.className='dialog-head';
  const heading=document.createElement('h2');heading.id='shortcut-heading';heading.textContent=w.heading;
  const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label',w.close);close.addEventListener('click',()=>help.close());head.append(heading,close);
  const list=document.createElement('dl');list.className='shortcut-list';
  for(const [key,description] of w.keys){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=key;dd.textContent=description;list.append(dt,dd);}
  const note=document.createElement('p');note.textContent=w.navigation;help.append(head,list,note);refreshCommands();
}
$('shortcut-help').addEventListener('click',()=>help.showModal());
help.addEventListener('close',()=>triggers.find(item=>item.dataset.menuTrigger==='help').focus());
const syncView=()=>{
  const view=document.querySelector('[data-view].active')?.dataset.view;
  $('transform-tools').hidden=view!=='space';
  for (const id of ['fit-tool','top-tool','front-tool','right-tool']) $(id).hidden=!['space','results'].includes(view);
  const fit=document.querySelector('[data-command="fit-button"]');
  fit.hidden=view==='design';
  refreshCommands();
};
new MutationObserver(syncView).observe(document.querySelector('.workspace-tabs'),{subtree:true,attributes:true,attributeFilter:['class']});
new MutationObserver(refreshCommands).observe(document.querySelector('.app'),{subtree:true,attributes:true,attributeFilter:['disabled']});
new MutationObserver(localize).observe(document.documentElement,{attributes:true,attributeFilter:['lang']});
// app.mjs decorates tool icons during initialization; text transform labels follow it.
window.addEventListener('load',localize,{once:true});localize();syncView();
