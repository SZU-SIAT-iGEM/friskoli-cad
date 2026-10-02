// Dock sizing is a local view preference, independent of the scientific document.
export function installDockSizing(studio, diagnostics) {
  const bottomMaximum = () => {
    const center = diagnostics.parentElement;
    const fixed = [...center.children].filter(item => item !== diagnostics && item.id !== 'viewport')
      .reduce((sum, item) => sum + item.getBoundingClientRect().height, 0);
    return Math.max(70, center.getBoundingClientRect().height - fixed - 120);
  };
  const config = [
    {id:'resize-left', variable:'--left-width', axis:'x', min:175, max:360, initial:232, sign:1},
    {id:'resize-right', variable:'--right-width', axis:'x', min:220, max:420, initial:282, sign:-1},
    {id:'resize-bottom', variable:'--bottom-height', axis:'y', min:110, max:bottomMaximum, initial:190, sign:-1},
  ];
  for (const spec of config) {
    const handle = document.getElementById(spec.id), target = spec.axis === 'y' ? diagnostics : studio;
    let value = spec.initial;
    try { const saved=Number(localStorage.getItem('friskoli.'+spec.id)); if(Number.isFinite(saved)&&saved>=spec.min)value=saved; } catch {}
    const apply = next => {
      const max = typeof spec.max === 'function' ? spec.max() : spec.max;
      const min = Math.min(spec.min, max);
      value=Math.max(min,Math.min(max,next));
      target.style.setProperty(spec.variable,value+'px');
      handle.setAttribute('aria-valuenow',Math.round(value));
      handle.setAttribute('aria-valuemin',Math.round(min));
      handle.setAttribute('aria-valuemax',Math.round(max));
    };
    apply(value);
    if (spec.axis === 'y') {
      // Recheck available space when the window or the replay timeline changes.
      const observer = new ResizeObserver(() => apply(value));
      observer.observe(diagnostics.parentElement);
      const timeline = document.getElementById('bottom-dock');
      if (timeline) observer.observe(timeline);
    }
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
