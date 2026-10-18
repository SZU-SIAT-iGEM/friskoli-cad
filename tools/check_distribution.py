"""Check bundled assets and execute an installed wheel outside the checkout."""
import argparse
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import venv
from zipfile import ZipFile


def check_distribution(folder, *, install=True):
    folder = Path(folder).resolve()
    wheels, sources = sorted(folder.glob('*.whl')), sorted(folder.glob('*.tar.gz'))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError('Use a distribution directory containing exactly one wheel and one source archive')
    root = Path(__file__).resolve().parents[1]
    with ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        for path in (root / 'src/friskoli_cad/web').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                member = path.relative_to(root / 'src').as_posix()
                if member not in names or archive.read(member) != path.read_bytes():
                    raise ValueError(f'Wheel is missing or changes a web asset: {member}')
        for path in (root / 'src/friskoli_cad/examples').glob('*.json'):
            member = path.relative_to(root / 'src').as_posix()
            if member not in names:
                raise ValueError(f'Wheel is missing a preset: {member}')
    with tarfile.open(sources[0]) as archive:
        names = {name.split('/', 1)[-1] for name in archive.getnames()}
        for path in ('docs/USER_GUIDE.md', 'docs/USER_GUIDE.zh-CN.md', 'docs/EXTENDING.md',
                     'examples/modules/constant_bias/module.py', 'examples/design/compare_speeds.py', 'research/pts_assay.py',
                     'research/wiki_signal.py', 'research/wiki_spatial.py', 'research/wiki_report.py',
                     'docs/WIKI_STUDIES.md', 'docs/WIKI_MODEL_EVIDENCE.md'):
            if path not in names:
                raise ValueError(f'Source archive is missing a documented example: {path}')
        if any(name.startswith(('archive/', 'runs/', 'outputs/', 'node_modules/')) for name in names):
            raise ValueError('Source archive contains generated or archived files')
    if install:
        with tempfile.TemporaryDirectory(prefix='friskoli-wheel-') as directory:
            environment = Path(directory) / 'environment'
            venv.EnvBuilder(with_pip=True).create(environment)
            python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            subprocess.run([str(python), '-m', 'pip', 'install', str(wheels[0])], cwd=directory, check=True)
            smoke = (
                'from importlib.resources import files; '
                'from friskoli_cad.engine.presets import make_example; '
                'from friskoli_cad.project import simulation_from_project; '
                'root=files("friskoli_cad"); '
                'assert root.joinpath("web/vendor/katex/fonts/OFL.txt").is_file(); '
                'assert root.joinpath("web/vendor/three/build/three.module.js").is_file(); '
                'simulation=simulation_from_project(make_example()); simulation.step(.01); '
                'assert simulation.frame_index==1; print("Installed wheel: preset, step and local assets passed")'
            )
            subprocess.run([str(python), '-I', '-c', smoke], cwd=directory, check=True)
    print('Wheel assets and source archive passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=Path('dist'))
    parser.add_argument('--no-install', action='store_true', help='inspect archives without the isolated installation check')
    args = parser.parse_args()
    check_distribution(args.directory, install=not args.no_install)
