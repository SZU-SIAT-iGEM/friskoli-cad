// Values are stored Z/Y/X; these mappings preserve physical axes and grid centers.
export function fieldDisplayDomain(field,domain,{required=false,stride=null}={}){
  if(!field?.field_domain){if(required)throw Error('Missing field display domain');return domain;}
  const shown=field.field_domain;
  if(field.aggregation!=='volume_mean'||shown.geometry!==domain.geometry||!Array.isArray(shown.counts_xyz)||shown.counts_xyz.length!==3||!Array.isArray(shown.spacing_um_xyz)||shown.spacing_um_xyz.length!==3)throw Error('Invalid field display domain');
  for(let axis=0;axis<3;axis++){
    const count=shown.counts_xyz[axis],spacing=shown.spacing_um_xyz[axis],ratio=domain.counts_xyz[axis]/count;
    if(!Number.isSafeInteger(count)||count<1||!Number.isSafeInteger(ratio)||ratio<1||!Number.isFinite(spacing)||spacing<=0||(stride&&ratio!==stride[axis])||Math.abs(spacing-domain.spacing_um_xyz[axis]*ratio)>1e-10*Math.max(1,spacing))throw Error('Field display grid does not cover computation domain');
  }
  return shown;
}
export function concentrationSlice(field, domain, normal = 'z', index = 0) {
  domain=fieldDisplayDomain(field,domain);
  const axes = {z:[0,1,2], y:[0,2,1], x:[1,2,0]}[normal];
  if (!axes) throw new Error('Unknown section plane');
  const [horizontal, vertical, depth] = axes, counts = domain.counts_xyz, spacing = domain.spacing_um_xyz;
  if (!Number.isInteger(index) || index < 0 || index >= counts[depth]) throw new Error('Section index is outside the domain');
  const values = Array.from({length:counts[vertical]}, (_,v) => Array.from({length:counts[horizontal]}, (_,h) => {
    const p=[0,0,0];p[horizontal]=h;p[vertical]=v;p[depth]=index;
    return field.values_zyx[p[2]][p[1]][p[0]];
  }));
  return {values, axes, width:counts[horizontal], height:counts[vertical],
    size:[counts[horizontal]*spacing[horizontal],counts[vertical]*spacing[vertical]], coordinate:(index+.5)*spacing[depth]};
}
