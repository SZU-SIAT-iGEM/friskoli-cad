# Friskoli-CAD development guide

This is the continuation reference for version 1.0.0, maintained by the SZU-SIAT iGEM team for Friskoli. It describes the current implementation and its validation limits. User-facing workflows are in the [user guide](USER_GUIDE.md); contribution expectations are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Environment and checks

Use Python 3.11+ and Node.js 22+. Create and activate `.venv`, then run:

```bash
python -m pip install -e ".[dev,standards,research]"
npm ci
python -m pytest tests -q
npm test
python -m build
git diff --check
```

On Windows, use `.\.venv\Scripts\python` without activation. `standards` enables SBOL checks; `research` provides plotting; `dev` provides pytest and the build frontend. CUDA checks require compatible hardware and `cuda11`. Report skips separately from passes. The application itself does not require Node.js.

Start with `python -m friskoli_cad.replay_service --port 8765` or `start.ps1`. Restart after modifying backend code, declarations or presets. The service retains its startup source lock, and a worker rejects changed source with `task.source_changed`.

## Repository map

| Location | Responsibility |
| --- | --- |
| `src/friskoli_cad/engine/` | Module contracts, registry, compilation, execution, geometry, fields, observations and checkpoints |
| `src/friskoli_cad/science/` | Explicit model functions and source/parameter data |
| `src/friskoli_cad/protocol/schemas/` | Versioned JSON data contracts |
| `src/friskoli_cad/tasks/` | Admission, worker execution, persistence, arrays and artifacts |
| `src/friskoli_cad/web/` | Browser workspace, graph editor, Design and replay; bundled vendor assets |
| `src/friskoli_cad/examples/` | Eight installable current presets |
| `tests/` | Numerical, contract, HTTP, persistence and frontend checks; synthetic contracts in `tests/fixtures/protocol/` |
| `research/` | Reproducible chip and center-substrate study/export scripts |
| `docs/` | Current user and development guides |

Keep simulation data in ignored `runs/` and builds in ignored `build/` / `dist/`. Local historical records and retired implementation are in ignored `archive/`; no current code or documentation should depend on them. Git retains previous commits without including archives in the current checkout or source distribution.

Protocol fixtures provide standalone schema/compiler inputs with their own synthetic manifests. They are test data, not registered application presets. The retired Workspace 0.2 example remains archived and is not an accepted application input.

## Current data contracts

Software release versions and protocol versions are separate. Releasing 1.0.0 does not rename these contracts.

| Contract | Version |
| --- | --- |
| Execution profile | `modular-spatial-v1` |
| Project | 0.6 |
| Graph / Module | 0.2 / 0.2 |
| Catalog / Task / Frame | 0.5 / 0.6 / 0.2 |
| Workspace | 0.7 |
| DesignBrief / Design | Supported internal 0.1, 0.2 and 0.3 variants |
| Module distribution package | 0.1 |

Schemas in the package and `GET /api/capabilities` are authoritative for actual accepted fields. Retired project/task/checkpoint formats are rejected. Native result import preserves frozen source locks. Checkpoint continuation checks implementation hashes and RNG compatibility; source updates can invalidate old checkpoints.

## Architecture and ownership

`engine/core.py` defines World, CellGroup, Geometry and errors. `engine/module_api.py` defines immutable `StepContext`, `ModuleProposal` and Effects. `engine/compiler.py`, `execution_planner.py` and `port_semantics.py` validate ownership, ports, units, stage order, resource access and temporal edges. `modular_registry()` registers standard modules and science adapters into one registry. `ModularSimulation` is the common runner; model-specific algorithms belong in registered modules rather than branches keyed by preset names or A/B/MCP identity.

The browser keeps scientific document state separate from camera, selection and layout state. View state does not enter the scientific input hash. Commands and workspace transactions manage edits; submitted tasks and historical results remain frozen.

`TaskService` handles admission, idempotency, persistence and delivery. The worker checks source and implementation locks before execution. Frame metadata is separate from binary arrays; readers verify shape, units and hashes. Design, single runs, paged replay, native delivery and static export use frozen inputs and declared observations.

