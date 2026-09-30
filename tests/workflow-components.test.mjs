import assert from 'node:assert/strict';
import {test} from 'node:test';
import {moduleName, nodeGeometry, nodeStatus, graphBounds, createPortRow, createNodeHeader} from '../src/friskoli_cad/web/workflow-components.mjs';
import {GraphEditor, autoLayout} from '../src/friskoli_cad/web/workflow.mjs';
import {resolveGraph} from '../src/friskoli_cad/web/catalog.mjs';

const manifest = {id:'growth.linear_elongation',version:'1.0.0',scope:'population',phase:2,
  inputs:{length:{quantity:'length',unit:'um'}}, outputs:{length:{quantity:'length',unit:'um'},diameter:{quantity:'diameter',unit:'um'}}, parameters:{rate:{unit:'um/s'}}};
const node = (id='a', data=manifest) => ({id,module_id:data.id,module_version:data.version,owner:{kind:'population',id:'group_1'},parameters:{rate:{value:0.5}},manifest:data});

test('friendly labels support the installed bilingual catalog, localized names and legacy IDs', () => {
  const data={...manifest,declaration:{label:'胶囊几何读取 / Capsule readout'}};
  assert.equal(moduleName(data,'zh-CN'),'胶囊几何读取'); assert.equal(moduleName(data,'en'),'Capsule readout');
  assert.equal(moduleName({...manifest,declaration:{label:manifest.id}}),'Linear elongation');
  assert.equal(moduleName({...manifest,declaration:{name:{en:'Long name', 'zh-CN':'长名称'}}},'zh-CN'),'长名称');
  assert.equal(moduleName({...manifest,declaration:{label:'a'.repeat(300)}}),'a'.repeat(300));
});

test('composition allocates separate space for ports, parameters and math; coarse ports cannot overlap', () => {
  const data={...manifest, declaration:{mathematics:{equations:[{latex:'x=y'}]}}};
  const full=nodeGeometry(data,false,true), collapsed=nodeGeometry(data,true,true);
  assert.ok(full.row>=44); assert.ok(full.footer>=full.head + 2*44);
  assert.ok(full.height>=full.footer + 26 + 80); assert.ok(collapsed.height<full.height);
  assert.equal(collapsed.math,false); assert.equal(collapsed.parameters,0);
});

test('missing connection status and unavailable module status never claim a runnable or calibrated state', () => {
  assert.deepEqual(nodeStatus(node(),[{node:'a',port:'length'}]),{kind:'error',text:'Missing inputs · 1'});
  assert.match(nodeStatus(node('a',{...manifest,unavailable:true}),[{node:'a',port:'length'}]).text,/unavailable.*blocked/);
  assert.equal(nodeStatus(node()).text,'Module');
});

test('unknown modules retain edge anchors and original graph data', () => {
  const graph={protocol_version:'0.1.0',nodes:[node('missing')],edges:[{id:'e',from:{node:'missing',port:'result'},to:{node:'missing',port:'input'},timing:'previous_step'}]};
  const before=structuredClone(graph), resolved=resolveGraph(graph,new Map(),{allowUnknown:true});
  const editor=Object.create(GraphEditor.prototype); Object.assign(editor,{resolved,layout:{missing:{x:70,y:70}},coarse:false});
  assert.ok(editor.portPoint('missing','outputs','result')); assert.ok(editor.portPoint('missing','inputs','input'));
  assert.equal(editor.portPoint('missing','inputs','absent'),null); assert.deepEqual(graph,before);
});

test('layout keeps saved positions and fits actual content rather than a fixed board size', () => {
  const graph={nodes:[node('a'),node('b')],edges:[]}, modules=new Map([[`${manifest.id}@1.0.0`,manifest]]);
  const saved={a:{x:70,y:70,collapsed:false}}, layout=autoLayout(graph,modules,saved);
  assert.deepEqual(layout.a,saved.a); assert.ok(layout.b.y>=layout.a.y+nodeGeometry(manifest,false,true).height);
  const bounds=graphBounds(graph.nodes,layout,true); assert.equal(bounds.width,250); assert.equal(bounds.x,70);
  const editor=Object.create(GraphEditor.prototype); Object.assign(editor,{board:{},resolved:{nodes:[node()]},layout:{a:{x:70,y:70}},coarse:false,
    container:{clientWidth:400,clientHeight:400},resizeBoard(){}});
  editor.fit(); assert.equal(editor.zoom,1); assert.equal(editor.container.scrollLeft,46);
});

