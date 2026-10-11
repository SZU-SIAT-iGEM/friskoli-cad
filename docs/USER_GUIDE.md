# Friskoli-CAD user guide

This guide describes version 1.0.0. Friskoli-CAD runs simulations locally and stores editable projects separately from frozen run records.

## Installation and startup

Use Python 3.11+. From a repository clone, create a virtual environment:

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\python -m pip install .
.\start.ps1 -Port 8765
```

Linux/macOS:

```bash
source .venv/bin/activate
python -m pip install .
python -m friskoli_cad.replay_service --port 8765
```

Open `http://127.0.0.1:8765`. On Windows, `run.cmd` launches the same service; `start.ps1 -NoBrowser` leaves browser opening to you. If PowerShell disallows scripts, use `.\.venv\Scripts\python -m friskoli_cad.replay_service --port 8765`. Choose another port if 8765 is occupied. `Ctrl+C` stops the service.

For a release wheel, run `python -m pip install /path/to/friskoli_cad-1.0.0-py3-none-any.whl` in an activated environment, then start the Python service. Keep the software version associated with your checkpoints. First installation needs dependency downloads. The installed CPU engine and bundled frontend work offline; offline browser editing still needs the local service for new computations.

| Optional extra | Source-install command | Purpose |
| --- | --- | --- |
| Standards | `python -m pip install ".[standards]"` | SBOL Component export through the pinned pySBOL3 implementation |
| Research | `python -m pip install ".[research]"` | Plotting used by study scripts |
| CUDA 11 | `python -m pip install ".[cuda11]"` | CUDA diffusion on compatible NVIDIA hardware |

CPU is the normal starting point. CUDA accelerates supported diffusion operations, not every operation in a graph.

## First simulation

1. Load **Modular foundation** on the welcome screen.
2. Review populations and environment in **Space**, and module connections in **Workflow**.
3. Keep the short default time step and step count, then select **Run**.
4. Inspect task status in **Runs** and recorded metrics in **Results → Data**.
5. Move the timeline to a saved frame. Select a cell and use **Focus cell** to inspect its geometry.
6. Export a portable `.friskoli` research package, then use **File → Open** to reopen it.

Settings switches between English and Simplified Chinese. A task freezes its input at submission; later draft edits do not change that run.

## Choose an example

| Preset ID | Use |
| --- | --- |
| `modular-foundation` | Fields, uptake, reserves, death, motion and explicit sources |
| `modular-material` | Cellulose-equivalent conversion, catalysts, erosion and lineage |
| `chip-mcp-gradient` | MCP benchmark with a gradient along the 400 µm X axis |
| `chip-mcp-zero` | Matched chip control at a uniform 100 µM ligand concentration |
| `center-pts-a-small-strong` | PTS A capacity approximation around center material |
| `center-pts-a-small-strong-control` | Matched A control with sensing-to-motion feedback disabled |
| `center-pts-b-small-strong` | PTS B capacity approximation in the same research setting |
| `center-pts-b-small-strong-control` | Matched B control with feedback disabled |

The four center presets use a 1 µm grid, `dt = 0.05 s`, and the `responsive` PTS methylation configuration. Fixed enzyme copy number and turnover are exploratory inputs. Read the actual loaded project's values and provenance rather than a previous study record.

## Edit a scene and workflow

**Space** holds initial populations, geometry, fields, sources and material objects. **Workflow** exposes the module graph. Objects represent graph inputs for display and editing; hiding an object or moving the camera does not change a submitted task.

Inspect registered ID/version, equations, parameters, sources and ports in the catalog and mathematical inspector. Parameter entries carry values, units and provenance. Ports must agree in shape, quantity, unit, species and population owner. Diagnostics point to invalid paths; correct the input before running.

A `same_step` connection reads an output produced earlier in the current numerical step. A `previous_step` connection reads the previous committed value. This delay is part of the model: the MCP chip's motor-to-motion edge uses `previous_step`. A connected graph still requires meaningful scientific choices.

The engine executes **prepare → field → physiology → lifecycle → observation**, then commits the complete step. Modules propose changes; the system settles shared inventory, geometry, identities and events. A failed step leaves the preceding committed state intact.

Physical duration is `dt × steps`. Numerical step, saved-frame interval, playback speed and wall-clock budget are separate settings. Saving fewer frames reduces output, while the engine still computes every numerical step and observation. Larger domains, finer grids, more cells and more seeds increase resource use. Start with a short run before a large study.

## Design candidates and controls

1. Configure a current example's scene, graph and observation region.
2. In **Design**, specify a name, chassis rationale, target population, metric, direction, parameter values to enumerate and independent seeds.
3. Select **Generate candidates**. This checks the graph and candidate budgets without simulating. Rejected candidates retain their reasons.
4. Choose candidates, controls, seeds and batch size, then run. Successful records are retained; missing or failed runs can be continued or retried. Failed and cancelled attempts remain in history.
5. Select **Compare completed runs**. Each independent run is one replicate; cells within a run are not independent repeats.
6. Export the native package when the records needed for comparison are present.

