const messages = {
  en: {
    open:'Open',save:'Save',settings:'Settings',space:'Space',workflow:'Workflow',results:'Results',
    step:'Step',steps:'Steps',run:'Run',objects:'Objects',modules:'Modules',properties:'Properties',
    simulation:'Simulation',perspective:'Perspective',orthographic:'Orthographic',top:'Top · XY',
    front:'Front · XZ',right:'Right · YZ',noField:'No field',fit:'Fit',timeline:'Timeline',
    language:'Interface language',domain:'Domain',population:'Population',populations:'Populations',
    cells:'Cells',select:'Select',placePopulation:'Place population',scatter:'Scatter population',
    scatterHint:'Right-click the volume to scatter cells.',name:'Name',count:'Count',center:'Center',size:'Volume size',
    length:'Capsule length',diameter:'Diameter',seed:'Seed',position:'Position',orientation:'Orientation XYZW',
    channels:'Channels',geometry:'Geometry',unknown:'Unknown',registered:'Registered',active:'Computed',
    node:'Node',scope:'Scope',phase:'Phase',parameters:'Parameters',inputs:'Inputs',outputs:'Outputs',
    module:'Module',description:'Description',source:'Source',target:'Target',events:'Events',
    noEvents:'No events in this frame',noSelection:'Select an object in the viewport or tree.',
    noCell:'This cell is absent from the current frame.',pending:'Scatter pending',
    ready:'Ready',running:'Running simulation…',runReady:'{count} frames · {cells} cells at current frame',
    runFailed:'Run failed: {message}',loadFailed:'Could not load project: {message}',
    invalidControls:'Enter a positive step and 1–100 integer steps.',
    blockPlaced:'Population volume placed. Set its count and scatter it.',
    blockUpdated:'Population volume changed. Scatter to apply it.',
    scattered:'Scattered {count} cells in {id}',
    saved:'Workspace saved',opened:'Workspace opened',
    frame:'Frame',time:'Time',group:'Group',species:'Species',grid:'Grid',extent:'Extent',
    fieldPlane:'Field plane',noActiveField:'No computed concentration field',
    division:'Division',birth:'Birth',death:'Death',
    localRun:'Local run',viewError:'3D viewport unavailable: {message}',
    moduleMissing:'This graph needs a module not present in the backend catalog.',
    compiled:'Backend modules',connected:'Connected',notConnected:'Not connected',
    inspect:'Inspect',history:'Visible in {count} frames',
  },
  'zh-CN': {
    open:'打开',save:'保存',settings:'设置',space:'空间',workflow:'工作流',results:'结果',
    step:'步长',steps:'步数',run:'运行',objects:'对象',modules:'模块',properties:'属性',
    simulation:'仿真',perspective:'透视',orthographic:'正交',top:'顶视 · XY',
    front:'前视 · XZ',right:'右视 · YZ',noField:'无浓度场',fit:'适应',timeline:'时间轴',
    language:'界面语言',domain:'场地',population:'菌群',populations:'菌群',
    cells:'菌体',select:'选择',placePopulation:'放置菌群',scatter:'散布菌体',
    scatterHint:'右键点击体积块可散布菌体。',name:'名称',count:'数量',center:'中心',size:'体积尺寸',
    length:'胶囊总长',diameter:'直径',seed:'随机种子',position:'位置',orientation:'朝向 XYZW',
    channels:'通道',geometry:'几何',unknown:'未知',registered:'已登记',active:'参与计算',
    node:'节点',scope:'作用范围',phase:'执行相位',parameters:'参数',inputs:'输入',outputs:'输出',
    module:'模块',description:'说明',source:'来源',target:'去向',events:'事件',
    noEvents:'本帧没有事件',noSelection:'从画面或对象树选择对象。',
    noCell:'这个菌体不在当前帧。',pending:'等待散布',
    ready:'就绪',running:'正在运行仿真…',runReady:'{count} 帧 · 当前帧 {cells} 个菌体',
    runFailed:'运行失败：{message}',loadFailed:'项目加载失败：{message}',
    invalidControls:'请输入正的步长和 1–100 的整数步数。',
    blockPlaced:'已放置菌群体积块。设置数量后散布。',
    blockUpdated:'菌群体积块已修改，散布后生效。',
    scattered:'已在 {id} 散布 {count} 个菌体',
    saved:'工作区已保存',opened:'工作区已打开',
    frame:'帧',time:'时间',group:'菌体组',species:'物质',grid:'格点',extent:'范围',
    fieldPlane:'浓度平面',noActiveField:'没有参与计算的浓度场',
    division:'分裂',birth:'出生',death:'死亡',
    localRun:'本地运行',viewError:'三维视口不可用：{message}',
    moduleMissing:'行为图使用了后端清单中没有的模块。',
    compiled:'后端模块',connected:'已连接',notConnected:'未连接',
    inspect:'检查',history:'出现在 {count} 帧中',
  },
};

let language = 'en';
try { if (localStorage.getItem('friskoli.language') === 'zh-CN') language = 'zh-CN'; } catch { /* storage unavailable */ }

export function currentLanguage() { return language; }
export function t(key, values = {}) {
  const template = messages[language][key] ?? messages.en[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? ''));
}
export function applyLanguage() {
  document.documentElement.lang = language;
  document.title = `Friskoli-CAD · ${t('simulation')}`;
  for (const element of document.querySelectorAll('[data-i18n]')) element.textContent = t(element.dataset.i18n);
}
export function setLanguage(next) {
  if (!Object.hasOwn(messages, next)) throw new Error('Unsupported language');
  language = next;
  try { localStorage.setItem('friskoli.language', next); } catch { /* storage unavailable */ }
  applyLanguage();
}