test('zoom preserves the graph point underneath the cursor', () => {
  const editor=Object.create(GraphEditor.prototype);
  Object.assign(editor,{board:{},zoom:1,container:{scrollLeft:100,scrollTop:150,getBoundingClientRect:()=>({left:10,top:20,width:400,height:300})},applyZoom(){}});
  editor.zoomBy(1.5,[110,120]);
  assert.equal((editor.container.scrollLeft+100)/editor.zoom,200);
  assert.equal((editor.container.scrollTop+100)/editor.zoom,250);
});

// Small DOM boundary double: verifies emitted text/attributes and user callbacks without a browser dependency.
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.style={};this.dataset={};this.attributes={};this.events={};}
  append(...items){this.children.push(...items);}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(key,fn){this.events[key]=fn;}
}
test('identifiers and hostile labels remain plain text; global scalar ports expose full units', () => {
  const previous=globalThis.document; globalThis.document={createElement:tag=>new Element(tag)};
  try {
    const hostile='<img src=x onerror=alert(1)>', item=node(hostile,{...manifest,declaration:{label:hostile}});
    const header=createNodeHeader(item,{status:nodeStatus(item),language:'en'});
    assert.equal(header.children[0].textContent,hostile);
    const panel=header.children[3].children[1]; assert.equal(panel.children[0].children[1].value,hostile);
    assert.equal(panel.children[1].children[1].value,`${manifest.id}@1.0.0`);
    const {dot}=createPortRow(item,'inputs','bulk',{shape:'global.scalar',quantity:'glucose_concentration',unit:'mM'},
      {index:0,geometry:nodeGeometry(manifest),language:'en'});
    assert.match(dot.attributes['aria-label'],/glucose_concentration \[mM\]/);
    assert.equal(dot.dataset.node,hostile);
  } finally {globalThis.document=previous;}
});

test('pointer cancellation restores layout and never records an undo move', () => {
  const editor=Object.create(GraphEditor.prototype), events={}, calls=[];
  const card={style:{},classList:{add(){},remove(){}},setPointerCapture(){},addEventListener(name,fn){events[name]=fn;},removeEventListener(name){delete events[name];}};
  Object.assign(editor,{layout:{a:{x:70,y:70}},zoom:1,drawEdges(){},resizeBoard(){},callbacks:{move(...args){calls.push(args);},select(...args){calls.push(args);}}});
  editor.startDrag({pointerId:1,clientX:0,clientY:0},card,'a');
  events.pointermove({pointerId:1,clientX:100,clientY:100}); assert.notEqual(editor.layout.a.x,70);
  editor.cancelDrag(); assert.deepEqual(editor.layout.a,{x:70,y:70}); assert.equal(calls.length,0);
});

test('two-finger transition cancels a draft drag/wire, zooms, then resumes one-finger panning', () => {
  const events={}, zooms=[], cancelled=[];
  const container={scrollLeft:100,scrollTop:100,addEventListener(name,fn){events[name]=fn;},setPointerCapture(){}};
  const editor=new GraphEditor(container,{}); editor.zoomBy=(...args)=>zooms.push(args);
  editor.cancelDrag=()=>cancelled.push('drag'); editor.cancelWire=()=>cancelled.push('wire');
  const target={closest:selector=>selector==='.graph-node,.edge-hit' ? {} : null};
  const pointer=(pointerId,clientX,clientY)=>({pointerId,clientX,clientY,button:0,target});
  events.pointerdown(pointer(1,20,20)); events.pointerdown(pointer(2,120,20));
  assert.deepEqual(cancelled,['drag','wire']);
  events.pointermove(pointer(2,140,20)); assert.equal(zooms[0][0],1.2);
  events.pointerup(pointer(2,140,20)); events.pointermove(pointer(1,30,40));
  assert.equal(container.scrollLeft,90); assert.equal(container.scrollTop,80);
  events.pointercancel(pointer(1,30,40)); assert.equal(editor.pointers.size,0); assert.equal(editor.panStart,null);
});
