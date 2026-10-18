"""Current guidance resolves to shipped references rather than local history."""
from pathlib import Path
import re

ROOT = Path(__file__).parents[1]


def test_documentation_references_and_local_guide_links_resolve():
    paths = [*ROOT.glob('*.md')]
    for folder in ('docs', 'src/friskoli_cad', 'research', 'examples'):
        paths.extend(path for path in (ROOT / folder).rglob('*')
                     if path.suffix in ('.md', '.py', '.json', '.mjs') and 'vendor' not in path.parts and '__pycache__' not in path.parts)
    missing = set()
    for path in paths:
        text = path.read_text(encoding='utf-8')
        for reference in re.findall(r'(?<![/\w])docs/[\w/.-]+\.md', text):
            if not (ROOT / reference).is_file():
                missing.add(f'{path.relative_to(ROOT)}: {reference}')
        if path.suffix == '.md':
            for target in re.findall(r'\]\(([^)]+)\)', text):
                target = target.split('#', 1)[0]
                if target and not re.match(r'[a-z]+://', target) and not (path.parent / target).exists():
                    missing.add(f'{path.relative_to(ROOT)}: {target}')
    assert not missing, '\n'.join(sorted(missing))
