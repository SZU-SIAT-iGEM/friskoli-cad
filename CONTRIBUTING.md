# Contributing to Friskoli-CAD

Friskoli-CAD is maintained in the SZU-SIAT-iGEM organization for the Friskoli project. Contributions should make model behaviour, assumptions and simulation records easier to inspect and reproduce.

## Set up

Use Python 3.11+ and Node.js 22+. Create and activate a virtual environment, then install:

```bash
python -m pip install -e ".[dev,standards,research]"
npm ci
```

On Windows, run Python commands with `.\.venv\Scripts\python` if the environment is not activated. Node.js is required only for frontend tests. CUDA is optional and requires compatible hardware and the `cuda11` extra.

## Check a change

```bash
python -m pytest tests -q
npm test
python -m build
git diff --check
```

Restart the service after changing backend code, model declarations or presets: a running service retains its startup source lock. Preserve frozen historical inputs and results. Use `runs/` for generated studies; keep archives and build outputs out of commits.

## Submit a contribution

Open an issue for a substantial change, then submit a pull request describing the problem, resulting behaviour, relevant assumptions and checks actually performed. Keep changes focused. Include tests for changed numerical or data-contract behaviour; documentation edits should be checked for working links and accurate commands.

Write documentation, code comments and docstrings in English. Preserve the application's English and Simplified Chinese localization. Record parameter sources and distinguish constructed values from experimental measurements. Retain third-party license notices.

The [development guide](docs/DEVELOPMENT.md) describes contracts, module extension points and validation limits. Contributions are distributed under the repository's MIT license.
