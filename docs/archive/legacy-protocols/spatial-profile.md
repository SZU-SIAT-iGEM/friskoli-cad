# Spatial execution profile

`spatial-unbiased-v1` is explicit and uses Project 0.4.0, Catalog 0.3.0, Task 0.3.0 and CompiledPlan 0.3.0. The legacy and conservative PTS schemas and scheduling remain unchanged. Example: `GET /api/examples/spatial-baseline`. Catalog: `GET /api/catalog?execution_profile=spatial-unbiased-v1`.

Project 0.4.0 requires a nonnegative IEEE-754 safe integer `random_seed`. Obstacles, finite degradable material boxes and finite local attractant sources are graph nodes, with no second entity representation in the project. Catalog `environment.node@1` properties name manifest parameters and must match their number/string type and unit; both initializer and data module references resolve to environment modules. Existing `population.block@1` keeps its contract.

The schedule saved in the plan is the same `spatial_schedule` used by the numerical runtime: prepare step-start requests, propose contact degradation, release finite inventory, diffuse with stable substeps, settle local shared uptake, advance signals, advance unbiased motion, commit atomically. Cell growth and division are outside this profile. PTS signals are recorded but do not bias motion. Source, geometry and rate parameters are constructed numerical examples, not a calibrated biological model.

The task seed is `execution.seed`; the worker passes it directly to the simulation without editing the frozen project. Browser submissions default to `settings.seed ?? project.random_seed`. Provenance 0.2.0 records `pcg64-sha256-key-v1`, the actual execution seed and whether any nonempty motion group has a positive tumble rate. The requested and project seeds can differ, and both remain inspectable.

Task 0.3.0 accepts boolean `output_plan.include_fields`, defaulting to true in the browser. When true, every complete FrameEnvelope includes `concentrations`, using the synchronous replay shape: species keys with `unit: "uM"` and finite nonnegative `values_zyx` arrays matching the domain. Only active field species are emitted. Missing requested species, false empty placeholders, wrong units and malformed arrays are rejected by the browser. When false, the task transports only cell frames. Task 0.1.0 and 0.2.0 still require false.

Complete chunks retain atomic publication and SHA-256 integrity checks; requested field arrays are included in static output estimates and actual chunk/output byte limits. Project validation limits this profile to 256 cells, 10000 voxels and 8 declared species before simulation array allocation. Service limits may reduce these bounds further. Synchronous replay also checks its total field-value budget before allocation.

`capabilities.task` retains the legacy task contract; `capabilities.task_profiles["spatial-unbiased-v1"]` advertises the new contract. Task pause, resume and checkpoint capabilities remain false. Python simulation checkpoint support does not imply a persisted service continuation API. Common errors use the explicit Task 0.1.0 Error reference. See [OpenAPI 0.3](tasks-openapi-v0.3.json).

The separate [checkpoint file API and CLI](../checkpoint-files.md) wrap the frozen project and numerical checkpoint in a self-contained, bounded JSON document. This M4 entry point restores an independent simulation, preserving the actual execution seed and committed clock; it does not change the server task state machine or import checkpoint files through the project editor.


## Solid material and degradation

A degradable material box is a physical solid with finite inventory. It does not release molecules by itself. `reaction.contact_degradation` requests release from step-start capsule-to-box geometry, explicit surface enzyme copies, a finite contact distance, and `kcat_s * enzyme_copies * dt_s`. A distant blocked movement is not a contact. Shared demands are scaled against remaining solid inventory; released molecules enter the external field and require a separate uptake mechanism to enter a cell. The pure reference proposal exposes per-cell contributions and a conservation ledger; its inputs are immutable. A finite self-releasing attractant source is a separate object with different semantics.

Catalog 0.3 entries may declare `provides_roles` and validated `default_parameters`. Material placement declares an initializer requirement with `role: "material.degradation"`, `default_module: "reaction.contact_degradation@1.0.0"` and `scope: "environment"`. The default must resolve to a module that explicitly provides that role. The editor creates the compatible global mechanism and protects the last provider while a dependent material remains. An alternative provider must explicitly declare the role. New parameter values supplied by the editor carry example provenance describing them as constructed demonstration inputs, without a claim of measurement or calibration.


Every Task 0.3 FrameEnvelope also requires `object_states`, regardless of `include_fields`. It maps each registered finite inventory node ID to `{object_type, remaining_molecules}`. Types are `material.degradable_box` and `source.attractant`; values must be finite, nonnegative and no greater than the frozen initial inventory. The exact complete node set is validated by the worker and browser. Exhausted material reports zero and its box disappears at the atomic geometry commit. Synchronous spatial replay uses the same object state shape; old profile payloads remain unchanged.

The compiled schedule explicitly records `field_sources` (field node, species, matching source and material node IDs) and `degradation_participants` (material and enzyme node IDs). These freeze the global species ownership reads that do not appear as ordinary graph edges.
