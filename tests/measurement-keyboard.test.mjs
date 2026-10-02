import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as THREE from '../src/friskoli_cad/web/vendor/three/build/three.module.js';
import {nextHit} from '../src/friskoli_cad/web/catalog.mjs';
import {environmentTransformGeometry,isEnvironmentObject} from '../src/friskoli_cad/web/placeables.mjs';

// Exercise production methods using shipped Three.js, without a WebGL/browser constructor.
const source=readFileSync(new URL('../src/friskoli_cad/web/scene3d.mjs',import.meta.url),'utf8');
const SpatialViewport=new Function('THREE','nextHit','environmentTransformGeometry','isEnvironmentObject',
  source.replace(/^import .*;\r?\n/gm,'').replace(/^export /gm,'')+'\nreturn SpatialViewport;')(
    THREE,nextHit,environmentTransformGeometry,isEnvironmentObject);
function viewport() {
  const v=Object.create(SpatialViewport.prototype), reports=[];
  Object.assign(v,{mode:'space',tool:'measure',size:[10,10,10],domain:{spacing_um_xyz:[1,1,1]},
    camera:new THREE.PerspectiveCamera(),raycaster:new THREE.Raycaster(),objectHits:[],meshes:[],blockHits:[],
    measurePoints:[],measureGroup:new THREE.Group(),geometryCache:new Map(),request(){},pointFromEvent(){},
    callbacks:{measure:r=>reports.push(r),selectCell(){},selectEnvironment(){}}});
  v.camera.position.set(5,5,20);v.camera.lookAt(5,5,5);v.camera.updateMatrixWorld(true);
  v.aim=(x,y)=>v.raycaster.set(new THREE.Vector3(x,y,20),new THREE.Vector3(0,0,-1));
  return {v,reports};
}
function box(id,position,size=[2,2,2]) {
  const mesh=new THREE.Mesh(new THREE.BoxGeometry(...size),new THREE.MeshBasicMaterial());
  mesh.position.fromArray(position);mesh.userData.nodeId=id;return mesh;
}
function closeArray(actual,expected) { actual.forEach((value,index)=>assert.ok(Math.abs(value-expected[index])<1e-6,`${actual} != ${expected}`)); }

test('two surface hits at different Z report world distance, delta and endpoint provenance',()=>{
  const {v,reports}=viewport();
  v.objectHits=[box('low',[1,1,2]),box('high',[4,1,6])];
  const scientificInput={domain:structuredClone(v.domain),objects:[{id:'low',center:[1,1,2]},{id:'high',center:[4,1,6]}]};
  const before=structuredClone(scientificInput);
  v.aim(1,1);v.addMeasurePoint();
  assert.equal(reports.at(-1).distance,null);assert.equal(reports.at(-1).endpoints[0].source,'surface');
  v.aim(4,1);v.addMeasurePoint();
  const result=reports.at(-1);
  assert.equal(result.distance,5);closeArray(result.delta,[3,0,4]);
  closeArray(result.endpoints[0].position,[1,1,3]);closeArray(result.endpoints[1].position,[4,1,7]);
  assert.deepEqual(result.endpoints.map(p=>p.id),['low','high']);
  assert.deepEqual(scientificInput,before);
  assert.deepEqual(v.objectHits.map(mesh=>mesh.position.toArray()),before.objects.map(o=>o.center));
  result.endpoints[0].position[0]=999;
  assert.equal(v.measureEndpoints[0].position[0],1,'callback cannot mutate stored endpoint');
  v.aim(8,8);v.addMeasurePoint();assert.equal(v.measurePoints.length,1);
});