The workflow supports up to 16 execution branches, 8 seeds and 32 total runs. Generation does not guarantee a task fits actual memory, time or cell budgets. The generic Design fixed-bias control uses `bias = 0.5`; the center PTS presets have their own matched no-feedback controls. Keep these definitions distinct.

Comparisons use complete runs with compatible frozen inputs and source locks. Reports show means, sample standard deviations, valid seed counts and missing values. **Evaluate results** supplies an exploratory ranking under declared rules; it does not establish statistical significance or experimental performance. Report material accumulation and cumulative conversion separately.

## Read results

Replay reads saved data without advancing the simulation. The timeline loads task frames on demand with a bounded cache. Inspect cell ID, position, dimensions, state and trajectory. At a wide view, a minimum 4 px marker makes small cells selectable; **Focus cell** reveals the actual capsule geometry.

View XY, XZ or YZ concentration slices and check whether an array is full-resolution data or a volume-mean preview. Preview averaging changes display arrays, not numerical calculations. Preserve shape, unit, spacing and hash metadata with exported arrays.

Observations update at every committed numerical step independently of saved-frame frequency. They include region occupancy, arrival, residence, displacement, conversion, surface contact and declared radial measurements. Contact uses capsule-to-material surface distance. Chip drift fits mean X position over 10–120 s and remains null before sufficient data exist. Empty or unavailable values remain null.

Use orbit and hand tools to rotate or pan; wheel or pinch zoom changes the camera. Touch interaction distinguishes selection from dragging and supports one- and two-finger transitions. Scrollable panels and keyboard timeline controls support smaller screens. Physical-device testing remains separate from browser-size and pointer-logic checks.

## Save, reopen and continue

**File → Open** accepts supported project JSON, Workspace 0.7 and native `.friskoli` packages. Workspaces preserve the editable document and view state. Portable packages preserve frozen submissions, provenance, metrics, delivered frames and requested arrays. Keep a native package for reproducibility; CSV and HTML summaries refer to its complete inputs rather than replacing them.

Reopening displays existing results. New computation needs the local engine and locked dependencies. Pause at a committed boundary to obtain a checkpoint. Continuation checks implementation hashes and the random-number environment; use the exact software version that produced it. Source changes can invalidate continuation. Retired formats are rejected.

Environment migration requires a preview. Supported domain resizing retains the coordinate frame, conserves mapped inventory and rejects removal of occupied or nonzero-inventory regions. Prior results and parent-run relationships are retained.

Module packages distribute data or trusted executable modules. Preview checks the manifest; installation validates and copies files. Loading a locked executable dependency imports its Python factory. Install executable packages only from trusted sources: the module API provides no operating-system sandbox.

## Export a static viewer

Export a completed native design package or standalone **Export run JSON** record, then build into a new directory:

```bash
python -m friskoli_cad.wiki_export /path/to/completed.friskoli /path/to/viewer
python -m http.server 8881 --directory /path/to/viewer
```

Open `http://127.0.0.1:8881`. A standalone result JSON can replace the `.friskoli` input. Host the complete output directory on a static website. Optional `--download-url https://example.org/download` supplies a real full-version download link; omit it when unavailable.

The viewer includes previously computed records and local assets. It has no solver and cannot edit or rerun models. Inputs need complete frozen records and saved frames; incomplete or failed runs are rejected. Use HTTP because browsers often block JSON loading from `file://`. Large records download with progress, then parse as complete JSON and need sufficient browser memory. Review records before public hosting; the exporter neither uploads nor removes sensitive data.

## Standards export

```bash
python -m friskoli_cad.standards_export /path/to/completed.friskoli /path/to/completed.omex
```

Output must not already exist. OMEX preserves the native package and adds a manifest, checksums and loss report. It declares no executable master simulation.

With the `standards` extra, add `--components /path/to/components.json` for an SBOL Component subset. Input is an array of objects with exactly `identity`, `name`, `types`, `roles` and `derived_from`. Identity is an explicit HTTP(S) URI; types is a nonempty ontology-URI array; roles may be empty; derived_from is a nonempty source-URI array. pySBOL3 validates and reads back these properties. The exporter does not infer DNA sequences, strains or molecular identities from a simulation graph.

SBML, SED-ML and GenBank/FASTA mappings are unavailable. Loss reports list omissions and unsupported formats. Keep the native package as the complete record.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Missing Python or dependency | Activate the environment and reinstall with that interpreter's `python -m pip install .` |
| Port in use | Stop the existing instance or choose another `--port` / `-Port` |
| `task.source_changed` | Restart after backend or preset edits, then submit with the new source lock |
| Invalid port or parameter | Follow its diagnostic path and check units, owner and timing |
| `resource.cell_limit` or budget rejection | Review the resource budget and preview a compatible continuation; geometric growth blocking is a separate outcome |
| Incompatible checkpoint | Restore its exact software/dependency environment, or start a new run and retain the old record |
| Null metric | Check definition, required channels, duration and population; missing values are not zero |
| Viewer cannot read JSON | Serve its full directory over HTTP and confirm all files exist |

Report software problems through [GitHub Issues](https://github.com/SZU-SIAT-iGEM/friskoli-cad/issues) with version, operating system, browser, steps, diagnostic and a small shareable input. Developer checks and assumptions are in the [development guide](DEVELOPMENT.md).
