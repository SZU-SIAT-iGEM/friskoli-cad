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

1. Load **模块基础 / Modular foundation** and run its short default configuration.
2. Load **MCP 梯度芯片 / MCP gradient chip** and its zero-gradient control for the measurement benchmark, or **PTS A/B · 小域 / 中域** and their matched controls for center-substrate research.
3. Use Design for parameter candidates and multiple seeds. Inspect recorded metrics in Results → Data.
4. Replay in 3D. Small cells have 4 px position markers; select a cell and use **Focus cell** to inspect its actual capsule geometry.
5. Export a portable `.friskoli` research package and reopen it through File → Open. Frozen inputs, metrics, saved frames and requested arrays travel with it.

The sole current execution profile is `modular-spatial-v1`. See the [support matrix](docs/first-release/support.md), [scientific scope](docs/first-release/science.md), [acceptance evidence](docs/first-release/acceptance.md), and [installation/user checklist](docs/first-release/install-and-use.md).

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
- A omits surface sugar pools, contact QSSA and adhesion feedback; B uses documented research simplifications. Null or negative outcomes are retained. Long-term and large-domain studies require separate validation.
- SBML, SED-ML and GenBank/FASTA export are not supported. SBOL export covers Component core properties only.
- Browser-size simulation and pointer-logic tests have been done; physical-device testing has not.
- Old project/task/checkpoint formats are rejected. New same-version checkpoints retain RNG, inventories, identity and observation state.
- User-owned A/B materials are authorized under MIT. Vendored Three.js, KaTeX and third-party dependencies keep their own license notices.

## Repository layout

```
src/friskoli_cad/   engine, protocol schemas, task service, web frontend
tests/              Python (pytest) and frontend (.mjs) tests
examples/           runnable scripts and example projects
docs/               current documentation; docs/archive/ holds history
```

Install with `python -m pip install -e ".[standards]"`, then run `python -m pytest tests -q` and `node --test tests/*.test.mjs`. Build an installable wheel with `python -m build --wheel`.

## License

[MIT](LICENSE).
