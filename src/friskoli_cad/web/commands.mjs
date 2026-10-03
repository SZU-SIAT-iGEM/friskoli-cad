// Every entry point checks the same capability immediately before execution.
export class CommandRegistry {
  constructor(context) { this.context = context; this.entries = new Map(); }
  register(id, definition) { if (this.entries.has(id)) throw Error(`Duplicate command: ${id}`); this.entries.set(id, definition); return this; }
  inspect(id, argument) {
    const command = this.entries.get(id);
    if (!command) return { enabled:false, reason:'Unknown command' };
    const reason = command.reason?.(this.context(), argument) ?? '';
    return { enabled:!reason, reason, label:command.label };
  }
  execute(id, argument) { const capability=this.inspect(id,argument); if(!capability.enabled)return false; return this.entries.get(id).run(this.context(),argument); }
  bind(button,id,argument) {
    const refresh=()=>{const value=this.inspect(id,argument);button.disabled=!value.enabled;button.title=value.reason;button.dataset.commandId=id;};
    refresh();button.addEventListener('click',()=>{refresh();if(!button.disabled)this.execute(id,argument);});return button;
  }
}

export function graphMutationReason(project, blocks, selection, requiredRole = () => null) {
  if(!project||!selection)return 'selection';
  const nodes=project.graph.nodes;
  const edge=selection.kind==='edge'?project.graph.edges.find(e=>e.id===selection.id):null;
  const affected=selection.kind==='node'?nodes.filter(n=>n.id===selection.id):nodes.filter(n=>[edge?.from.node,edge?.to.node].includes(n.id));
  if(!affected.length)return 'selection';
  if(affected.some(n=>n.owner?.kind==='population'&&blocks.find(b=>b.id===n.owner.id)?.locked))return 'locked';
  if(selection.kind==='node'){const role=requiredRole(selection.id);if(role)return `role:${role}`;}
  return '';
}

export const settingControl = target => {
  const key=target.parameter;
  if(['dt_s','steps'].includes(key))return key==='dt_s'?'dt-input':'steps-input';
  if(key==='field_stride_xyz')return `setting-field_stride_xyz-${target.axis??0}`;
  return ['max_cells','backend','include_fields','include_final_fields','frame_every_steps','seed'].includes(key)?`setting-${key}`:null;
};
