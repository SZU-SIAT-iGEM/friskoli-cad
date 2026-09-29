// Adapters are editor capabilities, not a fixed list of biological modules.
import { addNode } from './graph-edit.mjs';
import { scatterBlock } from './population.mjs';

export function availablePlaceables(modules, capabilities = null, objects = new Map()) {
  return [...objects.values()].map(object => {
    const supported = object.kind === 'population' && object.initializer.adapter === 'population.block@1';
    const present = [object.initializer.module, ...object.initializer.data_modules].every(key => modules.has(key));
    const enabled = !capabilities || capabilities.placeables.some(p => p.kind === object.kind && p.module === object.initializer.module);
    return {...object, hint:object.description, module:object.initializer.module, tool:supported ? 'population' : null,
      status:!supported ? 'unsupported' : present && enabled ? 'ready' : 'missing'};
  });
}

export function objectForBlock(block, objects) {
  return block.object_type ? objects.get(block.object_type) : [...objects.values()].find(object =>
    object.kind === 'population' && object.initializer.adapter === 'population.block@1');
}

// Initialization and added readers belong to one undoable document transaction.
// A binding prevents re-scatter from recreating readers deliberately deleted by the user.
export function initializeObject(project, block, object, modules) {
  if (!object || object.initializer.adapter !== 'population.block@1') throw new Error('Unsupported object initializer');
  const keys = [object.initializer.module, ...object.initializer.data_modules];
  for (const key of keys) {
    const manifest = modules.get(key);
    if (!manifest || manifest.scope !== 'population' || Object.keys(manifest.parameters).length ||
        Object.keys(manifest.inputs).length) throw new Error('Unsupported initializer contract: ' + key);
  }
  const nextProject = structuredClone(project), nextBlock = structuredClone(block);
  scatterBlock(nextProject, nextBlock, modules.get(object.initializer.module));
  if (!nextBlock.binding) {
    const data = [];
    for (const key of object.initializer.data_modules) {
      const manifest = modules.get(key);
      const node = nextProject.graph.nodes.find(n => n.owner.kind === 'population' && n.owner.id === block.id &&
        n.module_id === manifest.id && n.module_version === manifest.version) ??
        addNode(nextProject.graph, manifest, {kind:'population',id:block.id});
      data.push(node.id);
    }
    nextBlock.binding = {data_nodes:data};
  }
  nextBlock.object_type = object.id;
  Object.assign(project, nextProject); Object.assign(block, nextBlock);
  return project.groups[block.id];
}
