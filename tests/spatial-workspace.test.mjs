import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {registerCatalog} from '../src/friskoli_cad/web/catalog.mjs';
import {readWorkspace,writeWorkspace} from '../src/friskoli_cad/web/workspace.mjs';
import {availablePlaceables,availableSourceSpecies,environmentObjects,initializeEnvironmentObject,deleteEnvironmentObject,requiredRoleRemovalProblem,environmentTransformGeometry,transformEnvironmentObject} from '../src/friskoli_cad/web/placeables.mjs';

const number = unit => ({type:'number',unit,minimum:0});
const xyz = prefix => Object.fromEntries([...'xyz'].map(axis => [`${prefix}_${axis}_um`,number('um')]));
const inventory = {shape:'global.scalar',quantity:'source_inventory',unit:'molecule',species_parameter:'species'};
const specs = [
  {id:'space.axis_aligned_obstacle',parameters:{...xyz('lower'),...xyz('upper')},inputs:{},outputs:{}},
  {id:'source.finite_local',parameters:{species:{type:'string'},...xyz('center'),radius_um:number('um'),initial_molecules:number('molecule'),release_rate:number('molecule/s')},inputs:{},outputs:{inventory}},
  {id:'field.diffusive_local',parameters:{species:{type:'string'},diffusivity_um2_s:number('um^2/s')},inputs:{},outputs:{concentration:{shape:'field.scalar',quantity:'concentration',unit:'uM'}}},
  {id:'material.degradable_box',parameters:{species:{type:'string'},...xyz('lower'),...xyz('upper'),initial_molecules:number('molecule')},inputs:{},outputs:{inventory}},
  {id:'reaction.contact_degradation',parameters:{rate:number('1/s')},inputs:{},outputs:{}},
  {id:'plugin.alternative_degradation',parameters:{},inputs:{},outputs:{}}
].map(item => ({...item,version:'1.0.0',scope:'environment',phase:0,state:{},initial_outputs:item.id === 'source.finite_local' ? ['inventory'] : []}));
const objects = specs.slice(0,2).map((spec,index) => ({id:index ? 'source.finite_local' : 'space.obstacle_box',kind:index ? 'local_source' : 'obstacle_box',label:spec.id,description:'test contract',
  initializer:{adapter:'environment.node@1',module:spec.id+'@1.0.0',data_modules:index ? ['field.diffusive_local@1.0.0'] : []},
  properties:Object.keys(spec.parameters).map(path => ({path,label:path,type:spec.parameters[path].type,unit:spec.parameters[path].unit ?? '1'}))}));
objects.push({id:'material.degradable_box',kind:'degradable_box',label:'Material',description:'test material contract',
  initializer:{adapter:'environment.node@1',module:'material.degradable_box@1.0.0',data_modules:['field.diffusive_local@1.0.0'],requirements:[{role:'material.degradation',default_module:'reaction.contact_degradation@1.0.0',scope:'environment'}]},
  properties:Object.keys(specs[3].parameters).map(path => ({path,label:path,type:specs[3].parameters[path].type}))});
const payload = {catalog_version:'0.3.0',execution_semantics:'spatial-unbiased-v1',modules:specs,objects,entries:specs.map(spec => ({key:spec.id+'@1.0.0',
  ...(['reaction.contact_degradation','plugin.alternative_degradation'].includes(spec.id) ? {provides_roles:['material.degradation'],default_parameters:spec.id==='reaction.contact_degradation' ? {rate:.2} : {}} : {})}))};
const registry = registerCatalog(payload);
const template = JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/workspace_3d.project.json',import.meta.url)));
function project() {
  return {...structuredClone(template),project_version:'0.4.0',execution_profile:'spatial-unbiased-v1',random_seed:42,
    domain:{geometry:'volume',counts_xyz:[12,12,12],spacing_um_xyz:[2,2,2]},groups:{},graph:{protocol_version:'0.1.0',id:'spatial',nodes:[],edges:[]},
    species:{substrate:{initial_concentration:{value:0,unit:'uM'}},attractant:{initial_concentration:{value:0,unit:'uM'}}},
    run:{protocol_version:'0.1.0',run_id:'draft',groups:[],channels:{}}};
}

