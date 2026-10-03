from copy import deepcopy
import json
from pathlib import Path
import unittest

import numpy as np

from friskoli_cad.protocol import ProtocolError, validate_graph, validate_manifest
from friskoli_cad.engine.compiler import compile_graph
from friskoli_cad.engine.module_api import StepContext, ModuleProposal, Effect, execute_module
from friskoli_cad.engine.execution_planner import plan_execution
from friskoli_cad.engine.runtime import ModuleRegistry
from friskoli_cad.diagnostics import diagnose_project


def manifest(identifier='demo.scalar', *, scope='environment'):
    return {'protocol_version': '0.2.0', 'id': identifier, 'version': '1.0.0', 'scope': scope,
            'phase': 3, 'scientific_role': 'closure', 'maturity': 'exploratory',
            'inputs': {}, 'outputs': {'value': {'shape': 'global.scalar', 'quantity': 'scalar', 'unit': '1'}},
            'parameters': {}, 'state': {}, 'initial_outputs': ['value']}


class Constant:
    world_access = 'read_only'

    def __init__(self, identifier='demo.scalar', *, stage='prepare', backends=('numpy-cpu',)):
        self.manifest = manifest(identifier)
        self.execution_contract = {'api_version': '0.1.0', 'stage': stage, 'reads': [],
                                   'writes': [], 'effects': [], 'backends': list(backends)}

    def initialize(self, context):
        return self.propose(context)

    def propose(self, context):
        return ModuleProposal({'value': 7.}, {})


def graph_for(*modules):
    return {'protocol_version': '0.2.0', 'id': 'module-test', 'nodes': [
        {'id': f'n{i}', 'module_id': m.manifest['id'], 'module_version': '1.0.0',
         'owner': {'kind': m.manifest['scope'], 'id': f'object{i}'}, 'parameters': {}}
        for i, m in enumerate(modules)], 'edges': []}


