# Friskoli-CAD

A modular simulation and design tool for chemotactic engineered bacteria. You describe a 3D scene (cell populations, nutrient fields, obstacles), wire computation modules together in a typed graph (PTS signalling, MCP adaptation, run/tumble motion, uptake, growth), run it, and replay results cell by cell.

Friskoli-CAD 是面向趋化工程菌设计的模块化仿真与可视化工具：在三维场景中放置菌群、营养场与障碍物，用带类型和单位的端口连接计算模块，运行后逐菌体回放结果。

## Quick start

Requires Python 3.11+.

```powershell
# Windows: double-click run.cmd, or
.\start.ps1                 # serves http://127.0.0.1:8765 and opens the browser
.\start.ps1 -Port 9000 -NoBrowser
```

```bash
# Any platform
pip install -e .
python -m friskoli_cad.replay_service --port 8765
```

`start.ps1` prefers a project-local `.venv`, installs missing dependencies on first run (network needed once), and then works offline. Optional extras: `pip install -e ".[standards]"` for SBOL export, `".[cuda11]"` for GPU diffusion.

## First run

1. Open the welcome page and load **foundation-control** (no chemotaxis control) and **foundation-pts-b** (PTS-driven chemotaxis).
2. Run both; compare them in the Design workspace.
3. Replay in 3D and inspect individual cells.
4. Export a `.friskoli` package, HTML/CSV report, or static Wiki page.

Do not start with the 128 µm / 256³ scene: its full 10,000-step run has not been completed (see [limitations](#known-limitations)).

## Documentation

| Read this | For |
| --- | --- |
| [docs/module-guide.md](docs/module-guide.md) | Choosing examples, connecting modules, tuning parameters |
| [docs/module-reference.md](docs/module-reference.md) | Registered module reference |
| [docs/frontend-user-journey.md](docs/frontend-user-journey.md) | Workspace walkthrough |
| [docs/design-workflow.md](docs/design-workflow.md) | Candidates, controls, comparison reports |
| [docs/science/README.md](docs/science/README.md) | Model equations, parameter sources, limits |
| [docs/standards-export.md](docs/standards-export.md) | Supported SBOL/OMEX export and what is not supported |
| [docs/architecture.md](docs/architecture.md), [docs/system-execution.md](docs/system-execution.md) | How the system is built and executed |
| [docs/archive/](docs/archive/) | Development records, verification logs, superseded protocols |

## Known limitations

- Parameters are source-derived or constructed demonstration values. **Nothing is calibrated against wet-lab data**, and no 12-hour biological prediction is claimed.
- The large-domain PTS scene (128 µm cube, 256³ grid, 200 cells, 10,000 steps) is not completed; a clear central aggregation has not been demonstrated there. Step time is the current bottleneck.
- SBML, SED-ML and GenBank/FASTA export are not supported. SBOL export covers Component core properties only.
- Browser-size simulation and pointer-logic tests have been done; physical-device testing has not.
- No open-source license has been chosen yet.

## Repository layout

```
src/friskoli_cad/   engine, protocol schemas, task service, web frontend
tests/              Python (pytest) and frontend (.mjs) tests
examples/           runnable scripts and example projects
docs/               current documentation; docs/archive/ holds history
```

Run tests with `PYTHONPATH=src python -m pytest tests`.
