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
python tools/check_distribution.py
python tools/build_presets.py --check
git diff --check
```

The PTS preset files in `src/friskoli_cad/examples/` are generated: change `engine/presets.py`, run `python tools/build_presets.py`, and commit the regenerated files with the code. Restart the service after changing backend code, model declarations or presets: a running service retains its startup source lock. Preserve frozen historical inputs and results. Use `runs/` for generated studies; keep archives and build outputs out of commits.

## Submit a contribution

Open an issue for a substantial change, then submit a pull request describing the problem, resulting behaviour, relevant assumptions and checks actually performed. Keep changes focused. Include tests for changed numerical or data-contract behaviour; documentation edits should be checked for working links and accurate commands.

Write documentation, code comments and docstrings in English. Preserve the application's English and Simplified Chinese localization. Record parameter sources and distinguish constructed values from experimental measurements. Retain third-party license notices.

The [model reference](docs/MODEL_REFERENCE.md) describes equations and data contracts. Start with the [module tutorial](docs/EXTENDING.md) to add a mechanism, and use the [research workflow](docs/RESEARCH.md) to reproduce paired and sensitivity studies. Contributions are distributed under the repository's MIT license.

## Release a tested distribution

CI runs the Python and frontend suites on Windows and Linux, with Python 3.11 and 3.13. It builds both archives and checks a fresh wheel installation outside the checkout. Optional CUDA tests need compatible hardware; keep their skip status with the result. CI configuration is shipped with the source so contributors can reproduce the same commands.

Publish the reviewed source revision, matching tag, wheel, source archive and their SHA-256 checksums together. Release notes describe delivered behavior and completed checks. Keep user instructions, the current module API and scientific assumptions in the guides; preserve generated runs outside the source tree.

For iGEM submission, retain the MIT license and third-party notices, document validation and supported exchange formats, and submit the source through the team's dedicated iGEM GitLab software repository. The [official software deliverable](https://teams.igem.org/go/deliverables/software) and [award criteria](https://competition.igem.org/judging/special-prizes) describe the submission and review requirements. A GitHub release is useful for distribution; the team's official submission is a separate publication step.