test('Project 0.4 preserves the nonnegative safe seed through save, reopen and history snapshots',() => {
  const state = readWorkspace(project());
  state.settings.include_fields = true;
  const before = writeWorkspace(state); state.project.random_seed = Number.MAX_SAFE_INTEGER;
  assert.equal(readWorkspace(writeWorkspace(state)).project.random_seed,Number.MAX_SAFE_INTEGER);
  assert.equal(readWorkspace(before).project.random_seed,42);
  assert.equal(readWorkspace(before).settings.include_fields,true);
  for (const random_seed of [-1,1.5,Number.MAX_SAFE_INTEGER+1,undefined]) assert.throws(() => readWorkspace({...project(),random_seed}));
  assert.throws(() => readWorkspace({...project(),execution_profile:'conservative-pts-bulk-v1'}));
});

test('only registered exact environment adapters can place objects',() => {
  assert.deepEqual(availablePlaceables(registry.modules,null,registry.objects).map(item => item.status),['ready','ready','ready']);
  const capabilities={placeables:[],execution_profiles:['spatial-unbiased-v1'],placeable_profiles:{'spatial-unbiased-v1':objects.map(object=>object.initializer.module)}};
  assert.deepEqual(availablePlaceables(registry.modules,capabilities,registry.objects,project()).map(item=>item.status),['ready','ready','ready']);
  assert.deepEqual(availablePlaceables(registry.modules,{placeables:[],execution_profiles:['spatial-unbiased-v1']},registry.objects,project()).map(item=>item.status),['missing','missing','missing']);
  const unknown = structuredClone(objects[0]); unknown.initializer.module = 'unknown.obstacle@1.0.0';
  const unknownAdapter = structuredClone(objects[1]); unknownAdapter.initializer.adapter = 'environment.node@9';
  const unknownObjects = new Map([[unknown.id,unknown],[unknownAdapter.id,unknownAdapter]]);
  assert.deepEqual(availablePlaceables(registry.modules,null,unknownObjects).map(item => item.status),['unsupported','unsupported']);
  const p=project(), before=structuredClone(p);
  assert.throws(() => initializeEnvironmentObject(p,unknown,registry.modules,[1,1,1],'substrate'));
  assert.deepEqual(p,before);
  assert.deepEqual(environmentObjects(p,unknownObjects,registry.modules),[]);
});

test('obstacle uses grid-aligned graph bounds, edits and undo do not introduce a second geometry store',() => {
  const p=project(), id=initializeEnvironmentObject(p,objects[0],registry.modules,[7.7,11.1,8.4],'substrate');
  const item=environmentObjects(p,registry.objects,registry.modules)[0];
  assert.deepEqual(item.lower,[6,10,8]); assert.deepEqual(item.upper,[8,12,10]); assert.equal(item.id,id);
  const saved=writeWorkspace(readWorkspace(p));
  p.graph.nodes[0].parameters.upper_x_um.value=12;
  assert.equal(environmentObjects(p,registry.objects,registry.modules)[0].upper[0],12);
  assert.equal(environmentObjects(readWorkspace(saved).project,registry.objects,registry.modules)[0].upper[0],8);
  deleteEnvironmentObject(p,id); assert.equal(p.graph.nodes.length,0);
  assert.equal(environmentObjects(readWorkspace(saved).project,registry.objects,registry.modules).length,1);
});

test('environment transforms update registered graph parameters atomically without separate display geometry',()=>{
  for(const declaration of [objects[0],objects[2]]) {
    const p=project(),id=initializeEnvironmentObject(p,declaration,registry.modules,[7,11,9],'substrate');
    const before=writeWorkspace(readWorkspace(p));
    const geometry=transformEnvironmentObject(p,id,registry.objects,registry.modules,{center:[100,-4,13.4],size:[3.1,.01,4.7]});
    assert.deepEqual(geometry.lower,[20,0,12]);assert.deepEqual(geometry.upper,[24,2,16]);
    const item=environmentObjects(p,registry.objects,registry.modules).find(item=>item.id===id);
    assert.deepEqual(item.lower,geometry.lower);assert.deepEqual(item.upper,geometry.upper);
    assert.equal(item.node.parameters.lower_x_um.unit,'um');assert.equal(item.node.parameters.lower_x_um.provenance.kind,'user');
    const after=writeWorkspace(readWorkspace(p));
    assert.deepEqual(environmentObjects(readWorkspace(before).project,registry.objects,registry.modules).find(item=>item.id===id).lower,[6,10,8]);
    assert.deepEqual(environmentObjects(readWorkspace(after).project,registry.objects,registry.modules).find(item=>item.id===id).upper,[24,2,16]);
    assert.deepEqual(after.project.graph.edges,before.project.graph.edges);
  }
});