test('nearest real visible cell instance wins over an environment object, while proxy volumes and unknown cell geometry do not',()=>{
  const {v}=viewport();
  v.objectHits=[box('behind',[1,1,2])];
  const cells=new THREE.InstancedMesh(new THREE.SphereGeometry(1,16,8),new THREE.MeshBasicMaterial(),2);
  cells.setMatrixAt(0,new THREE.Matrix4().makeTranslation(1,1,7));
  cells.setMatrixAt(1,new THREE.Matrix4().makeTranslation(7,1,7));
  cells.userData.cellIds=['real','unknown'];v.meshes=[cells];
  v.snapshot={frame:{cells:[{id:'real',geometry:{length_um:2,diameter_um:2}},{id:'unknown',geometry:null}]}};
  const before=structuredClone(v.snapshot);
  const proxy=box('population-proxy',[5,5,10],[10,10,2]);v.blockHits=[proxy];
  v.aim(1,1);let hit=v.measurementEndpoint();
  assert.equal(hit.source,'cell');assert.equal(hit.id,'real');closeArray(hit.point.toArray(),[1,1,8]);
  v.aim(7,1);hit=v.measurementEndpoint();assert.equal(hit.source,'reference-plane');
  const parent=new THREE.Group();parent.add(cells);parent.visible=false;
  v.aim(1,1);assert.equal(v.measurementEndpoint().id,'behind');
  v.objectHits[0].material.visible=false;
  assert.equal(v.measurementEndpoint().source,'reference-plane');
  assert.deepEqual(v.snapshot,before);
});

test('empty-space fallback is labeled and Results cannot append measurement or alter scientific data',()=>{
  const {v,reports}=viewport();v.aim(8,8);v.addMeasurePoint();
  assert.deepEqual(reports.at(-1).endpoints,[{position:[8,8,5],source:'reference-plane'}]);
  const draft={domain:structuredClone(v.domain),cells:[]};v.snapshot={frame:{cells:[]}};
  const before=JSON.stringify({draft,snapshot:v.snapshot});
  v.mode='results';const count=reports.length,points=structuredClone(v.measureEndpoints);
  v.addMeasurePoint();v.pick({});
  assert.equal(reports.length,count);assert.deepEqual(v.measureEndpoints,points);
  assert.equal(JSON.stringify({draft,snapshot:v.snapshot}),before);
});

const app=readFileSync(new URL('../src/friskoli_cad/web/app.mjs',import.meta.url),'utf8');
function keyboard(view='space') {
  const calls=[],state={view,selectedBlock:'block',selectedEnvironment:'object',graphSelection:{kind:'node',id:'n'}};
  const document={activeElement:{tagName:'BODY'},querySelector:()=>null,addEventListener:(_,fn)=>document.press=fn};
  const $=id=>({hidden:true,disabled:false,click:()=>calls.push(id)});
  const block=app.slice(app.indexOf("document.addEventListener('keydown', event => {"),app.indexOf("for (const [id, key] of [['dt-input'"));
  const names=['document','$','state','graphEditor','viewport','closeDocks','useTool','saveWorkspace','redo','undo','removeEnvironment','removeBlock','deleteGraphItem','stop','setFrame'];
  new Function(...names,block)(document,$,state,{fit:()=>calls.push('graph-fit')},
    {fit:()=>calls.push('space-fit'),toggleGrid:()=>calls.push('grid')},()=>{},tool=>calls.push(tool),
    ...['save','redo','undo','delete-object','delete-block','delete-node','stop','frame'].map(name=>()=>calls.push(name)));
  return {calls,document,press(key,options={}){let prevented=false;document.press({key,preventDefault(){prevented=true;},...options});return prevented;}};
}

test('actual keyboard handler routes F to the active workspace and preserves browser modifier combinations',()=>{
  for(const [view,expected] of [['workflow','graph-fit'],['space','space-fit'],['results','space-fit'],['design',null]]) {
    const k=keyboard(view);assert.equal(k.press('f'),Boolean(expected));assert.deepEqual(k.calls,expected?[expected]:[]);
  }
  for(const modifier of ['ctrlKey','metaKey','altKey','shiftKey']) for(const key of ['f','g','b','e','r','m','o','Delete']) {
    const k=keyboard();assert.equal(k.press(key,{[modifier]:true}),false);assert.deepEqual(k.calls,[]);
  }
  const k=keyboard();k.press('m');k.press('s',{ctrlKey:true});k.press('z',{ctrlKey:true,shiftKey:true});
  assert.deepEqual(k.calls,['measure','save','redo']);
  for(const focus of [{tagName:'INPUT'},{tagName:'DIV',isContentEditable:true}]) {
    const editor=keyboard('workflow');editor.document.activeElement=focus;assert.equal(editor.press('f'),false);assert.deepEqual(editor.calls,[]);
  }
});
