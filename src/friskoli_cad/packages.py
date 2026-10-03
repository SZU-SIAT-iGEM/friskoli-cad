"""Local, content-locked data and trusted-code packages.

Inspection and installation only validate/copy data. Python factories are loaded
only by explicit executable registry resolution, after hashes and locks verify.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from zipfile import ZipFile, BadZipFile

from .protocol import ProtocolError, validate_manifest
from .protocol.task_validation import canonical_bytes, canonical_loads

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
NAME = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def fail(code, message, path="/package"):
    raise ProtocolError(code, path, message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version_tuple(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        fail("package.version", "Versions require three nonnegative integer components")
    return tuple(map(int, value.split('.')))


def matches(version, constraint):
    current = version_tuple(version)
    if not isinstance(constraint, str) or not constraint:
        fail("package.constraint", "A nonempty version constraint is required")
    for part in constraint.split(','):
        match = re.fullmatch(r"\s*(==|>=|<=|>|<)?\s*(\d+\.\d+\.\d+)\s*", part)
        if not match:
            fail("package.constraint", "Use exact versions or comma-separated ==, >=, <=, >, < constraints")
        operator, bound = match.groups()
        expected = version_tuple(bound)
        if not {None: current == expected, '==': current == expected, '>=': current >= expected,
                '<=': current <= expected, '>': current > expected, '<': current < expected}[operator]:
            return False
    return True


def _safe_path(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name
            or any(ord(char) < 32 or char in '<>"|?*' for char in name)
            or name.startswith('/') or any(part in ('', '.', '..') for part in name.split('/'))):
        fail("package.path", "Package paths must be normalized relative POSIX paths")
    reserved = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
    if any(part != part.rstrip(' .') or part.split('.')[0].casefold() in reserved for part in name.split('/')):
        fail('package.path', 'Paths must also be unambiguous on Windows')
    return PurePosixPath(name)


def inspect_package(data):
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE:
        fail("package.size", "Package archive exceeds its byte limit")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) > 1024 or len(set(name.casefold() for name in names)) != len(names):
                fail("package.members", "Duplicate/case-colliding paths or excessive member count")
            if sum(item.file_size for item in entries) > MAX_EXPANDED:
                fail("package.size", "Expanded package exceeds its byte limit")
            for item in entries:
                _safe_path(item.filename)
                if item.is_dir() or (item.external_attr >> 16) & 0o170000 == 0o120000 or item.flag_bits & 1:
                    fail("package.member", "Directories, symbolic links and encrypted members are not accepted")
            if 'package.json' not in names:
                fail("package.manifest", "Missing package.json")
            payload = {name: archive.read(name) for name in names}
    except (BadZipFile, RuntimeError, OSError) as error:
        fail("package.archive", str(error))
    description = canonical_loads(payload['package.json'])
    required = {'package_version', 'id', 'version', 'api_version', 'kind', 'license', 'source', 'dependencies', 'files', 'modules'}
    if not isinstance(description, dict) or set(description) != required:
        fail("package.manifest", "Package manifest members differ from contract 0.1.0")
    if description['package_version'] != '0.1.0' or description['api_version'] != '0.1.0':
        fail("package.api", "Unsupported package or execution API version")
    if not isinstance(description['id'], str) or not NAME.fullmatch(description['id']):
        fail("package.id", "Invalid package namespace")
    version_tuple(description['version'])
    if description['kind'] not in ('data', 'executable') or any(
            not isinstance(description[key], str) or not description[key].strip() for key in ('license', 'source')):
        fail("package.provenance", "Kind, license and source must be explicit")
    deps = description['dependencies']
    if not isinstance(deps, dict) or any(not isinstance(key, str) or not NAME.fullmatch(key) for key in deps):
        fail("package.dependencies", "Dependencies must map package names to version constraints")
    for constraint in deps.values():
        matches('0.0.0', constraint)
    files = description['files']
    if isinstance(files, dict):
        for name in files:
            _safe_path(name)
    if not isinstance(files, dict) or set(files) != set(payload) - {'package.json'}:
        fail("package.files", "Every package file must have exactly one hash declaration")
    for name, expected in files.items():
        if digest(payload[name]) != expected:
            fail("package.hash", f"Content hash differs: {name}")
    if not isinstance(description['modules'], list):
        fail("package.modules", "Module entries must be an array")
    modules, seen = [], set()
    for item in description['modules']:
        if not isinstance(item, dict) or set(item) != {'manifest', 'implementation', 'factory'}:
            fail("package.modules", "Module entry requires manifest, implementation and factory")
        if not isinstance(item['manifest'], str) or item['manifest'] not in payload or not item['manifest'].endswith('.json'):
            fail("package.modules", "Module manifest file is missing")
        manifest = canonical_loads(payload[item['manifest']])
        validate_manifest(manifest)
        if not manifest['id'].startswith(description['id'] + '.'):
            fail("package.namespace", "Module ID must belong to its package namespace")
        key = (manifest['id'], manifest['version'])
        if key in seen:
            fail("package.modules", "Module version occurs more than once")
        seen.add(key)
        if description['kind'] == 'data':
            if item['implementation'] is not None or item['factory'] is not None:
                fail("package.data_execution", "Data packages cannot declare executable factories")
        elif (not isinstance(item['implementation'], str) or item['implementation'] not in payload or not item['implementation'].endswith('.py')
              or not isinstance(item['factory'], str) or not re.fullmatch(r'[A-Za-z_]\w*', item['factory'])):
            fail("package.implementation", "Executable modules require a Python file and a named factory")
        modules.append({'id': manifest['id'], 'version': manifest['version'], 'manifest': manifest,
                        'implementation': item['implementation'], 'factory': item['factory']})
    return {'sha256': digest(data), 'bytes': len(data), 'expanded_bytes': sum(map(len, payload.values())),
            'package': description, 'modules': modules, 'executes_on_install': False}, payload


class PackageStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @classmethod
    def default(cls):
        override = os.environ.get('FRISKOLI_PACKAGE_DIR')
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share')
        return cls(override or base / 'Friskoli-CAD' / 'packages')

    def _location(self, package_id, version):
        if not isinstance(package_id, str) or not NAME.fullmatch(package_id):
            fail("package.id", "Invalid package ID")
        version_tuple(version)
        _safe_path(package_id)
        location = self.root / package_id / version
        if not location.resolve().is_relative_to(self.root):
            fail("package.path", "Resolved package path escapes the store")
        return location

    def list(self):
        result = []
        if not self.root.exists():
            return result
        for archive in sorted(self.root.glob('*/*/archive.zip')):
            preview, _ = inspect_package(archive.read_bytes())
            package = preview['package']
            if archive.parent != self._location(package['id'], package['version']):
                fail("package.path", "Installed location differs from its package identity")
            result.append(preview)
        return result

    def install(self, data, *, expected_sha256):
        preview, payload = inspect_package(data)
        if preview['sha256'] != expected_sha256:
            fail("package.preview_changed", "Package differs from the reviewed preview")
        package = preview['package']
        target = self._location(package['id'], package['version'])
        if target.exists():
            existing = target / 'archive.zip'
            if existing.is_file() and digest(existing.read_bytes()) == expected_sha256:
                return preview
            fail("package.conflict", "An installed ID/version has different content")
        # Dependency solvability is checked against installed packages plus this candidate.
        self.resolve({package['id']: package['version']}, candidates=self.list() + [preview])
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.install-', dir=target.parent))
        try:
            (staging / 'archive.zip').write_bytes(data)
            for name, content in payload.items():
                path = staging / 'content' / Path(*PurePosixPath(name).parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return preview

    def resolve(self, requirements, *, candidates=None, pin_id=None):
        if not isinstance(requirements, dict) or any(not isinstance(k, str) or not NAME.fullmatch(k) for k in requirements):
            fail("package.dependencies", "Package requirements must be a name/constraint mapping")
        for constraint in requirements.values():
            matches('0.0.0', constraint)
        available = self.list() if candidates is None else candidates
        by_id = {}
        for item in available:
            by_id.setdefault(item['package']['id'], []).append(item)
        for versions in by_id.values():
            versions.sort(key=lambda item: version_tuple(item['package']['version']), reverse=True)

        def solve(selected, constraints):
            for name, item in selected.items():
                if not all(matches(item['package']['version'], c) for c in constraints[name]):
                    return None
            pending = sorted(set(constraints) - set(selected))
            if not pending:
                return selected
            name = pending[0]
            for item in by_id.get(name, []):
                if not all(matches(item['package']['version'], c) for c in constraints[name]):
                    continue
                following = {key: list(value) for key, value in constraints.items()}
                for dep, constraint in item['package']['dependencies'].items():
                    following.setdefault(dep, []).append(constraint)
                solution = solve({**selected, name: item}, following)
                if solution is not None:
                    return solution
            return None

        selected = solve({}, {name: [constraint] for name, constraint in requirements.items()})
        if selected is None:
            fail("package.unresolved", "No installed version set satisfies all package constraints")
        visiting, ordered = set(), []
        def visit(name):
            if name in visiting:
                fail("package.cycle", "Package dependencies contain a cycle")
            if name in ordered:
                return
            visiting.add(name)
            for dep in selected[name]['package']['dependencies']:
                visit(dep)
            visiting.remove(name)
            ordered.append(name)
        for name in sorted(selected):
            visit(name)
        lock = {'lock_version': '0.1.0', 'packages': [{'id': name,
            'version': selected[name]['package']['version'], 'sha256': selected[name]['sha256']} for name in ordered]}
        if pin_id is not None:
            if not isinstance(pin_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', pin_id):
                fail('package.pin', 'Invalid project pin identifier')
            directory = self.root / '.pins';directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.pin-', dir=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(canonical_bytes(lock));stream.flush();os.fsync(stream.fileno())
                os.replace(name, directory / (pin_id + '.json'))
            finally:
                Path(name).unlink(missing_ok=True)
        return lock

    def verify_lock(self, lock):
        if (not isinstance(lock, dict) or set(lock) != {'lock_version', 'packages'}
                or lock['lock_version'] != '0.1.0' or not isinstance(lock['packages'], list)):
            fail('package.lock', 'Invalid package lock')
        selected = {}
        for item in lock['packages']:
            if not isinstance(item, dict) or set(item) != {'id','version','sha256'} or item['id'] in selected:
                fail('package.lock', 'Invalid or duplicate locked identity')
            target = self._location(item['id'], item['version']) / 'archive.zip'
            if not target.is_file():
                fail('package.missing', f"Missing locked package {item['id']}@{item['version']}")
            preview, payload = inspect_package(target.read_bytes())
            if preview['sha256'] != item['sha256']:
                fail('package.lock_hash', 'Installed package differs from the lock')
            for name, content in payload.items():
                stored = target.parent / 'content' / Path(*PurePosixPath(name).parts)
                if (not stored.is_file() or stored.is_symlink()
                        or not stored.resolve().is_relative_to((target.parent / 'content').resolve())
                        or stored.read_bytes() != content):
                    fail('package.installed_hash', f'Installed content changed: {name}')
            selected[item['id']] = preview
        resolved = self.resolve({name: item['package']['version'] for name, item in selected.items()}, candidates=list(selected.values()))
        if {tuple(sorted(v.items())) for v in resolved['packages']} != {tuple(sorted(v.items())) for v in lock['packages']}:
            fail('package.lock', 'Dependency closure differs from the lock')
        return selected

    def read_file(self, package_id, version, path, *, expected_sha256):
        """Return an inspected data member without importing executable code.

        Reading uses the archive bytes after checking the entire installed
        dependency closure. The caller pins the archive hash shown in preview,
        so a stale UI cannot silently read a replacement package.
        """
        _safe_path(path)
        lock = self.resolve({package_id: version})
        selected = next(item for item in lock['packages'] if item['id'] == package_id)
        if selected['sha256'] != expected_sha256:
            fail('package.preview_changed', 'Package differs from the selected file preview')
        self.verify_lock(lock)
        preview, payload = inspect_package((self._location(package_id, version) / 'archive.zip').read_bytes())
        if preview['sha256'] != expected_sha256:
            fail('package.preview_changed', 'Package changed during file retrieval')
        if path not in payload or path == 'package.json':
            fail('package.file', 'Select a data member declared by package.json')
        return payload[path]

    def uninstall(self, package_id, version):
        target = self._location(package_id, version)
        if not target.exists():
            return False
        for pin in (self.root / '.pins').glob('*.json'):
            lock = canonical_loads(pin.read_bytes())
            if any(item['id'] == package_id and item['version'] == version for item in lock['packages']):
                fail('package.in_use', f'Package is pinned by project {pin.stem}')
        for item in self.list():
            package = item['package']
            if package['id'] != package_id and package_id in package['dependencies'] and matches(version, package['dependencies'][package_id]):
                fail('package.in_use', f"Package is referenced by {package['id']}@{package['version']}")
        # Rename first; resolved path was checked against the configured store.
        temporary = target.with_name('.removed-' + target.name)
        if temporary.exists():
            fail('package.busy', 'A previous removal is still pending')
        target.rename(temporary)
        shutil.rmtree(temporary)
        return True

    def extend_registry(self, registry, lock):
        from .engine.module_api import execution_contract
        from .engine.runtime import ModuleRegistry
        previews = self.verify_lock(lock)
        modules = [registry.get(m['id'], m['version']) for m in registry.manifests]
        known = {(m.manifest['id'], m.manifest['version']) for m in modules}
        for item in lock['packages']:
            preview = previews[item['id']]
            if preview['package']['kind'] != 'executable':
                continue
            for entry in preview['modules']:
                key = (entry['id'], entry['version'])
                if key in known:
                    fail('package.module_conflict', f'Module already registered: {key}')
                path = self._location(item['id'], item['version']) / 'content' / entry['implementation']
                identity = preview['sha256'] + ':' + entry['implementation']
                spec = importlib.util.spec_from_file_location('friskoli_installed_' + digest(identity.encode()), path)
                if spec is None or spec.loader is None:
                    fail('package.implementation', 'Cannot load the locked Python factory')
                code = importlib.util.module_from_spec(spec)
                source = path.read_bytes()
                if digest(source) != preview['package']['files'][entry['implementation']]:
                    fail('package.installed_hash', 'Factory source changed during registry loading')
                # Execute the verified source itself, never an untracked stale
                # __pycache__ file that merely matches source size/mtime.
                exec(compile(source, str(path), 'exec'), code.__dict__)
                factory = getattr(code, entry['factory'], None)
                if not callable(factory):
                    fail('package.implementation', 'Registered factory is not callable')
                module = factory()
                if canonical_bytes(module.manifest) != canonical_bytes(entry['manifest']):
                    fail('package.manifest_mismatch', 'Runtime manifest differs from inspected package declaration')
                execution_contract(module)
                modules.append(module);known.add(key)
        return ModuleRegistry(modules, 'modular-spatial-v1')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store')
    parser.add_argument('action', choices=('list', 'preview', 'install', 'uninstall', 'lock'))
    parser.add_argument('value', nargs='?')
    parser.add_argument('--sha256')
    args = parser.parse_args(argv)
    store = PackageStore(args.store) if args.store else PackageStore.default()
    if args.action == 'list': result = store.list()
    elif args.action == 'preview': result = inspect_package(Path(args.value).read_bytes())[0]
    elif args.action == 'install': result = store.install(Path(args.value).read_bytes(), expected_sha256=args.sha256)
    elif args.action == 'uninstall': result = store.uninstall(*args.value.rsplit('@', 1))
    else: result = store.resolve(canonical_loads(Path(args.value).read_bytes()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