test('source transform retains inventory, release rate and shared field and allows support outside a thin layer',()=>{
  const p=project();p.domain={geometry:'thin_layer',counts_xyz:[12,12,1],spacing_um_xyz:[2,2,2]};
  const id=initializeEnvironmentObject(p,objects[1],registry.modules,[7,11,1],'substrate');
  const field=structuredClone(p.graph.nodes.find(n=>n.module_id==='field.diffusive_local'));
  const original=structuredClone(p.graph.nodes.find(n=>n.id===id));
  transformEnvironmentObject(p,id,registry.objects,registry.modules,{center:[100,-10,100],radius:5});
  const object=environmentObjects(p,registry.objects,registry.modules).find(item=>item.id===id);
  assert.ok(object.center[0] < 24 && object.center[0] > 23.99);assert.equal(object.center[1],0);assert.equal(object.center[2],1);
  assert.equal(object.radius,5);
  for(const key of ['species','initial_molecules','release_rate'])assert.deepEqual(object.node.parameters[key],original.parameters[key]);
  assert.deepEqual(p.graph.nodes.find(n=>n.module_id==='field.diffusive_local'),field);
});

test('invalid or colliding environment transforms leave every graph parameter unchanged',()=>{
  const p=project(),id=initializeEnvironmentObject(p,objects[0],registry.modules,[3,3,3],'substrate');
  const source=initializeEnvironmentObject(p,objects[1],registry.modules,[15,15,15],'substrate');
  const before=structuredClone(p);
  for(const transform of [{center:[NaN,3,3],size:[2,2,2]},{center:[3,3,3],size:[0,2,2]},
    {center:[15,15,15],size:[2,2,2]}]) {
    assert.throws(()=>transformEnvironmentObject(p,id,registry.objects,registry.modules,transform));assert.deepEqual(p,before);
  }
  assert.throws(()=>transformEnvironmentObject(p,source,registry.objects,registry.modules,{center:[3,3,3],radius:1}),/sourcePlacementOverlap/);
  assert.deepEqual(p,before);
  assert.throws(()=>transformEnvironmentObject(p,id,new Map(),registry.modules,{center:[3,3,3],size:[2,2,2]}));
  const restricted=new Map(registry.modules),spec=structuredClone(restricted.get(objects[0].initializer.module));
  spec.parameters.upper_z_um.maximum=6;restricted.set(objects[0].initializer.module,spec);
  assert.throws(()=>transformEnvironmentObject(p,id,registry.objects,restricted,{center:[7,7,7],size:[2,2,2]}),/parameter.range/);
  assert.deepEqual(p,before);
});

test('box normalization uses each axis spacing, preserves thin-layer thickness and never emits fractional voxels',()=>{
  const domain={geometry:'thin_layer',counts_xyz:[10,8,1],spacing_um_xyz:[2,3,1]};
  const object={kind:'obstacle_box',lower:[2,3,0],upper:[4,6,1]};
  for(const center of [[-100,100,5],[3.1,4.9,1],[20,24,-5]])for(const size of [[.01,.01,.01],[100,100,100],[5.1,7.2,3]]) {
    const next=environmentTransformGeometry(object,domain,{center,size});
    for(let i=0;i<3;i++) {
      assert.equal(next.lower[i]/domain.spacing_um_xyz[i],Math.round(next.lower[i]/domain.spacing_um_xyz[i]));
      assert.equal(next.upper[i]/domain.spacing_um_xyz[i],Math.round(next.upper[i]/domain.spacing_um_xyz[i]));
      assert.ok(next.lower[i]>=0 && next.upper[i]<=domain.counts_xyz[i]*domain.spacing_um_xyz[i]);
      assert.ok(next.size[i]>=domain.spacing_um_xyz[i]);
    }
    assert.deepEqual([next.lower[2],next.upper[2]],[0,1]);
  }
});

