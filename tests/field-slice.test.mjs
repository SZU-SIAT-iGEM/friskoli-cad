import {test} from 'node:test';
import assert from 'node:assert/strict';
import {concentrationSlice,fieldDisplayDomain} from '../src/friskoli_cad/web/field-slice.mjs';
const domain={counts_xyz:[3,2,2],spacing_um_xyz:[2,3,4]};
const field={values_zyx:[[[0,1,2],[10,11,12]],[[100,101,102],[110,111,112]]]};
test('volume-mean preview uses saved grid spacing and rejects a different physical extent',()=>{
 const computation={geometry:'volume',counts_xyz:[6,4,4],spacing_um_xyz:[1,1.5,2]};
 const preview={...field,aggregation:'volume_mean',field_domain:{...domain,geometry:'volume'}};
 assert.deepEqual(fieldDisplayDomain(preview,computation,{required:true,stride:[2,2,2]}),preview.field_domain);
 assert.equal(concentrationSlice(preview,computation,'z',1).coordinate,6);
 assert.throws(()=>fieldDisplayDomain(preview,computation,{stride:[1,1,1]}));
 assert.throws(()=>fieldDisplayDomain({...preview,field_domain:{...preview.field_domain,spacing_um_xyz:[1,3,4]}},computation));
 assert.throws(()=>fieldDisplayDomain(field,computation,{required:true}));
});
test('XY, XZ, YZ sections map Z/Y/X data into physical axes without interpolation',()=>{
  const xy=concentrationSlice(field,domain,'z',1);assert.deepEqual(xy.values,[[100,101,102],[110,111,112]]);assert.deepEqual(xy.size,[6,6]);assert.equal(xy.coordinate,6);
  const xz=concentrationSlice(field,domain,'y',0);assert.deepEqual(xz.values,[[0,1,2],[100,101,102]]);assert.deepEqual(xz.size,[6,8]);assert.equal(xz.coordinate,1.5);
  const yz=concentrationSlice(field,domain,'x',2);assert.deepEqual(yz.values,[[2,12],[102,112]]);assert.deepEqual(yz.size,[6,8]);assert.equal(yz.coordinate,5);
});
test('invalid and fractional sections are rejected before rendering',()=>{
  for(const axis of ['q',null])assert.throws(()=>concentrationSlice(field,domain,axis));
  for(const index of [-1,.5,2,NaN])assert.throws(()=>concentrationSlice(field,domain,'z',index));
});
