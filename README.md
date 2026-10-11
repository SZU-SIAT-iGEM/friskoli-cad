# Friskoli-CAD

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](pyproject.toml)

Friskoli-CAD is a local simulation and design application developed for **Friskoli**, the engineered-bacteria project of the **SZU-SIAT iGEM team**. It belongs to the [SZU-SIAT-iGEM organization](https://github.com/SZU-SIAT-iGEM) and is released as an open-source research tool.

The project explores how nutrient uptake, chemotactic signalling and cell motion interact with surface-enzyme substrate conversion. Friskoli-CAD provides a shared workspace for building these models, comparing parameter choices and controls, and sharing reproducible simulation records. Its name refers to computer-aided design of model configurations and research scenarios; it does not generate DNA sequences or manufacturing drawings.

**Version 1.0.0 is the first stable software release.** Model parameters remain exploratory: software stability does not establish experimental calibration or a biological performance benefit.

## Features

- **Build a 3D scene:** configure cell populations, nutrient fields, sources, material objects and obstacles.
- **Compose mechanisms:** connect modules through ports with declared types, units, owners and explicit timing. Inspect equations and parameter provenance.
- **Run locally:** simulate diffusion, uptake, PTS/MCP signalling, run-and-tumble motion, growth, division and observations through one modular engine.
- **Compare designs:** generate parameter candidates, run independent seeds, retain failed attempts and compare recorded outcomes against controls.
- **Inspect results:** replay saved frames in 3D, focus individual cells, view field slices, metrics and trajectories.
- **Save and share:** export portable `.friskoli` packages with frozen inputs and results, resume compatible checkpoints, or build a static viewer for completed records.
- **Export standards:** create an OMEX archive and, with an optional dependency, an explicitly declared SBOL Component subset.

The application runs a Python service on your computer and displays its interface in a browser. JavaScript, Three.js and KaTeX assets are bundled locally. CPU use needs no Node.js installation; Node.js is used for frontend tests. The interface supports English and Simplified Chinese.

## Get started

Install **Python 3.11 or later**, then clone the repository:

```bash
git clone https://github.com/SZU-SIAT-iGEM/friskoli-cad.git
cd friskoli-cad
python -m venv .venv
```

On Windows:

```powershell
.\.venv\Scripts\python -m pip install .
.\start.ps1
```

You can also double-click `run.cmd`. The launcher prefers `.venv`, checks dependencies and opens the browser. If PowerShell blocks the script, start directly:

```powershell
.\.venv\Scripts\python -m friskoli_cad.replay_service --port 8765
```

On Linux or macOS:

```bash
source .venv/bin/activate
python -m pip install .
python -m friskoli_cad.replay_service --port 8765
```

Open **http://127.0.0.1:8765**, load **Modular foundation**, and run its short default configuration. Inspect **Results → Data**, then replay the saved frames. Stop the service with `Ctrl+C`. Installation needs network access for dependencies; the installed application can run locally offline.

Downloadable packages are listed under [releases](https://github.com/SZU-SIAT-iGEM/friskoli-cad/releases). Install a wheel with `python -m pip install /path/to/friskoli_cad-1.0.0-py3-none-any.whl`, then start the same Python service.

## Documentation

| Document | Contents |
| --- | --- |
| [User guide](docs/USER_GUIDE.md) | Installation, examples, editing, Design, results, packages, exports and troubleshooting |
| [Development guide](docs/DEVELOPMENT.md) | Current architecture, contracts, scientific assumptions, extension points, checks and release maintenance |
| [Contributing](CONTRIBUTING.md) | Development setup and contribution expectations |

## Included examples

| Example | Purpose |
| --- | --- |
| Modular foundation | A short introduction to fields, uptake, reserves, motion and sources |
| Material and lifecycle | Material conversion, enzyme providers, geometry and lineage |
| MCP gradient / zero-gradient chip | A receptor-based chemotaxis benchmark with a matched zero-gradient comparison |
| PTS A / B, small domain, strong release | Two center-substrate research approximations, each with a matched no-feedback control |

Eight presets ship with the application. The execution profile is `modular-spatial-v1`. PTS presets use a 1 µm grid and the `responsive` methylation configuration; these fine-grid scenes need more resources than the introductory example.

## Scientific scope and compatibility

Parameters include literature-derived relationships and constructed demonstration values. **The models have not been calibrated against wet-lab measurements.** Numerical tests assess the implemented equations and software contracts. They do not validate engineered-bacteria performance or long-term biological predictions.

PTS A omits surface sugar pools, contact QSSA and adhesion feedback. B also uses documented simplifications. MCP is a measurement benchmark with an approximate adaptation law. Report aggregation and substrate conversion separately, retain null or negative outcomes, and treat independent runs as the units of replication. See the [scientific assumptions](docs/DEVELOPMENT.md#scientific-assumptions).

Version 1.0.0 supports the current project, workspace and task contracts. Retired formats are rejected. Checkpoint continuation requires compatible implementation and random-number environment locks; keep the exact software version used to produce a run. SBML, SED-ML and GenBank/FASTA export are unavailable. Browser-size and pointer-logic checks do not constitute physical-device testing.

## Development

```bash
python -m pip install -e ".[dev,standards,research]"
npm ci
python -m pytest tests -q
npm test
python -m build
```

Python 3.11+ and Node.js 22+ are required for this workflow. Source, bundled examples and web assets live in `src/friskoli_cad/`; tests in `tests/`; reproducible study scripts in `research/`; current guides in `docs/`. Generated outputs and local historical archives are excluded from Git.

## Support and license

Report reproducible problems through [GitHub Issues](https://github.com/SZU-SIAT-iGEM/friskoli-cad/issues), including the version, steps, diagnostic and a minimal shareable input. Contribution guidance is in [CONTRIBUTING.md](CONTRIBUTING.md).

Friskoli-CAD and the team's own A/B model material are distributed under the [MIT License](LICENSE). See [third-party notices](THIRD_PARTY_NOTICES.md) for bundled Three.js, KaTeX, font licenses and declared dependencies. Scientific publications cited by the project remain the work of their authors.
