import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as THREE from '../src/friskoli_cad/web/vendor/three/build/three.module.js';
import {nextHit} from '../src/friskoli_cad/web/catalog.mjs';
import {executionStepLimit} from '../src/friskoli_cad/web/task-store.mjs';

// Load the actual viewport methods without its browser-only controls imports or
// WebGL constructor. Geometry and picking below use the shipped Three.js runtime.
const source = readFileSync(new URL('../src/friskoli_cad/web/scene3d.mjs', import.meta.url), 'utf8');
const {SpatialViewport, canTransformBlock} = new Function('THREE', 'nextHit',
  source.replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '') +
  '\nreturn {SpatialViewport, canTransformBlock};')(THREE, nextHit);
const block = (id, extra = {}) => ({id, center:[50,50,5], size:[100,100,10], dirty:false, ...extra});
function viewport() {
  const v = Object.create(SpatialViewport.prototype);
  Object.assign(v, {blocks:new THREE.Group(), objects:new THREE.Group(), measureGroup:new THREE.Group(),
    mode:'space', tool:'select', domain:{geometry:'volume'}, objectHits:[], meshes:[], geometryCache:new Map(),
    canvas:{style:{}}, orbit:{mouseButtons:{}, touches:{}}, request(){}, pointFromEvent(){},
    raycaster:new THREE.Raycaster(new THREE.Vector3(50,50,100), new THREE.Vector3(0,0,-1))});
  v.events = [];
  v.callbacks = {selectBlock:id=>v.events.push(['block',id]), selectEnvironment:id=>v.events.push(['object',id]),
    selectCell:id=>v.events.push(['cell',id]), transformBlock:(...args)=>v.events.push(['transform',...args])};
  v.transform = {object:null, detach(){this.object=null;}, attach(mesh){this.object=mesh;}, setMode(mode){this.mode=mode;}};
  v.load = (blocks, selected=null) => {v.setBlocks(blocks, selected); v.blocks.updateMatrixWorld(true);};
  return v;
}

test('distributed full-domain populations do not capture blank space or a new population, including right click', () => {
  const v=viewport();
  v.load([block('distributed')]);v.pick({});
  assert.deepEqual(v.events,[]);assert.equal(v.hitBlock({}),null);
  v.load([block('distributed'),block('new',{dirty:true,size:[2,2,2]})]);v.pick({});
  assert.deepEqual(v.events,[['block','new']]);assert.equal(v.hitBlock({}),'new');
  // Explicit list selection shows the clean outline without intercepting picks.
  v.load([block('distributed'),block('new',{dirty:true,size:[2,2,2]})],'distributed');
  assert.equal(v.blocks.children.length,2);
  assert.equal(v.blocks.children[0].children[0].material.opacity,.9);
  assert.equal(v.hitBlock({}),'new');
});

test('a clean population selected from the list can attach and commit a gizmo after switching tools', () => {
  const v=viewport();v.load([block('distributed')],'distributed');
  assert.equal(v.transform.object,null);
  for(const tool of ['move','rotate','scale']) {
    v.setTool(tool);assert.equal(v.transform.object.userData.blockId,'distributed');
    assert.equal(v.hitBlock({}),null);
  }
  v.transform.object.position.x=60;v.commitTransform();
  assert.deepEqual(v.events[0],['transform','distributed',[60,50,5],[100,100,10],[0,0,0]]);
});

test('locked and hidden blocks cannot transform; Results neither picks blocks nor commits transforms', () => {
  const v=viewport();v.tool='move';
  for(const flags of [{locked:true},{hidden:true}]) {
    v.load([block('blocked',{dirty:true,...flags})],'blocked');
    assert.equal(v.transform.object,null);
    // A delayed transform completion must not mutate a newly locked/hidden block.
    v.transform.object=new THREE.Mesh();v.transform.object.userData.blockId='blocked';
    v.commitTransform();assert.deepEqual(v.events,[]);
    if(flags.hidden)assert.equal(v.hitBlock({}),null);
  }
  v.load([block('draft',{dirty:true})],'draft');
  const mesh=v.transform.object;v.setMode('results');
  assert.equal(v.transform.object,null);assert.equal(v.hitBlock({}),null);
  v.pick({});assert.deepEqual(v.events,[]);
  v.transform.object=mesh;v.commitTransform();assert.deepEqual(v.events,[]);
});

