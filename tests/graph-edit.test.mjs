import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { test } from 'node:test';
import { addNode, connect, connectionProblem, disconnect, missingInputs, preferredTiming, removeNode,
  setParameter } from '../src/friskoli_cad/web/graph-edit.mjs';

const dir = new URL('../src/friskoli_cad/engine/manifests/', import.meta.url);
const modules = new Map(readdirSync(dir).filter(name => name.endsWith('.json')).map(name => {
  const manifest = JSON.parse(readFileSync(new URL(name, dir)));
  return [`${manifest.id}@${manifest.version}`, manifest];
}));
const get = id => modules.get(`${id}@1.0.0`);
const graph = () => JSON.parse(readFileSync(new URL('../src/friskoli_cad/examples/workspace_3d.project.json',
  import.meta.url))).graph;
const group1 = { kind: 'population', id: 'group_1' };
const port = (node, name) => ({ node, port: name });

test('connections follow backend port, owner, and fan-in rules', () => {
  const g = graph();
  assert.equal(connectionProblem(g, modules, port('elongation', 'length'), port('adder', 'length'), 'same_step'),
    'edge.multiple_inputs');
  assert.equal(disconnect(g, 'growth_to_adder'), true);
  assert.deepEqual(missingInputs(g, modules), [port('adder', 'length')]);
  assert.equal(connectionProblem(g, modules, port('elongation', 'diameter'), port('adder', 'length'), 'same_step'),
    'edge.type');
  const other = addNode(g, get('growth.linear_elongation'), { kind: 'population', id: 'group_2' });
  assert.equal(connectionProblem(g, modules, port(other.id, 'length'), port('adder', 'length'), 'same_step'),
    'edge.population');
  assert.equal(connect(g, modules, port('elongation', 'length'), port('adder', 'length')).timing, 'same_step');
  assert.deepEqual(missingInputs(g, modules), []);
  assert.throws(() => addNode(g, get('field.diffusion_no_flux'), group1), /node.scope/);
});

test('a same-step cycle falls back to a previous-step edge', () => {
  const g = graph();
  const second = addNode(g, get('division.length_adder'), group1);
  connect(g, modules, port('adder', 'observed_length'), port(second.id, 'length'));
  disconnect(g, 'growth_to_adder');
  const back = [port(second.id, 'observed_length'), port('adder', 'length')];
  assert.equal(connectionProblem(g, modules, ...back, 'same_step'), 'edge.cycle');
  assert.equal(preferredTiming(g, modules, ...back), 'previous_step');
  assert.equal(removeNode(g, second.id), true);
  assert.ok(g.edges.every(edge => edge.from.node !== second.id && edge.to.node !== second.id));
});

test('parameter edits keep declared units and reject out-of-range values', () => {
  const g = graph();
  const node = g.nodes.find(item => item.id === 'elongation');
  const manifest = get('growth.linear_elongation');
  assert.equal(setParameter(node, manifest, 'elongation_rate', '-1'), 'parameter.range');
  assert.equal(setParameter(node, manifest, 'elongation_rate', 'abc'), 'parameter.type');
  assert.equal(node.parameters.elongation_rate.value, 0.5);
  assert.equal(setParameter(node, manifest, 'elongation_rate', '0.8'), null);
  assert.deepEqual(node.parameters.elongation_rate, { value: 0.8, unit: 'um/s',
    provenance: { kind: 'user', reference: 'set in Friskoli-CAD' } });
  assert.equal(setParameter(node, manifest, 'missing', 1), 'parameter.set');
  const sampler = addNode(g, get('field.sample_nearest'), group1, ['oxygen']);
  assert.equal(sampler.parameters.species.value, 'oxygen');
});