class ModuleSystemTests(unittest.TestCase):
    def test_context_detaches_mutable_inputs_and_proposals_are_checked(self):
        source = np.array([1., 2.])
        context = StepContext(0., .1, 0, 'x', ('a', 'b'), inputs={'value': source})
        source[0] = 9.
        np.testing.assert_array_equal(context.inputs['value'], [1., 2.])
        with self.assertRaises(ValueError):
            context.inputs['value'][0] = 3.
        with self.assertRaises(TypeError):
            context.inputs['x'] = 3
        self.assertEqual(execute_module(Constant(), StepContext(0., .1, 0, 'x', ())).outputs['value'], 7.)

    def test_undeclared_reads_effects_targets_and_nonfinite_fail(self):
        module = Constant()
        with self.assertRaisesRegex(ProtocolError, 'module.read_access'):
            execute_module(module, StepContext(0., 1., 0, 'x', (), world={'secret': 2.}))
        module.propose = lambda c: ModuleProposal({'value': 1.}, {}, (Effect('field.delta', 'wrong', 1.),))
        with self.assertRaisesRegex(ProtocolError, 'module.effect_access'):
            execute_module(module, StepContext(0., 1., 0, 'x', ()))
        module.execution_contract.update(effects=['field.delta'], effect_targets={'field.delta': ['$owner']})
        with self.assertRaisesRegex(ProtocolError, 'module.effect_target'):
            execute_module(module, StepContext(0., 1., 0, 'x', ()))
        with self.assertRaisesRegex(ProtocolError, 'module.non_finite'):
            ModuleProposal({'value': float('nan')}, {})

    def test_record_size_is_bounded_and_declared_reads_are_required(self):
        from friskoli_cad.engine.port_semantics import RECORD_BYTE_LIMIT, validate_record
        sample = {'unicode': '养分', 'empty': [], 'values': np.array([1., 2.])}
        encoded = {'unicode': '养分', 'empty': [], 'values': [1., 2.]}
        self.assertEqual(validate_record(sample, '/output'),
                         len(json.dumps(encoded, separators=(',', ':'), ensure_ascii=False).encode('utf-8')))
        with self.assertRaisesRegex(ProtocolError, 'module.record_size'):
            validate_record({'history': 'x' * RECORD_BYTE_LIMIT}, '/state/history')
        module = Constant()
        module.execution_contract['reads'] = ['positions_um']
        with self.assertRaisesRegex(ProtocolError, 'module.read_access'):
            execute_module(module, StepContext(0., 1., 0, 'x', ()))

    def test_tensor_dtype_and_cross_population_identity_are_not_shape_guesses(self):
        a, b = Constant('demo.source'), Constant('demo.target')
        for module in (a, b):
            module.manifest['scope'] = 'population'
            module.manifest['outputs']['value'].update(shape='cell.tensor', tensor_shape=[2, 2], dtype='float64')
        b.manifest['inputs']['value'] = deepcopy(a.manifest['outputs']['value'])
        graph = graph_for(a, b)
        graph['edges'] = [{'id': 'edge', 'from': {'node': 'n0', 'port': 'value'}, 'to': {'node': 'n1', 'port': 'value'}, 'timing': 'same_step'}]
        with self.assertRaisesRegex(ProtocolError, 'edge.population'):
            validate_graph(graph, [a.manifest, b.manifest])
        graph['nodes'][1]['owner'] = graph['nodes'][0]['owner']
        validate_graph(graph, [a.manifest, b.manifest])
        b.manifest['inputs']['value']['temporal'] = 'interval_amount'
        with self.assertRaisesRegex(ProtocolError, 'edge.semantic_type'):
            validate_graph(graph, [a.manifest, b.manifest])
        a.propose = lambda c: ModuleProposal({'value': np.zeros((1, 2, 2), dtype=np.float32)}, {})
        with self.assertRaisesRegex(ProtocolError, 'module.dtype'):
            execute_module(a, StepContext(0., 1., 0, 'object0', ('cell',)))

    def test_structured_parameters_compile_to_an_immutable_snapshot(self):
        module = Constant()
        module.manifest['parameters']['table'] = {'type': 'array', 'items': {'type': 'object', 'required': ['x'], 'properties': {'x': {'type': 'number'}}, 'additionalProperties': False}}
        graph = graph_for(module)
        graph['nodes'][0]['parameters']['table'] = {'value': [{'x': 2.}], 'provenance': {'kind': 'example', 'reference': 'constructed test'}}
        plan = compile_graph(graph, [module.manifest])
        graph['nodes'][0]['parameters']['table']['value'][0]['x'] = 9.
        self.assertEqual(plan.nodes[0].parameters['table'].value[0]['x'], 2.)
        with self.assertRaises(TypeError):
            plan.nodes[0].parameters['table'].value[0]['x'] = 3.
        graph['nodes'][0]['parameters']['table']['value'][0]['x'] = 'wrong'
        with self.assertRaisesRegex(ProtocolError, 'parameter.schema'):
            compile_graph(graph, [module.manifest])

    def test_explicit_mapping_input_resolves_default_source_entity_set(self):
        from friskoli_cad.engine.standard_modules import standard_modules
        source = Constant('demo.cells')
        source.manifest['scope'] = 'population'
        source.manifest['outputs']['value']['shape'] = 'cell.scalar'
        mapping = next(m for m in standard_modules() if m.manifest['id'] == 'mapping.scalar_by_id')
        graph = graph_for(source, mapping)
        provenance = {'kind': 'example', 'reference': 'explicit identity mapping test'}
        graph['nodes'][1]['parameters'] = {
            'source_population': {'value': 'population:object0', 'provenance': provenance},
            'mapping': {'value': {'target': 'source'}, 'provenance': provenance}}
        graph['edges'] = [{'id': 'mapping', 'from': {'node': 'n0', 'port': 'value'},
                           'to': {'node': 'n1', 'port': 'value'}, 'timing': 'same_step'}]
        validate_graph(graph, [source.manifest, mapping.manifest])
        graph['nodes'][1]['parameters']['source_population']['value'] = 'population:wrong'
        with self.assertRaisesRegex(ProtocolError, 'edge.semantic_type'):
            validate_graph(graph, [source.manifest, mapping.manifest])

    def test_planner_dispatches_declared_implementations_and_exclusive_owners(self):
        a = Constant('demo.cpu')
        b = Constant('demo.cuda', backends=('numpy-cpu', 'numpy-cupy-cuda'))
        registry = ModuleRegistry([a, b], 'modular-spatial-v1')
        graph = graph_for(a, b)
        result = plan_execution(compile_graph(graph, registry.manifests), registry, backend='numpy-cupy-cuda')
        self.assertEqual(result['node_backends'], {'n0': 'numpy-cpu', 'n1': 'numpy-cupy-cuda'})
        self.assertEqual(result['transfers'][0]['node_id'], 'n1')
        self.assertEqual(result['memory']['numeric_outputs_bytes'], 16)
        a.execution_contract['workspace_bytes'] = {'fixed': 4096, 'per_voxel': 64}
        with_scratch = plan_execution(compile_graph(graph, registry.manifests), registry,
                                     world_sizes={'voxels': 8})
        self.assertEqual(with_scratch['memory']['declared_workspace_bytes'], 4096 + 8 * 64)
        a.execution_contract['workspace_bytes']['fixed'] = -1
        with self.assertRaisesRegex(ProtocolError, 'module.workspace'):
            plan_execution(compile_graph(graph, registry.manifests), registry)
        a.execution_contract.pop('workspace_bytes')
        a.execution_contract['writes'] = b.execution_contract['writes'] = ['inventory']
        graph['nodes'][1]['owner'] = graph['nodes'][0]['owner']
        with self.assertRaisesRegex(ProtocolError, 'module.owner_conflict'):
            plan_execution(compile_graph(graph, registry.manifests), registry)

    def test_planner_does_not_reorder_an_illegal_stage_edge(self):
        a, b = Constant('demo.late', stage='physiology'), Constant('demo.early')
        b.manifest['inputs']['value'] = deepcopy(a.manifest['outputs']['value'])
        graph = graph_for(a, b)
        graph['edges'].append({'id': 'edge', 'from': {'node': 'n0', 'port': 'value'}, 'to': {'node': 'n1', 'port': 'value'}, 'timing': 'same_step'})
        registry = ModuleRegistry([a, b], 'modular-spatial-v1')
        with self.assertRaisesRegex(ProtocolError, 'planner.stage_dependency'):
            plan_execution(compile_graph(graph, registry.manifests), registry)

    def test_shared_uptake_is_read_only_after_settlement_or_with_explicit_delay(self):
        from friskoli_cad.engine.science_extensions import ScientificModule, modular_registry
        from friskoli_cad.project import simulation_from_project

        project = json.loads((Path(__file__).parents[1] /
            'src/friskoli_cad/examples/modular_foundation.project.json').read_text(encoding='utf-8'))
        project['species']['nutrient']['initial_concentration']['value'] = 3.
        builtin = modular_registry()
        uptake_node = next(node for node in project['graph']['nodes']
                           if node['module_id'] == 'uptake.local_settlement')
        uptake = builtin.get(uptake_node['module_id'], uptake_node['module_version'])
        amount_port = deepcopy(uptake.manifest['outputs']['accepted_amount'])
        reader = ScientificModule('test.accepted_reader', 'Accepted uptake reader', 'field', 'population',
            {'amount': amount_port}, {'value': amount_port},
            {'species': deepcopy(uptake.manifest['parameters']['species'])}, {}, 'y=x',
            'Read the actual shared settlement, not the uncommitted proposal.',
            lambda context, initial: ModuleProposal({'value': np.zeros(len(context.entity_ids))
                if initial else context.inputs['amount']}, {}))
        registry = ModuleRegistry([
            builtin.get(m['id'], m['version']) for m in builtin.manifests] + [reader], 'modular-spatial-v1')
        project['graph']['nodes'].append({'id': 'accepted_reader', 'module_id': reader.manifest['id'],
            'module_version': '1.0.0', 'owner': deepcopy(uptake_node['owner']),
            'parameters': {'species': deepcopy(uptake_node['parameters']['species'])}})
        binding = {'id': 'accepted_reader_input', 'from': {'node': uptake_node['id'], 'port': 'accepted_amount'},
                   'to': {'node': 'accepted_reader', 'port': 'amount'}, 'timing': 'same_step'}
        project['graph']['edges'].append(binding)
        with self.assertRaisesRegex(ProtocolError, 'planner.unsettled_output'):
            simulation_from_project(project, registry)

        reader.execution_contract['stage'] = 'physiology'
        reader.manifest['phase'] = 8
        sim = simulation_from_project(project, registry)
        sim.step(.01)
        accepted = sim.outputs[uptake_node['id']]['accepted_amount']
        self.assertGreater(float(np.sum(accepted)), 0.)
        np.testing.assert_array_equal(sim.outputs['accepted_reader']['value'], accepted)

        reader.execution_contract['stage'] = 'field'
        reader.manifest['phase'] = 4
        binding['timing'] = 'previous_step'
        delayed = simulation_from_project(project, registry)
        delayed.step(.01)
        np.testing.assert_array_equal(delayed.outputs['accepted_reader']['value'], np.zeros_like(accepted))
        previous = delayed.outputs[uptake_node['id']]['accepted_amount'].copy()
        delayed.step(.01)
        np.testing.assert_array_equal(delayed.outputs['accepted_reader']['value'], previous)

    def test_effect_after_its_system_settlement_boundary_is_rejected(self):
        from friskoli_cad.engine.science_extensions import modular_registry
        from friskoli_cad.project import validate_project

        project = json.loads((Path(__file__).parents[1] /
            'src/friskoli_cad/examples/modular_foundation.project.json').read_text(encoding='utf-8'))
        registry = modular_registry()
        movement = registry.get('motion.hazard_run_tumble', '1.0.0')
        movement.execution_contract['stage'] = 'observation'
        movement.manifest['phase'] = 20
        with self.assertRaisesRegex(ProtocolError, 'modular.effect_stage'):
            validate_project(project, registry.manifests, registry=registry)

    def test_independent_errors_are_collected_without_derivative_missing_edges(self):
        from friskoli_cad.engine.profiles import registry_for_project
        project = json.loads((Path(__file__).parents[1] / 'src/friskoli_cad/examples/workspace_3d.project.json').read_text(encoding='utf-8'))
        registry = registry_for_project(project)
        # Two independent wrong parameters survive one diagnostic request.
        targets = [(node, name) for node in project['graph']['nodes'] for name, p in node['parameters'].items() if isinstance(p['value'], (int, float))]
        self.assertGreaterEqual(len(targets), 2)
        for node, name in targets[:2]:
            node['parameters'][name]['value'] = 'incorrect'
        result = diagnose_project(project, registry)
        self.assertFalse(result['valid'])
        self.assertEqual(sum(x['code'] == 'parameter.type' for x in result['issues']), 2)
        self.assertFalse(any(x['code'] == 'edge.required' for x in result['issues']))
        self.assertTrue(any(x['status'] == 'skipped' for x in result['checks']))


if __name__ == '__main__':
    unittest.main()