test('environment geometry remains selectable inside a distributed full-domain population', () => {
  const v=viewport();v.load([block('distributed')],'distributed');
  v.setObjects([{id:'obstacle',kind:'obstacle_box',lower:[49,49,4],upper:[51,51,6]}],null);
  v.objects.updateMatrixWorld(true);v.pick({});
  assert.deepEqual(v.events,[['object','obstacle']]);assert.equal(v.hitBlock({}),null);
});

// Exercise the actual app handlers with DOM/render dependencies supplied by a
// small harness, including renderScene's tool-state refresh. No source assertions.
const app=readFileSync(new URL('../src/friskoli_cad/web/app.mjs',import.meta.url),'utf8');
const functions=(first,next)=>app.slice(app.indexOf(`function ${first}(`),app.indexOf(`function ${next}(`));
function editor() {
  const state={project:{domain:{counts_xyz:[10,10,1],spacing_um_xyz:[1,1,1]}},blocks:[],
    selectedBlock:null,selectedEnvironment:null,view:'space',tool:'select',ranges:{},objects:new Map()};
  const elements=new Map();const $=id=>{if(!elements.has(id))elements.set(id,{classList:{toggle(){}},disabled:false});return elements.get(id);};
  const context=`
    const TOOLS={select:'select-tool',move:'move-tool',rotate:'rotate-tool',scale:'scale-tool'};
    const renderLeft=()=>{},renderInspector=()=>{},closeDocks=()=>{},renderData=()=>{},renderFieldControls=()=>{};
    const currentSnapshot=()=>null,visibleEnvironment=()=>[],fmt=String,t=String,status=()=>{};
    const setView=view=>{state.view=view;};
    ${functions('selectedBlockTransformable','kv')}
    ${functions('renderScene','renderTimeline')}
    ${functions('selectBlock','renderEnvironmentRows')}
    return {selectBlock,selectEnvironment,useTool,renderScene};`;
  return {state,$,...new Function('state','$','canTransformBlock','objectForBlock','viewport',context)
    (state,$,canTransformBlock,b=>b?.registered!==false,null)};
}

test('list selection immediately enables tools and environment selection disables and resets them', () => {
  const e=editor();e.state.blocks=[block('clean')];e.renderScene();
  assert.equal(e.$('move-tool').disabled,true);
  e.selectBlock('clean');assert.equal(e.$('move-tool').disabled,false);
  e.useTool('move');assert.equal(e.state.tool,'move');
  e.selectEnvironment('obstacle');assert.equal(e.$('move-tool').disabled,true);
  assert.equal(e.state.tool,'select');
  e.useTool('move');assert.equal(e.state.tool,'select');
});

test('tool commands cannot bypass locked, hidden, unsupported or Results selection', () => {
  for(const flags of [{locked:true},{hidden:true},{registered:false},{}]) {
    const e=editor();e.state.blocks=[block('clean',flags)];e.selectBlock('clean');
    if(!Object.keys(flags).length)e.state.view='results';
    e.renderScene();
    for(const tool of ['move','rotate','scale']) {
      assert.equal(e.$(`${tool}-tool`).disabled,true);
      e.useTool(tool);assert.equal(e.state.tool,'select');
    }
    if(!Object.keys(flags).length)assert.equal(e.state.view,'results');
  }
});

test('step limits prefer matched task capability, then top-level limit, then conservative fallback', () => {
  const project={execution_profile:'chemotaxis-spatial-v1',project_version:'0.5.0'};
  const task={task_contract_version:'0.4.0',execution:{semantics:project.execution_profile},limits:{steps:2345}};
  const caps={task_profiles:{[project.execution_profile]:task},limits:{steps:1900}};
  assert.equal(executionStepLimit(caps,project),2345);
  assert.equal(executionStepLimit(caps,{...project,project_version:'0.4.0'}),1900);
  assert.equal(executionStepLimit(caps,{execution_profile:'unknown'}),1900);
  assert.equal(executionStepLimit({limits:{steps:1900}},{}),1900);
  assert.equal(executionStepLimit(null,project),100);
  for(const steps of [0,-1,NaN,Infinity,'10000',1.5]) {
    assert.equal(executionStepLimit({limits:{steps}},project),100);
    assert.equal(executionStepLimit({...caps,task_profiles:{[project.execution_profile]:{...task,limits:{steps}}}},project),1900);
  }
});
