import {test} from 'node:test';
import assert from 'node:assert/strict';
import {arrangeGraph,timelineMarkers} from '../src/friskoli_cad/web/workflow-layout.mjs';
import {nodeGeometry,NODE_WIDTH} from '../src/friskoli_cad/web/workflow-components.mjs';
const manifest={id:'m',version:'1',inputs:{in:{}},outputs:{out:{}},parameters:{}};
const modules=new Map([['m@1',manifest]]);
const node=id=>({id,module_id:'m',module_version:'1',owner:{kind:'population',id:'cells'}});
const edge=(id,from,to,timing='same_step')=>({id,from:{node:from,port:'out'},to:{node:to,port:'in'},timing});

test('explicit organization removes an avoidable crossing without changing graph timing',()=>{
  const graph={nodes:['a','b','c','d'].map(node),edges:[edge('ad','a','d'),edge('bc','b','c'),edge('da','d','a','previous_step')]};
  const frozen=structuredClone(graph), prior={a:{x:400,y:500,collapsed:true}};
  const layout=arrangeGraph(graph,modules,prior,nodeGeometry);
  assert.ok(layout.a.x<layout.d.x);assert.ok(layout.b.x<layout.c.x);
  assert.ok((layout.a.y-layout.b.y)*(layout.d.y-layout.c.y)>0);
  assert.equal(layout.a.collapsed,true);assert.deepEqual(graph,frozen);assert.equal(prior.a.x,400);
  assert.deepEqual(arrangeGraph(graph,modules,prior,nodeGeometry),layout);
});

test('organization fits variable card heights and keeps invalid cycles editable',()=>{
  const graph={nodes:['a','b','c','d'].map(node),edges:[edge('ab','a','b'),edge('ba','b','a'),edge('cd','c','d')]};
  const layout=arrangeGraph(graph,modules,{},nodeGeometry);
  for(const [id,p] of Object.entries(layout))for(const [other,q] of Object.entries(layout)){
    if(id===other)continue;
    assert.ok(Math.abs(p.x-q.x)>=NODE_WIDTH || Math.abs(p.y-q.y)>=nodeGeometry(manifest,p.collapsed,true).height);
  }
  assert.equal(graph.edges.length,3);
});

test('long replay has bounded accessible markers with current frame and all event counts',()=>{
  const snapshots=Array.from({length:1901},(_,i)=>({frame:{time_s:i*.05,events:[92,94,1500].includes(i)?[{}]:[]}}));
  const marks=timelineMarkers(snapshots,1800);
  assert.ok(marks.length<=72);assert.ok(marks.some(m=>m.index===1800));
  assert.equal(marks.reduce((sum,m)=>sum+m.events,0),3);
  assert.equal(marks[0].start,0);assert.equal(marks.at(-1).end,1900);
  assert.equal(marks.reduce((sum,m)=>sum+m.end-m.start+1,0),1901);
});
