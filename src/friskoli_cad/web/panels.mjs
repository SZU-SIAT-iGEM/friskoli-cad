// Dock sizing is a local view preference, independent of the scientific document.
export function installDockSizing(studio, diagnostics) {
  const config = [
    {id:'resize-left', variable:'--left-width', axis:'x', min:175, max:360, initial:232, sign:1},
    {id:'resize-right', variable:'--right-width', axis:'x', min:220, max:420, initial:282, sign:-1},
    {id:'resize-bottom', variable:'--bottom-height', axis:'y', min:110, max:350, initial:190, sign:-1},
  ];
  for (const spec of config) {
    const handle = document.getElementById(spec.id), target = spec.axis === 'y' ? diagnostics : studio;
    let value = spec.initial;
    try { const saved=Number(localStorage.getItem('friskoli.'+spec.id)); if(saved>=spec.min&&saved<=spec.max)value=saved; } catch {}
    const apply = next => { value=Math.max(spec.min,Math.min(spec.max,next)); target.style.setProperty(spec.variable,value+'px');handle.setAttribute('aria-valuenow',Math.round(value)); };
    apply(value); handle.setAttribute('aria-valuemin',spec.min);handle.setAttribute('aria-valuemax',spec.max);
    const save=()=>{try{localStorage.setItem('friskoli.'+spec.id,String(value));}catch{}};
    handle.addEventListener('pointerdown',event=>{
      if(event.button!==0)return;event.preventDefault();handle.setPointerCapture(event.pointerId);
      const origin=value,start=spec.axis==='x'?event.clientX:event.clientY;
      const move=e=>apply(origin+spec.sign*((spec.axis==='x'?e.clientX:e.clientY)-start));
      const end=e=>{handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',end);handle.removeEventListener('pointercancel',end);if(e.type==='pointercancel')apply(origin);save();};
      handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',end);handle.addEventListener('pointercancel',end);
    });
    handle.addEventListener('keydown',event=>{const direction={ArrowLeft:-1,ArrowRight:1,ArrowUp:-1,ArrowDown:1}[event.key];if(direction){event.preventDefault();apply(value+direction*spec.sign*10);save();}});
    handle.addEventListener('dblclick',()=>{apply(spec.initial);save();});
  }
}

export function preserveScroll(element, render) {
  const top = element.scrollTop, left = element.scrollLeft; render(); element.scrollTop=top;element.scrollLeft=left;
}
