"""Regenerate or check the shipped PTS presets from their builders.

The PTS presets in ``src/friskoli_cad/examples/`` are generated, not edited by hand:

    python tools/build_presets.py           # rewrite the PTS preset files
    python tools/build_presets.py --check   # fail if any file is stale

``--check`` compares structure exactly and floating-point values to a relative
1e-12, because exp/erf results differ in the last ulp between platform maths
libraries; any real parameter change is far larger. The MCP chip presets are
written by ``research/chip-benchmark/chip_benchmark.py --write-preset`` and the
two modular presets are maintained as documents, so neither is touched here.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from friskoli_cad.engine.presets import (EXAMPLES, build_center_project, build_encounter_project,  # noqa: E402
                                         encounter_steady_field)

EXAMPLES_DIR = ROOT / 'src' / 'friskoli_cad' / 'examples'


def builders():
    """Preset id -> (zero-argument builder, file format)."""
    result = {}
    for mechanism in ('a', 'b'):
        for feedback in (True, False):
            suffix = '' if feedback else '-control'
            result[f'center-pts-{mechanism}-assay-strong{suffix}'] = (
                lambda m=mechanism, f=feedback: build_center_project(m, 'assay', feedback=f, release='strong', spacing_um=4.),
                'indent')
            result[f'center-pts-{mechanism}-small-strong{suffix}'] = (
                lambda m=mechanism, f=feedback: build_center_project(m, 'small', feedback=f, release='strong', spacing_um=1.),
                'compact')
    field = {}

    def encounter(feedback):
        if 'value' not in field:
            field['value'] = encounter_steady_field()[0]
        return build_encounter_project('a', feedback=feedback, field=field['value'])
    for feedback in (True, False):
        result['center-pts-a-encounter' + ('' if feedback else '-control')] = (lambda f=feedback: encounter(f), 'compact')
    missing = set(result) - set(EXAMPLES)
    if missing:
        raise SystemExit(f'Builders exist for unregistered presets: {sorted(missing)}')
    return result


def serialise(project, style):
    return json.dumps(project, indent=1) + '\n' if style == 'indent' else json.dumps(project)


def differences(built, shipped, path='', rel_tol=1e-12, limit=5):
    """First few structural or numeric differences between two JSON documents."""
    found = []

    def walk(a, b, p):
        if len(found) >= limit:
            return
        if isinstance(a, float) or isinstance(b, float):
            if not (isinstance(a, (int, float)) and isinstance(b, (int, float))
                    and math.isclose(a, b, rel_tol=rel_tol, abs_tol=0.)):
                found.append(f'{p}: {a!r} != {b!r}')
        elif type(a) is not type(b):
            found.append(f'{p}: {type(a).__name__} != {type(b).__name__}')
        elif isinstance(a, dict):
            if a.keys() != b.keys():
                found.append(f'{p}: keys differ {sorted(a.keys() ^ b.keys())}')
            for key in a.keys() & b.keys():
                walk(a[key], b[key], f'{p}/{key}')
        elif isinstance(a, list):
            if len(a) != len(b):
                found.append(f'{p}: length {len(a)} != {len(b)}')
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f'{p}[{index}]')
        elif a != b:
            found.append(f'{p}: {a!r} != {b!r}')
    walk(built, shipped, path)
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--check', action='store_true', help='compare instead of writing; exit 1 if stale')
    args = parser.parse_args(argv)
    stale = []
    for name, (build, style) in builders().items():
        path = EXAMPLES_DIR / (name.replace('-', '_') + '.project.json')
        project = build()
        if args.check:
            problems = ['missing file'] if not path.is_file() else differences(project, json.loads(path.read_text(encoding='utf-8')))
            if problems:
                stale.append(name)
                print(f'STALE {name}: ' + '; '.join(problems))
            else:
                print(f'ok    {name}')
        else:
            path.write_text(serialise(project, style), encoding='utf-8', newline='\n')
            print(f'wrote {path.relative_to(ROOT)}')
    if stale:
        print(f'{len(stale)} preset file(s) are stale; run python tools/build_presets.py')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
