import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { blocksFromProject, createBlock, scatterBlock } from '../src/friskoli_cad/web/population.mjs';
import { nextHit, registerModules, resolveGraph } from '../src/friskoli_cad/web/catalog.mjs';

const project = () => JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/center_pts_a_small_strong.project.json', import.meta.url)));
const staticModule = { id: 'population.static', version: '1.0.0', scope: 'population',
  inputs: {}, outputs: {}, parameters: {} };

test('population volume scatters reproducible 3D cells and registers an owner', () => {
  const document = project();
  const existing = blocksFromProject(document);
  const block = createBlock(document, [18, 12, 6], existing.map(item => item.id));
  block.count = 12;
  const first = scatterBlock(document, block, staticModule);
  assert.equal(first.ids.length, 12);
  assert.equal(new Set(first.ids).size, 12);
  assert.ok(first.positions_um.some(position => position[2] !== first.positions_um[0][2]));
  assert.ok(first.positions_um.every(([x, y, z]) => x > 0 && x < 64 && y > 0 && y < 64 && z > 0 && z < 32));
  assert.ok(first.orientation_xyzw.every(q => Math.abs(q.reduce((sum, v) => sum + v * v, 0) - 1) < .001));
  assert.equal(document.graph.nodes.at(-1).module_id, 'population.static');
  assert.ok(document.run.groups.includes(block.id));
  const previous = structuredClone(first);
  const second = scatterBlock(document, block, staticModule);
  assert.deepEqual(second, previous);
  block.count = 13;
  scatterBlock(document, block, staticModule);
  assert.deepEqual(document.groups[block.id].ids.slice(0, 12), previous.ids);
});

test('module catalog resolves exact versions and overlapping picks cycle by ID', () => {
  const modules = registerModules({ protocol_version: '0.2.0', modules: [staticModule] });
  const graph = { protocol_version: '0.2.0', nodes: [{ id: 'static', module_id: 'population.static',
    module_version: '1.0.0' }], edges: [] };
  assert.equal(resolveGraph(graph, modules).nodes[0].manifest.id, 'population.static');
  assert.equal(nextHit(['a', 'b'], 'a'), 'b');
  assert.equal(nextHit(['a', 'b'], 'b'), 'a');
  assert.throws(() => registerModules({ protocol_version: '0.2.0', modules: [staticModule, staticModule] }));
});