### One numerical step

```mermaid
flowchart LR
    P[prepare] --> F[field]
    F --> S[Shared settlement and diffusion]
    S --> B[physiology]
    B --> G[Geometry and realized growth]
    G --> L[lifecycle]
    L --> I[Identity and inheritance]
    I --> O[observation]
    O --> V[Validate and commit]
```

Modules read authorized resources and return proposals. The system owns shared inventory settlement, geometry constraints, cell identities, events and atomic commit. Concentrations, module state, RNG, ledgers, identities and observation history roll back if a step fails. Observation occurs after physical and identity events, every numerical step. Saving fewer frames must not change the scientific state or accumulated metrics.

`same_step` reads an already available output in the current step; `previous_step` reads the preceding committed output and makes an explicit delay. Backward same-step dependencies and undeclared algebraic cycles are rejected. Settled outputs represent accepted uptake, exchange or realized growth; later stages may read them, while same-stage proposals cannot masquerade as settled quantities.

Field settlement applies material release, explicit sources, external exchange and shared uptake before finite-volume diffusion. Physiological modules propose signal, movement and growth updates. The system applies geometry constraints before lifecycle modules read realized growth and propose division. Stable IDs and state inheritance are then resolved before observation.

### Units, geometry and resource limits

Length is `um`, time is `s`, concentration is `uM` and inventory is `molecule`. A voxel contains `602.214076 × dx × dy × dz` molecules per µM; thin layers retain their actual Z thickness. Field storage follows declared axis order, not a display assumption.

Cells are finite capsules. Total length L and diameter d satisfy `L >= d > 0`; the axial segment has half-length `(L-d)/2`, with radius `d/2`. Clearance uses capsule-to-capsule, capsule-to-box and capsule-to-wall geometry. Continuous paths are checked conservatively. These are kinematic rules, with no hydrodynamic or physical contact-force model. Floating-point tolerances and subdivision limits are part of the numerical method.

The transaction coordinator stores state in host memory. Supported CUDA diffusion uses explicit host/device transfer; other registered CPU operations remain on the CPU. Budget calculations include field buffers, maximum-population state, collision workspace, module workspaces, outputs and checkpoints. Record ports/states have bounded size. `system_limits.max_cells` is a computational limit, not biological inhibition: excess causes rejection of the uncommitted step.

Domain migration retains the origin and coordinate frame. Added regions start at zero concentration; cropped regions must contain neither inventory nor excluded entities/sources. Local volume-overlap mapping conserves fields and compatible components. Rotation or removal interpreted as external outflow requires a separate explicit model and is currently rejected.

## Extend the system

1. Define a unique module ID/version, stage, owner scope, parameters, ports, state and authorized effects. Include units, equations, provenance, license and validation status.
2. Implement `initialize(StepContext)` and/or `propose(StepContext)` returning `ModuleProposal(outputs, state, effects)`. Context arrays/maps are read-only; do not mutate another owner's state or the world.
3. Register the implementation through the shared registry. Use `standard_modules.py`, `science_extensions.py`, `science_adapters.py` and `science_advanced.py` as current examples.
4. Test the governing relation independently, conservation, temporal edges, failure rollback and checkpoint restoration. Add lifecycle inheritance tests when relevant.
5. Expose display/edit adapters without moving scientific computation into the browser. Update these guides when behaviour changes.

New shared state kinds or effects require an explicitly versioned system-contract change and tests. Module IDs, parameter values and provenance must not be silently rewritten when importing a frozen result.

### Distribution packages

Package ZIPs contain root `package.json`, explicit file SHA-256 values, a license, source, exact version and dependency declarations. Executable module entries name the manifest, implementation and factory; IDs are namespaced. The factory's manifest must match the previewed manifest. Installation validates and copies files; loading a locked dependency imports its factory as trusted Python code. API restrictions are not an operating-system sandbox.

Validation rejects path traversal, duplicate/case-conflicting names, symlinks, encryption and reserved Windows names. Package archives are limited to 32 MiB compressed, 64 MiB expanded and 1024 members. Refer to `packages.py` and its tests for complete rules.

