"""The formal engine must load and run with every retired import blocked."""
import os
from pathlib import Path
import subprocess
import sys


def test_modular_engine_has_no_legacy_import_dependencies():
    script = '''
import importlib.abc, sys
retired = {'runtime', 'modules', 'pts_modules', 'pts_runtime', 'spatial_modules',
           'spatial_runtime', 'spatial_checkpoint', 'chemotaxis_modules',
           'chemotaxis_runtime', 'chemotaxis_checkpoint'}
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name.startswith('friskoli_cad.engine.') and name.rsplit('.', 1)[-1] in retired:
            raise ImportError('Retired implementation imported: ' + name)
sys.meta_path.insert(0, Block())
from friskoli_cad.engine import modular_registry
from friskoli_cad.engine.science_extensions import make_modular_example
from friskoli_cad.project import simulation_from_project
from friskoli_cad.engine.modular_checkpoint import export_checkpoint, restore_checkpoint
p = make_modular_example()
s = simulation_from_project(p)
s.step(.1)
r = restore_checkpoint(p, export_checkpoint(s))
assert r.current.cell_frame == s.current.cell_frame
assert modular_registry().catalog['execution_semantics'] == 'modular-spatial-v1'
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    subprocess.run([sys.executable, '-c', script], env=env, check=True, capture_output=True, text=True)
