import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from friskoli_cad.packages import PackageStore, inspect_package, digest
from friskoli_cad.protocol import ProtocolError
from friskoli_cad.protocol.task_validation import canonical_bytes
from friskoli_cad.engine.module_registry import ModuleRegistry
from friskoli_cad.engine.module_api import StepContext, execute_module


def bundle(identifier='demo', version='1.0.0', dependencies=None, code=None, extra=None, module_manifest=None):
    manifest = {'protocol_version': '0.2.0', 'id': identifier + '.constant', 'version': version,
                'scope': 'environment', 'phase': 3, 'scientific_role': 'closure', 'maturity': 'exploratory',
                'inputs': {}, 'outputs': {'value': {'shape': 'global.scalar', 'quantity': 'scalar', 'unit': '1'}},
                'parameters': {}, 'state': {}, 'initial_outputs': ['value']}
    manifest = module_manifest or manifest
    files = {'module.json': canonical_bytes(manifest), **(extra or {})}
    if code is not None:
        files['module.py'] = code.encode()
    package = {'package_version': '0.1.0', 'id': identifier, 'version': version, 'api_version': '0.1.0',
               'kind': 'executable' if code is not None else 'data', 'license': 'MIT', 'source': 'local test fixture',
               'dependencies': dependencies or {}, 'files': {name: digest(value) for name, value in files.items()},
               'modules': [{'manifest': 'module.json', 'implementation': 'module.py' if code is not None else None,
                            'factory': 'create' if code is not None else None}]}
    files['package.json'] = canonical_bytes(package)
    buffer = io.BytesIO()
    with ZipFile(buffer, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


CODE = '''
import json
from pathlib import Path
from friskoli_cad.engine.module_api import ModuleProposal
class Installed:
    world_access = 'read_only'
    manifest = json.loads(Path(__file__).with_name('module.json').read_text())
    execution_contract = dict(api_version='0.1.0', stage='prepare', reads=[], writes=[], effects=[], backends=['numpy-cpu'])
    def initialize(self, context): return self.propose(context)
    def propose(self, context): return ModuleProposal({'value': 17.}, {})
def create(): return Installed()
'''


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PackageStore(Path(self.temp.name) / 'store')

    def install(self, data):
        return self.store.install(data, expected_sha256=digest(data))

    def test_preview_and_install_do_not_execute_but_locked_registry_does(self):
        marker = Path(self.temp.name) / 'execution.txt'
        data = bundle(code=CODE + '\nPath(' + repr(str(marker)) + ').write_text("executed")\n')
        preview = inspect_package(data)[0]
        self.assertFalse(preview['executes_on_install'])
        self.install(data)
        self.assertFalse(marker.exists())
        lock = self.store.resolve({'demo': '1.0.0'})
        registry = self.store.extend_registry(ModuleRegistry([], 'modular-spatial-v1'), lock)
        self.assertTrue(marker.exists())
        result = execute_module(registry.get('demo.constant', '1.0.0'), StepContext(0., .1, 0, 'world', ()))
        self.assertEqual(result.outputs['value'], 17.)

    def test_preview_hash_identity_and_installed_tamper_are_rejected(self):
        data = bundle(code=CODE)
        with self.assertRaisesRegex(ProtocolError, 'package.preview_changed'):
            self.store.install(data, expected_sha256='0' * 64)
        self.install(data)
        with self.assertRaisesRegex(ProtocolError, 'package.conflict'):
            self.install(bundle(code=CODE + '\n# changed'))
        lock = self.store.resolve({'demo': '1.0.0'})
        (self.store.root / 'demo/1.0.0/content/module.py').write_text('tampered')
        with self.assertRaisesRegex(ProtocolError, 'package.installed_hash'):
            self.store.verify_lock(lock)

    def test_version_constraints_are_solved_together_and_project_pins_protect_uninstall(self):
        self.install(bundle('dep', '1.0.0'))
        self.install(bundle('dep', '2.0.0'))
        self.install(bundle('consumer', dependencies={'dep': '>=1.0.0,<2.0.0'}))
        lock = self.store.resolve({'consumer': '1.0.0'}, pin_id='projectA')
        self.assertEqual([(p['id'], p['version']) for p in lock['packages']], [('dep', '1.0.0'), ('consumer', '1.0.0')])
        with self.assertRaisesRegex(ProtocolError, 'package.in_use'):
            self.store.uninstall('consumer', '1.0.0')
        with self.assertRaisesRegex(ProtocolError, 'package.unresolved'):
            self.store.resolve({'consumer': '1.0.0', 'dep': '2.0.0'})
        self.assertTrue(self.store.uninstall('dep', '2.0.0'))

    def test_missing_dependencies_cycles_and_namespace_conflicts_fail_before_execution(self):
        with self.assertRaisesRegex(ProtocolError, 'package.unresolved'):
            self.install(bundle(dependencies={'missing': '1.0.0'}))
        first = inspect_package(bundle('first', dependencies={'second': '1.0.0'}))[0]
        second = inspect_package(bundle('second', dependencies={'first': '1.0.0'}))[0]
        with self.assertRaisesRegex(ProtocolError, 'package.cycle'):
            self.store.resolve({'first': '1.0.0'}, candidates=[first, second])

    def test_nonportable_and_traversal_paths_are_rejected(self):
        for name in ('../escape', '/absolute', 'C:/drive', 'back\\slash', 'CON.txt', 'trailing. ', 'dot/../path',
                     'bad?.json', 'bad|name.json', 'new\nline.json'):
            with self.subTest(name=name), self.assertRaisesRegex(ProtocolError, 'package.path'):
                inspect_package(bundle(extra={name: b'test'}))

    def test_data_package_never_adds_executable_modules(self):
        self.install(bundle())
        registry = self.store.extend_registry(ModuleRegistry([], 'modular-spatial-v1'), self.store.resolve({'demo': '1.0.0'}))
        self.assertEqual(registry.manifests, ())

    def test_package_members_can_be_used_without_executing_code(self):
        marker = Path(self.temp.name) / 'should-not-run.txt'
        data = bundle(code=CODE + '\nPath(' + repr(str(marker)) + ').write_text("executed")\n',
                      extra={'parameters.json': b'{"value": 2}'})
        self.install(data)
        self.assertEqual(self.store.read_file('demo', '1.0.0', 'parameters.json', expected_sha256=digest(data)),
                         b'{"value": 2}')
        self.assertFalse(marker.exists())
        with self.assertRaisesRegex(ProtocolError, 'package.preview_changed'):
            self.store.read_file('demo', '1.0.0', 'parameters.json', expected_sha256='0' * 64)
        with self.assertRaisesRegex(ProtocolError, 'package.file'):
            self.store.read_file('demo', '1.0.0', 'missing.json', expected_sha256=digest(data))

    def test_fresh_process_loads_locked_plugin_into_a_real_modular_step(self):
        from friskoli_cad.engine.science_extensions import modular_registry
        manifest = json.loads(json.dumps(modular_registry().get('signal.constant_bias', '1.0.0').manifest))
        manifest.update(id='demo.motor')
        manifest['parameters'] = {}
        code = CODE.replace("return ModuleProposal({'value': 17.}, {})",
                            "return ModuleProposal({'motor_bias': __import__('numpy').full(len(context.entity_ids), .234)}, {})")
        self.install(bundle(code=code, module_manifest=manifest))
        project = json.loads((Path(__file__).parents[1] / 'src/friskoli_cad/examples/foundation_control.project.json').read_text(encoding='utf-8'))
        project.update(project_version='0.6.0', execution_profile='modular-spatial-v1', dependency_lock=self.store.resolve({'demo': '1.0.0'}))
        project['graph']['protocol_version'] = '0.2.0'
        node = next(n for n in project['graph']['nodes'] if n['id'] == 'motor_signal')
        node.update(module_id='demo.motor', parameters={})
        path = Path(self.temp.name) / 'project.json'
        path.write_bytes(canonical_bytes(project))
        script = '''import json,sys
from friskoli_cad.project import simulation_from_project
p=json.load(open(sys.argv[1],encoding='utf-8'))
s=simulation_from_project(p)
s.step(.01)
print(json.dumps({'bias':s.outputs['motor_signal']['motor_bias'].tolist(),'time_s':s.time_s}))
'''
        result = subprocess.run([sys.executable, '-c', script, str(path)], env={**os.environ, 'FRISKOLI_PACKAGE_DIR': str(self.store.root)},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        computed = json.loads(result.stdout)
        self.assertTrue(computed['bias'])
        self.assertEqual(set(computed['bias']), {.234})
        self.assertEqual(computed['time_s'], .01)


if __name__ == '__main__':
    unittest.main()