## Scientific assumptions

Version 1.0.0 is a stable software baseline with exploratory models. It has no wet-lab parameter fit, trajectory calibration or experimental demonstration of engineered-bacteria benefit. A catalog entry marked `tested` refers to listed numerical checks, not biological validation. Source support and parameter records live in `science/data/` and module declarations.

| Mechanism | Implemented relation or assumption |
| --- | --- |
| Diffusion | `dC/dt = D∇²C`; finite-volume paired fluxes, no-flux obstacle faces and stable substeps |
| Boundary exchange | `C' = target + (C-target) exp(-k dt)`; finite-rate exchange, with actual changes recorded as external inventory |
| Shared uptake | `copies × turnover × C/(K+C) × dt`, limited by shared representable inventory; downstream signalling reads accepted flux |
| Material release | Finite material stock transferred to dissolved equivalents; unrepresentable small transfers remain in their source |
| PTS A capacity | `min(g × reference_copies, area × (1-basal_inner) × max_fraction / footprint)` |
| PTS B capacity | `min(g × reference_copies, area/reference_area × reference_copies × gcap)` |
| PTS EI | `de/dt = kd × accepted_flux × (1-e) - kre × e`; exact relaxation for fixed step flux |
| PTS methylation | `a = clip(a0-e+gm(m-mmin),0,1)` and `dm/dt = km(a0-a)` with bounded backward Euler |
| CheY | `dy/dt = kp A (Ytotal-y) - kd y`; exponential advance with end-step CheA held fixed |
| MCP | MWC receptor activity with a reduced implicit adaptation law; receptor/parameter attribution remains qualified |
| Motor and motion | Hill readout, event-time tumble hazard, stable-ID RNG streams and constrained capsule movement |
| Surface enzymes | Fixed enzyme copies and turnover within a contact range; no expression burden, folding or surface sugar pool |
| Lifecycle | Explicit module proposals and system-controlled geometry, inventories and stable-ID inheritance |

PTS methylation advances EI, adaptation and CheY in sequence with first-order splitting. Its coupling slopes are constructed from input ranges, not fitted experimental strengths. PTS A omits surface sugar pools, contact quasi-steady-state approximation (QSSA) and adhesion/detachment feedback. B is also a research simplification rather than a complete reproduction of its source model. Generic growth/division/death modules demonstrate platform capability; their availability does not validate a source-specific lifecycle.

### Center-substrate presets

The shipped small-domain scenes are 64 × 64 × 32 µm with a 1 µm grid and `dt = 0.05 s`. Center material size is proportional to domain size. Initial cells, material inventory, enzyme copies, turnover, Gaussian product field and boundary-exchange layers are declared in the actual project. Current strong-release presets use 4,000 enzyme copies and `kcat = 130/s`; these are constructed research settings. Study-script overrides are separate configurations and must retain their project hash.

A and B share environmental settings, accepted-flux PTS signalling, methylation adaptation and motor relationships; their capacity relations differ. Their matched controls disable sensing-to-motion feedback while retaining the corresponding adapted bias. Initial dissolved product is separate inventory from newly converted material. Boundary sinks make the system open and record outgoing inventory.

Report region occupancy, arrival, residence, radial distributions, surface contact and cumulative conversion separately. Contact fraction is the current living fraction within the declared capsule-surface contact range. Contact cell-seconds sums the initial cohort's contact integral; death stops accumulation. Radial observations use a declared center and increasing radii. Descendants do not enlarge the initial-cohort denominator. Independent seeds represent independent runs; all cells in a run share that replicate.

### MCP chip benchmark

The default chip is 400 × 800 × 200 µm with a 20 µm grid, 200 cells and `dt = 0.1 s`. Its gradient follows X, from 0 to 200 µM across 400 µm, with finite exchange on the two X boundary layers. The high-concentration observation half is X >= 200 µm. The zero-gradient comparison is 100 µM. Diffusion `D = 800 µm²/s`, exchange `5/s` and adaptation `k = 0.3/s` are exploratory settings.

