export function resolutionGuidance(project){
 const spacing=project.domain.spacing_um_xyz,diameters=Object.values(project.groups).flatMap(g=>(g.initial_geometry??[]).map(x=>x?.diameter_um).filter(Number.isFinite));
 const scales=[];
 for(const node of project.graph.nodes){for(const [key,entry]of Object.entries(node.parameters))if(/radius_um|support_um|width_um/.test(key)&&Number.isFinite(entry.value)&&entry.value>0)scales.push({node:node.id,parameter:key,size_um:entry.value,cells:spacing.map(dx=>entry.value/dx)});}
 return {smallest_diameter_um:diameters.length?Math.min(...diameters):null,cells_across:diameters.length?spacing.map(dx=>Math.min(...diameters)/dx):null,scales,half_spacing_cells_multiplier:project.domain.geometry==='thin_layer'?4:8,converged:false};
}
export function renderResolutionGuidance(parent,project,zh=false){
 const data=resolutionGuidance(project),section=document.createElement('details');section.className='property-section resolution-guidance';
 const summary=document.createElement('summary');summary.textContent=zh?'格距与细化指导':'Resolution and refinement';section.append(summary);
 const line=text=>{const p=document.createElement('p');p.textContent=text;section.append(p);};
 line(data.cells_across?(zh?'最小菌体直径跨格数 XYZ：':'Cells across the smallest diameter XYZ: ')+data.cells_across.map(v=>v.toFixed(2)).join(' / '):(zh?'菌体几何未知，暂不能计算跨格数。':'Cell geometry is unknown; cells across diameter cannot be calculated.'));
 for(const scale of data.scales)line(`${scale.node}.${scale.parameter}: ${scale.size_um} µm · ${scale.cells.map(v=>v.toFixed(2)).join(' / ')} cells`);
 line(zh?'跨格数仅描述尺度，不能证明精度。先固定物理域、源的物理支持、seed 与输出时间；分别用当前格距、半格距运行，再比较物质量账与预先选定指标；同时检查 dt 收敛。':'Cells across a feature describe scale, not accuracy. Keep the physical domain, source support, seed and output times fixed; compare the current and half spacing using mass balance and a declared metric, and check dt convergence separately.');
 line(zh?`半格距约需 ${data.half_spacing_cells_multiplier} 倍场存储；显式扩散可能需要约4倍子步。实际耗时须由当前机器测量；未提供收敛证据时不推荐“最佳”格距。`:`Half spacing requires about ${data.half_spacing_cells_multiplier}× field storage and may require about 4× explicit diffusion substeps. Measure elapsed time on the actual device. No optimal spacing is claimed without convergence evidence.`);
 parent.append(section);
}
