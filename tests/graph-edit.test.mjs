import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import {addNode,connect,connectionProblem,disconnect,missingInputs,preferredTiming,removeNode,setParameter} from '../src/friskoli_cad/web/graph-edit.mjs';
const declarations=JSON.parse(readFileSync(new URL('./fixtures/catalog.modules.json',import.meta.url)));
const modules=new Map(declarations.map(m=>[`${m.id}@${m.version}`,m]));
const get=id=>modules.get(`${id}@1.0.0`),owner={kind:'population',id:'cells'},port=(node,port)=>({node,port});
const graph=()=>({protocol_version:'0.2.0',id:'editor-test',nodes:[],edges:[]});
test('connections check types, population ownership and single-input binding',()=>{
 const g=graph(),a=addNode(g,get('control.delay'),owner),b=addNode(g,get('control.delay'),owner);
 connect(g,modules,port(a.id,'value'),port(b.id,'value'));
 assert.equal(connectionProblem(g,modules,port(a.id,'value'),port(b.id,'value'),'same_step'),'edge.multiple_inputs');
 assert.equal(disconnect(g,g.edges[0].id),true);
 assert.ok(missingInputs(g,modules).some(p=>p.node===b.id&&p.port==='value'));
 const other=addNode(g,get('control.delay'),{kind:'population',id:'other'});
 assert.equal(connectionProblem(g,modules,port(other.id,'value'),port(b.id,'value'),'same_step'),'edge.population');
 const motor=addNode(g,get('signal.constant_bias'),owner);
 assert.equal(connectionProblem(g,modules,port(motor.id,'motor_bias'),port(b.id,'value'),'same_step'),'edge.type');
 assert.throws(()=>addNode(g,modules.get('field.diffusive_local@2.0.0'),owner),/node.scope/);
});
test('cycles require an explicitly initialized previous-step edge',()=>{
 const g=graph(),a=addNode(g,get('control.delay'),owner),b=addNode(g,get('control.delay'),owner);
 connect(g,modules,port(a.id,'value'),port(b.id,'value'));
 const back=[port(b.id,'value'),port(a.id,'value')];
 assert.equal(connectionProblem(g,modules,...back,'same_step'),'edge.cycle');
 assert.equal(preferredTiming(g,modules,...back),'previous_step');
 assert.equal(removeNode(g,b.id),true); assert.equal(g.edges.length,0);
});
test('parameter edits preserve units, reject invalid values and bind declared species',()=>{
 const g=graph(),node=addNode(g,get('growth.linear_elongation'),owner),manifest=get('growth.linear_elongation');
 node.parameters.elongation_rate={value:.5,unit:'um/s',provenance:{kind:'example',reference:'test'}};
 assert.equal(setParameter(node,manifest,'elongation_rate','-1'),'parameter.range');
 assert.equal(setParameter(node,manifest,'elongation_rate','abc'),'parameter.type');
 assert.equal(node.parameters.elongation_rate.value,.5);
 assert.equal(setParameter(node,manifest,'elongation_rate','.8'),null);
 assert.deepEqual(node.parameters.elongation_rate,{value:.8,unit:'um/s',provenance:{kind:'user',reference:'set in Friskoli-CAD'}});
 assert.equal(setParameter(node,manifest,'missing',1),'parameter.set');
 assert.equal(addNode(g,get('field.sample_trilinear'),owner,['oxygen']).parameters.species.value,'oxygen');
});
