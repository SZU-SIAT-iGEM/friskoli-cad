# Add a module

A module supplies one mechanism through a versioned manifest and two Python methods. The existing engine checks its ports and execution stage, then commits its proposals together with the rest of the simulation. A new parameterized mechanism usually needs no changes to the solver, browser or task service.

The runnable example in [`examples/modules/constant_bias`](../examples/modules/constant_bias) supplies a constant motor bias to an existing population. It illustrates registration and packaging; it contains no receptor or adaptation mechanism. See the [model reference](MODEL_REFERENCE.md) for stages, units and scientific assumptions.

## Run the local example

Install the project in your active Python environment, then run from the repository root:

```bash
python -m pip install -e .
python examples/modules/constant_bias/demo.py
```

On Windows, replace `python` with `.\.venv\Scripts\python`. The example computes one step, prints eight bias values of `0.75`, and writes `runs/tutorial/project.json`. Its registry contains the built-ins plus `tutorial.constant_bias@1.0.0`. The graph keeps the existing node ID and connections while selecting the new implementation. That registry belongs to this Python process; the browser service needs the distribution package described below.

## Describe the contract

[`module.json`](../examples/modules/constant_bias/module.json) names the module, version, population scope, parameters, inputs, outputs and state. Its `motor_bias` output has shape `cell.scalar`, quantity `motor_bias` and unit `1`. These match the existing motion input exactly. `bias` must lie in `[0, 1]` and enters a project with its value, unit and provenance.

Use a distinct namespaced ID for a new implementation. A changed parameter contract, equation or state interpretation needs a new version. Two modules cannot register the same ID/version. Never change a frozen project's version or provenance during import.

Population outputs contain one value per `context.entity_ids` entry, in the same order. Supported port shapes and their numerical constraints are defined in `engine/port_semantics.py` and the bundled Module 0.2 schema. Unit conversions require explicit modules. Connections between populations must follow the declared owner rules.

## Implement and register

[`module.py`](../examples/modules/constant_bias/module.py) implements `initialize(context)` and `propose(context)`. Both return `ModuleProposal(outputs, state, effects)`. The stateless example returns an empty state and no effects. Its execution contract declares the `physiology` stage, CPU support, and no world reads or writes.

`StepContext` provides committed time, time step, node ID, owner ID, cell IDs, parameters, inputs, state and authorized world resources. Arrays and mappings are read-only. Return new values rather than changing inputs. Initialization must provide the manifest's `initial_outputs`; later proposals provide every output and declared state entry. The engine checks shapes, finite values and granted effects before committing.

Local registration uses the public registry:

```python
builtin = modular_registry()
registry = ModuleRegistry([
    builtin.get(m['id'], m['version']) for m in builtin.manifests
] + [create()])
simulation = simulation_from_project(project, registry)
```

The complete imports and graph edit are in `demo.py`. The same manifest drives the catalog, numeric parameter editing, typed connections and Design parameter scans. The optional `declaration` supplies equations, assumptions and verification references. Mark evidence as tested only when the referenced check supports that claim.

## Load the module in the service

Build a ZIP containing canonical JSON manifests, Python source, a license and SHA-256 file hashes:

```bash
python examples/modules/constant_bias/build_package.py
python -m friskoli_cad.packages preview runs/tutorial/constant-bias.zip
```

Read the preview. Install the same archive using the SHA-256 printed by the builder:

```bash
python -m friskoli_cad.packages install runs/tutorial/constant-bias.zip --sha256 YOUR_ARCHIVE_SHA256
```

`YOUR_ARCHIVE_SHA256` is the actual 64-character archive hash. Preview and installation inspect/copy files without executing the module. Resolving a project with an executable dependency loads its verified factory as trusted Python code. This API has no operating-system sandbox.

Add a dependency lock to the generated project, using the same environment as the service:

```python
import json
from pathlib import Path
from friskoli_cad.packages import PackageStore

path = Path('runs/tutorial/project.json')
project = json.loads(path.read_text(encoding='utf-8'))
project['dependency_lock'] = PackageStore.default().resolve({'tutorial': '1.0.0'})
path.write_text(json.dumps(project, indent=2) + '\n', encoding='utf-8')
```

Start the service and open that project. The workflow catalog includes the tutorial module and its equation. New tasks resolve the same locked package; checkpoints retain its implementation identity. Share the ZIP with recipients along with the project. They install the exact package before computing. `FRISKOLI_PACKAGE_DIR` selects a separate store when needed; set it consistently for the installer and service. Install a new module version after changing its source, and resolve a new lock for a new study.

## Extend safely

Choose `same_step` for a dependency available earlier in the numerical step and `previous_step` for an explicit delay. A later stage can read settled uptake or growth; a same-stage proposal cannot claim that an unsettled amount has already been accepted.

Stateful modules declare state shape, units and policies for division, death and environment migration. Stochastic modules use the context RNG with stable node, owner, entity and purpose keys; they must declare RNG use so checkpoints preserve continuation. Modules that affect shared inventory return existing typed effects and declare target grants and workspace budgets. Adding a new shared effect or resource changes the system contract and requires a core contribution.

Test the governing equation separately, then test a complete graph, invalid parameters, checkpoint continuation and rollback. Add conservation and lifecycle checks where applicable. `tests/test_examples.py` builds this example's ZIP, installs it into an isolated store, runs it through the ordinary engine and compares checkpoint continuation. `tests/test_module_system.py` covers execution permissions and stage ordering.