test('environment edits use the real editor transaction and Undo/Redo restores solver parameters and draft revision',()=>{
  const p=project(),id=initializeEnvironmentObject(p,objects[0],registry.modules,[3,3,3],'substrate');
  const state={...readWorkspace(p),history:[],future:[],revision:0,checks:['old check'],selectedEnvironment:id};
  const app=readFileSync(new URL('../src/friskoli_cad/web/app.mjs',import.meta.url),'utf8');
  // Run production history/transaction functions, replacing only browser effects.
  const historySource=app.slice(app.indexOf('const snapshot ='),app.indexOf('applyLanguage();'));
  const history=new Function('state','writeWorkspace',`
    const localStorage={setItem(){}}, RECOVERY_KEY='test', $=()=>({});
    const renderAll=()=>{},status=()=>{},t=value=>value;
    ${historySource}
    return {edit,undo,redo};`)(state,writeWorkspace);
  const geometry=()=>environmentObjects(state.project,registry.objects,registry.modules).find(o=>o.id===id);
  assert.equal(history.edit('updated',()=>transformEnvironmentObject(state.project,id,registry.objects,registry.modules,
    {center:[9,9,9],size:[4,4,4]})),true);
  assert.deepEqual(geometry().lower,[8,8,8]);assert.equal(state.history.length,1);assert.equal(state.revision,1);assert.deepEqual(state.checks,[]);
  history.undo();assert.deepEqual(geometry().lower,[2,2,2]);assert.equal(state.future.length,1);assert.equal(state.revision,2);
  history.redo();assert.deepEqual(geometry().upper,[12,12,12]);assert.equal(state.history.length,1);assert.equal(state.revision,3);
  const before=structuredClone(state.project);
  assert.equal(history.edit('updated',()=>transformEnvironmentObject(state.project,id,registry.objects,registry.modules,
    {center:[Infinity,9,9],size:[4,4,4]})),false);
  assert.deepEqual(state.project,before);assert.equal(state.history.length,1);assert.equal(state.revision,3);
});

test('multiple sources share one registered nutrient field and deletion preserves the remaining source field',() => {
  const p=project(), id=initializeEnvironmentObject(p,objects[1],registry.modules,[5.7,7.1,9],'substrate');
  const source=p.graph.nodes.find(node => node.id===id),field=p.graph.nodes.find(node => node.module_id==='field.diffusive_local');
  assert.equal(source.parameters.species.value,'substrate'); assert.equal(field.parameters.species.value,'substrate');
  assert.deepEqual(environmentObjects(p,registry.objects,registry.modules)[0].center,[5,7,9]);
  assert.equal(p.graph.edges.length,0); assert.deepEqual(availableSourceSpecies(p),['substrate','attractant']);
  initializeEnvironmentObject(p,objects[1],registry.modules,[15,15,15],'substrate');
  assert.equal(new Set(p.graph.nodes.filter(node=>node.module_id==='source.finite_local').map(node=>node.owner.id)).size,2);
  assert.ok(p.graph.nodes.filter(node=>node.module_id==='source.finite_local').every(node=>node.owner.id===node.id));
  assert.equal(p.graph.nodes.filter(node=>node.module_id==='field.diffusive_local').length,1);
  assert.equal(availablePlaceables(registry.modules,null,registry.objects,p)[1].status,'ready');
  deleteEnvironmentObject(p,id); assert.ok(!p.graph.nodes.some(node=>node.id===source.id)); assert.ok(p.graph.nodes.some(node=>node.id===field.id));
  const last=p.graph.nodes.find(node=>node.module_id==='source.finite_local');
  deleteEnvironmentObject(p,last.id); assert.equal(p.graph.nodes.length,0);
});

test('ambiguous source species require explicit selection and do not mutate the draft',()=>{
  const p=project(),before=structuredClone(p);
  assert.throws(()=>initializeEnvironmentObject(p,objects[1],registry.modules,[1,1,1]),/chooseSourceSpecies/);
  assert.deepEqual(p,before);
  const id=initializeEnvironmentObject(p,objects[1],registry.modules,[1,1,1],'attractant');
  assert.equal(p.graph.nodes.find(n=>n.id===id).parameters.species.value,'attractant');
  assert.equal(p.graph.nodes.find(n=>n.module_id==='field.diffusive_local').parameters.species.value,'attractant');
});

test('obstacle placement rejects initial capsule/source contacts without moving or deleting anything',() => {
  const p=project();
  p.groups.g={ids:['cell'],positions_um:[[7,7,7]],orientation_xyzw:[[0,0,0,1]],initial_geometry:[{length_um:6,diameter_um:1}]};
  const before=structuredClone(p);
  // Capsule segment reaches this voxel although its center is outside the voxel.
  assert.throws(() => initializeEnvironmentObject(p,objects[0],registry.modules,[9,7,7],'substrate'),/obstaclePlacementOverlap/);
  assert.deepEqual(p,before);
  initializeEnvironmentObject(p,objects[0],registry.modules,[19,19,19],'substrate');
  initializeEnvironmentObject(p,objects[1],registry.modules,[3,3,3],'substrate');
  const withSource=structuredClone(p);
  assert.throws(() => initializeEnvironmentObject(p,objects[0],registry.modules,[3,3,3],'substrate'),/obstaclePlacementOverlap/);
  assert.deepEqual(p,withSource);
  assert.throws(() => initializeEnvironmentObject(p,objects[1],registry.modules,[19,19,19],'substrate'),/sourcePlacementOverlap/);
  assert.deepEqual(p,withSource);
});