Speed 20 µm/s, cluster size 6, Ki 18 µM and Ka 3000 µM are retained candidate values whose exact primary attribution is unverified. `science/data/chip_parameters.json` records this status. Drift is the least-squares slope of population mean X position over committed boundaries in 10–120 s; it is null before 120 s. A 300 s endpoint distribution is a separate measurement window.

### Mechanistic references

- [Neumann et al., PNAS (2012)](https://doi.org/10.1073/pnas.1205307109): pathway context for PTS and adaptation.
- [Somavanshi, Ghosh and Sourjik, PLOS Biology (2016)](https://doi.org/10.1371/journal.pbio.2000074): PTS influx sensing and chemotactic signalling.
- [Tu, Shimizu and Berg, PNAS (2008)](https://pmc.ncbi.nlm.nih.gov/articles/PMC2551628/): receptor/adaptation theory; the application's reduced law is separately defined.
- [Cluzel, Surette and Leibler, Science (2000)](https://doi.org/10.1126/science.287.5458.1652): motor-response context.

These references supply mechanistic context. They do not establish that the complete parameter combination or numerical splitting in this application reproduces the original experiments.

## Reproduce studies

Run resource-intensive studies serially and write new records under `runs/`:

```bash
python research/chip-benchmark/chip_benchmark.py --table
python research/chip-benchmark/chip_benchmark.py --duration 300 --seeds 8 --workers 1 --out runs/chip-study
python research/first-release/center_study.py --duration 120 --seeds 4 --dt 0.05 --spacing 1 --out runs/center-study
python research/first-release/export_handoff.py --center runs/center-study --chip runs/chip-study --out runs/exported-study
```

The center script also accepts `--scale` and `--mechanism`. Every changed duration, grid, time step or mechanism defines a distinct configuration. Preserve complete input, project hash, seed, source lock and software environment. The center script has its own defaults; explicit arguments above identify the requested study. Retain null and negative outcomes. Previously generated runs are not evidence for the current executable.

## Delivery and compatibility

Native `.friskoli` packages preserve frozen designs/submissions, results, saved frames, requested arrays and hashes. `design_delivery.py` and `run_delivery.py` validate these records. CSV/HTML reports refer to complete packaged input evidence instead of duplicating large arrays. Static export in `wiki_export.py` accepts completed native designs or frozen standalone task records, validates completeness, and bundles a read-only viewer. It downloads then parses complete JSON; very large datasets still require browser memory.

`standards_export.py` provides OMEX containing the native package, manifest, checksums and loss report. Optional `sbol3==1.2.0.post0` maps only explicitly supplied Component identity, name, types, roles and derivation, with validation and semantic readback. No SBML, SED-ML, GenBank or FASTA mapping is implemented; OMEX supplies no executable master simulation. Preserve vendor notices and do not describe container validation as external numerical reproduction.

## Validation boundaries

Relevant checks include `test_first_release_contracts.py`, `test_first_release_science.py`, `test_first_release_workflow.py`, dense-field, diffusion, PTS/methylation, collision, observation, checkpoint, task, Design and export tests. Frontend tests cover transactions, persistence, selection, field display and gesture logic. Fix failures before claiming a passing suite; record optional dependency and CUDA skips explicitly.

Scientific confidence still requires configuration-specific time-step/grid sensitivity, fresh independent-seed comparisons, experimental parameter/trajectory calibration and comparison of script, task and UI metrics under identical frozen inputs. Physical touch devices and independent-user acceptance require their own observations. Long-term prediction and complete source-model reproduction are outside the current evidence. This list describes present validation limits without assigning a development schedule.

## Release maintenance

Keep Python and npm software versions aligned; protocol versions change only when their contracts change. Before a release, run the suites, build a wheel and source distribution, install the wheel outside the checkout, exercise a preset and confirm web/schema/example/license assets are included. Check documentation links, English comments and absence of archived/generated files in the Git index and source distribution.

Commit the reviewed state, create an annotated `vX.Y.Z` tag, push without rewriting existing history, and attach distributions and SHA-256 checksums to the GitHub release. Release notes should state delivered behaviour and checks actually completed. Retain scientific assumptions and explicit validation limits in the current guides.
