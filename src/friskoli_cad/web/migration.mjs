import { readWorkspace, WORKSPACE_VERSION } from './workspace.mjs';
import { unavailableModules } from './catalog.mjs';

// Import changes editor metadata only. No module versions, edges or numerical parameters change.
export function adaptWorkspace(document, modules) {
  const state = readWorkspace(document);
  const source = document.workspace_format_version ?? 'project-only';
  const changes = [];
  if (source !== WORKSPACE_VERSION) changes.push({code:'workspace.metadata',path:'/workspace_format_version',
    message:'Editor metadata will be saved as ' + WORKSPACE_VERSION + '; executable project and timing are unchanged.'});
  if (!document.population_blocks) changes.push({code:'workspace.blocks',path:'/population_blocks',
    message:'Editable volume handles inferred from existing cells; cell positions have not been regenerated.'});
  const issues = unavailableModules(state.project.graph, modules).map(node => ({code:'module.missing',
    severity:'error',path:'/graph/nodes/' + state.project.graph.nodes.indexOf(node),
    node_id:node.id,message:node.module_id + '@' + node.module_version + ': preserved for reading; run is disabled.'}));
  return {state, report:{adapter_version:'0.1.0',source_version:source,target_version:WORKSPACE_VERSION,
    execution_semantics:'legacy-explicit-v1',changes,issues}};
}
