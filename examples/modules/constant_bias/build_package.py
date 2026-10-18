"""Build a distributable module package without importing its Python code."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from friskoli_cad.packages import digest
from friskoli_cad.protocol.task_validation import canonical_bytes


def build_package(destination):
    folder = Path(__file__).parent
    members = {name: (folder / name).read_bytes() for name in ('module.json', 'module.py')}
    members['module.json'] = canonical_bytes(json.loads(members['module.json']))
    members['LICENSE'] = (folder.parents[2] / 'LICENSE').read_bytes()
    package = {
        'package_version': '0.1.0', 'id': 'tutorial', 'version': '1.0.0',
        'api_version': '0.1.0', 'kind': 'executable', 'license': 'MIT',
        'source': 'Friskoli-CAD module tutorial: examples/modules/constant_bias',
        'dependencies': {}, 'files': {name: digest(data) for name, data in members.items()},
        'modules': [{'manifest': 'module.json', 'implementation': 'module.py', 'factory': 'create'}],
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, 'w', ZIP_DEFLATED) as archive:
        for name, data in {**members, 'package.json': canonical_bytes(package)}.items():
            archive.writestr(name, data)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('runs/tutorial/constant-bias.zip'))
    args = parser.parse_args()
    path = build_package(args.out)
    print(f'{path}\nSHA-256: {digest(path.read_bytes())}')
