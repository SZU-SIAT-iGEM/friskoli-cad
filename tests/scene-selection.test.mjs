import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as THREE from '../src/friskoli_cad/web/vendor/three/build/three.module.js';
import {nextHit} from '../src/friskoli_cad/web/catalog.mjs';
import {executionStepLimit} from '../src/friskoli_cad/web/task-store.mjs';
import {environmentTransformGeometry,isEnvironmentObject} from '../src/friskoli_cad/web/placeables.mjs';

// Load the actual viewport methods without its browser-only controls imports or
// WebGL constructor. Geometry and picking below use the shipped Three.js runtime.
const source = readFileSync(new URL('../src/friskoli_cad/web/scene3d.mjs', import.meta.url), 'utf8');
const {SpatialViewport, canTransformBlock, canTransformEnvironment} = new Function('THREE', 'nextHit', 'environmentTransformGeometry', 'isEnvironmentObject',
  source.replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '') +
  '\nreturn {SpatialViewport, canTransformBlock, canTransformEnvironment};')(THREE, nextHit, environmentTransformGeometry, isEnvironmentObject);
const block = (id, extra = {}) => ({id, center:[50,50,5], size:[100,100,10], dirty:false, ...extra});
function viewport() {
  const v = Object.create(SpatialViewport.prototype);
  Object.assign(v, {blocks:new THREE.Group(), objects:new THREE.Group(), measureGroup:new THREE.Group(),
    mode:'space', tool:'select', domain:{geometry:'volume',counts_xyz:[100,100,10],spacing_um_xyz:[1,1,1]}, objectHits:[], meshes:[], geometryCache:new Map(),
    canvas:{style:{}}, orbit:{mouseButtons:{}, touches:{}}, request(){}, pointFromEvent(){},
    raycaster:new THREE.Raycaster(new THREE.Vector3(50,50,100), new THREE.Vector3(0,0,-1))});
  v.events = [];
  v.callbacks = {selectBlock:id=>v.events.push(['block',id]), selectEnvironment:id=>v.events.push(['object',id]),
    selectCell:id=>v.events.push(['cell',id]), transformBlock:(...args)=>v.events.push(['transform',...args]),
    transformEnvironment:(...args)=>v.events.push(['environment-transform',...args])};
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
    const currentSnapshot=()=>null,visibleEnvironment=()=>state.environment??[],fmt=String,t=String,status=()=>{};
    const setView=view=>{state.view=view;};
    ${functions('selectedBlockTransformable','kv')}
    ${functions('renderScene','renderTimeline')}
    ${functions('selectBlock','renderEnvironmentRows')}
    return {selectBlock,selectEnvironment,useTool,renderScene};`;
  return {state,$,...new Function('state','$','canTransformBlock','canTransformEnvironment','objectForBlock','viewport',context)
    (state,$,canTransformBlock,canTransformEnvironment,b=>b?.registered!==false,null)};
}

test('list selection immediately enables tools and unsupported selection disables and resets them', () => {
  const e=editor();e.state.blocks=[block('clean')];e.renderScene();
  assert.equal(e.$('move-tool').disabled,true);
  e.selectBlock('clean');assert.equal(e.$('move-tool').disabled,false);
  e.useTool('move');assert.equal(e.state.tool,'move');
  e.selectEnvironment('obstacle');assert.equal(e.$('move-tool').disabled,true);
  assert.equal(e.state.tool,'select');
  e.useTool('move');assert.equal(e.state.tool,'select');
});

const environment = (kind='obstacle_box') => ({id:kind,kind,lower:[10,10,0],upper:[14,14,2],center:[20,20,1],radius:5,
  declaration:{kind,initializer:{adapter:'environment.node@1',module:({'obstacle_box':'space.axis_aligned_obstacle','degradable_box':'material.degradable_box','local_source':'source.finite_local'})[kind]+'@1.0.0'}}});

test('registered environment selection enables move/scale but never rotation or Results edits', () => {
  for(const kind of ['obstacle_box','degradable_box','local_source']) {
    const e=editor();e.state.environment=[environment(kind)];e.selectEnvironment(kind);
    assert.equal(e.$('move-tool').disabled,false);assert.equal(e.$('scale-tool').disabled,false);
    assert.equal(e.$('rotate-tool').disabled,true);assert.equal(e.$('rotate-tool').title,'environmentRotationUnsupported');
    e.useTool('move');assert.equal(e.state.tool,'move');
    e.useTool('rotate');assert.equal(e.state.tool,'move');
    e.state.view='results';e.renderScene();assert.equal(e.state.tool,'select');
    for(const tool of ['move','scale','rotate']) {assert.equal(e.$(`${tool}-tool`).disabled,true);e.useTool(tool);assert.equal(e.state.tool,'select');}
  }
});

test('environment box gizmos constrain preview to complete voxels and domain before committing once', () => {
  for(const kind of ['obstacle_box','degradable_box']) {
    const v=viewport(), object=environment(kind);v.domain.spacing_um_xyz=[2,3,1];v.domain.counts_xyz=[10,10,10];
    v.setObjects([object],object.id);v.setTool('move');
    assert.equal(v.transform.object.userData.nodeId,object.id);
    const mesh=v.transform.object;mesh.position.set(-50,100,50);
    const bounded=v.constrainEnvironmentTransform();
    assert.deepEqual(bounded.lower,[0,27,8]);assert.deepEqual(bounded.upper,[4,30,10]);
    assert.deepEqual(v.events,[]);v.commitTransform();assert.equal(v.events.length,1);
    v.setTool('scale');mesh.scale.set(.001,100,.001);v.constrainEnvironmentTransform();
    assert.deepEqual(mesh.scale.toArray().map((s,i)=>s*(object.upper[i]-object.lower[i])),[2,30,1]);
    // Crossing odd/even voxel sizes repeatedly must not drift the center.
    for(let pass=0;pass<4;pass++)for(const factor of [1.5,2,1]) {
      mesh.scale.set(factor,1,1);const preview=v.constrainEnvironmentTransform();
      assert.ok(Math.abs(preview.center[0]-12)<=1);
    }
    assert.equal(mesh.position.x,12);
    v.setTool('rotate');assert.equal(v.transform.object,null);
  }
});

test('thin-layer environment tools preserve Z and sphere scale always remains spherical', () => {
  for(const kind of ['obstacle_box','degradable_box','local_source']) {
    const v=viewport(),object=environment(kind);v.domain={geometry:'thin_layer',counts_xyz:[100,100,1],spacing_um_xyz:[1,1,2]};
    v.setObjects([object],object.id);v.setTool('scale');assert.equal(v.transform.showZ,false);
    const mesh=v.transform.object;mesh.position.z=20;mesh.scale.set(2,3,4);v.transform.axis='Y';
    const preview=v.constrainEnvironmentTransform();assert.equal(mesh.position.z,1);
    if(kind==='local_source') {assert.deepEqual(mesh.scale.toArray(),[3,3,3]);assert.equal(preview.radius,15);}
    else {assert.equal(mesh.scale.z,1);assert.deepEqual([preview.lower[2],preview.upper[2]],[0,2]);}
    v.setMode('results');assert.equal(v.transform.object,null);
    v.transform.object=mesh;v.commitTransform();assert.deepEqual(v.events,[]);
  }
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
  const project={execution_profile:'modular-spatial-v1',project_version:'0.6.0'};
  const task={task_contract_version:'0.6.0',execution:{semantics:project.execution_profile},limits:{steps:2345}};
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