test('deleting a source preserves a referenced field for explicit graph repair',() => {
  const p=project(),id=initializeEnvironmentObject(p,objects[1],registry.modules,[1,1,1],'substrate');
  const field=p.graph.nodes.find(node => node.module_id==='field.diffusive_local');
  p.graph.nodes.push({id:'reader',module_id:'field.sample_local',module_version:'1.0.0',owner:{kind:'population',id:'g'},parameters:{}});
  p.graph.edges.push({id:'read',from:{node:field.id,port:'concentration'},to:{node:'reader',port:'field'},timing:'same_step'});
  deleteEnvironmentObject(p,id);
  assert.ok(p.graph.nodes.some(node => node.id===field.id)); assert.equal(p.graph.edges.length,1);
});

test('material placement atomically installs one role provider and shared nutrient field, with guarded removal',() => {
  const p=project(); initializeEnvironmentObject(p,objects[1],registry.modules,[1,1,1],'substrate');
  const material=initializeEnvironmentObject(p,objects[2],registry.modules,[11,11,11],'substrate');
  const provider=p.graph.nodes.find(node=>node.module_id==='reaction.contact_degradation');
  assert.equal(provider.parameters.rate.value,.2);
  assert.equal(provider.parameters.rate.provenance.kind,'example');
  assert.match(provider.parameters.rate.provenance.reference,/constructed/);
  assert.deepEqual(readWorkspace(writeWorkspace(readWorkspace(p))).project.graph.nodes.find(node=>node.id===provider.id).parameters.rate,provider.parameters.rate);
  assert.equal(p.graph.nodes.filter(node=>node.module_id==='field.diffusive_local').length,1);
  assert.equal(environmentObjects(p,registry.objects,registry.modules).find(item=>item.id===material).kind,'degradable_box');
  assert.equal(requiredRoleRemovalProblem(p,provider.id,registry.objects,registry.modules),'material.degradation');
  initializeEnvironmentObject(p,objects[2],registry.modules,[17,17,17],'substrate');
  assert.equal(p.graph.nodes.filter(node=>node.module_id==='reaction.contact_degradation').length,1);
  p.graph.nodes.push({id:'alternative',module_id:'plugin.alternative_degradation',module_version:'1.0.0',owner:{kind:'environment',id:'domain'},parameters:{}});
  assert.equal(requiredRoleRemovalProblem(p,provider.id,registry.objects,registry.modules),null);
  p.graph.nodes.pop();
  for (const node of [...p.graph.nodes].filter(node=>node.module_id==='material.degradable_box')) deleteEnvironmentObject(p,node.id);
  assert.equal(requiredRoleRemovalProblem(p,provider.id,registry.objects,registry.modules),null);
  assert.ok(p.graph.nodes.some(node=>node.module_id==='field.diffusive_local'));
});

test('unknown modules cannot satisfy a required degradation role; missing role defaults abort placement',() => {
  const p=project();
  p.graph.nodes.push({id:'unknown',module_id:'unknown.degradation',module_version:'1.0.0',owner:{kind:'environment',id:'domain'},parameters:{}});
  const modules=new Map(registry.modules); modules.delete('reaction.contact_degradation@1.0.0');
  const before=structuredClone(p);
  assert.throws(()=>initializeEnvironmentObject(p,objects[2],modules,[11,11,11],'substrate'),/Unavailable required role/);
  assert.deepEqual(p,before);
});

test('result inventory follows the selected snapshot while frozen graph geometry stays independent of draft edits',() => {
  const draft=project(),id=initializeEnvironmentObject(draft,objects[2],registry.modules,[11,11,11],'substrate');
  const frozen=structuredClone(draft), node=draft.graph.nodes.find(node=>node.id===id);
  node.parameters.upper_x_um.value=18;
  const at = remaining_molecules => environmentObjects(frozen,registry.objects,registry.modules,
    {[id]:{object_type:'material.degradable_box',remaining_molecules}}).find(object=>object.id===id);
  assert.equal(at(100).remaining_molecules,100); assert.equal(at(0).remaining_molecules,0);
  assert.equal(at(100).remaining_molecules,100); assert.equal(at(100).upper[0],12);
  assert.equal(environmentObjects(draft,registry.objects,registry.modules).find(object=>object.id===id).upper[0],18);
  assert.equal(environmentObjects(frozen,registry.objects,registry.modules,{}).find(object=>object.id===id).remaining_molecules,null);
});
