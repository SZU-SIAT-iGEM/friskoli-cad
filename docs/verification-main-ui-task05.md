# Main CAD integration verification — 2026-10-02

This records main-CAD wiring checks, not a 10,000-step performance claim. All browser work used headless Chromium. Sizes were browser simulations, not physical-device tests. Screenshots and generated downloads were kept outside the repository.

## Verified behavior

- Population inspectors expose the actual owner-bound graph nodes through the shared parameter editor. Editing `motility.speed_um_s` from 5 to 7 appeared in Workflow; Undo restored 5. Locking the population disabled both editors. Result-cell parameters use frozen run input and are disabled.
- Applying the official Complete PTS A mechanism worked through the API and the main Undo restored the prior branch. Empty template deletion history has a disabled Undo button. Arrange and Undo were exercised.
- Domain properties explain thin-layer thickness and physical count × spacing. Checks can move from Evidence to the actual domain properties for a `/project` resource diagnostic.
- Task 0.5 negotiates from advertised versions and keeps earlier contracts readable. Saved concentration previews use `field_domain` with matching physical extents and `aggregation=volume_mean`. XYZ slice counts and positions use preview spacing; computation spacing remains explicit.
- Backend options come from service capabilities. CUDA is labeled as diffusion only, with physiology on CPU. An unavailable requested backend remains visibly unavailable and is not silently substituted.
- The registered 128 µm B lifecycle example loads as an ordinary project from template metadata. Its recommended `.01 s`, 10,000 steps, every 100 steps, `[8,8,8]` preview and final NPZ are visible. Its 256³ computation grid gives 0.5 µm computation spacing and 4 µm preview spacing. It was loaded and preflighted, not fully run in this UI check. The small independent service correctly rejected 16,777,216 voxels against its default 262,144 voxel limit.

## Real small runs and exports

- `run_0f4b45e81ee7441f9b345ba53eef5a7b`: PTS A, two steps, Task 0.5, preview stride `[2,2,1]`. XY/XZ/YZ controls were exercised. The display reported 20 × 20 × 2 µm preview versus 10 × 10 × 2 µm computation spacing. Refresh restored the same run through IndexedDB metadata and fetched published chunks.
- Its final NPZ was downloaded independently: 2,497 bytes; SHA-256 `3dae5d617f2280cd564d0d61fac6791753cd83cc39b1fdef9d662c44c9f0497a`, matching the manifest. The browser does not automatically parse or attach this binary to replay JSON.
- `design-1790950943548`, candidate 001, seed 0, batch size 1: completed one two-step repeat. Refresh preserved selection and completed history; OMEX downloaded successfully. Preferences are separate from the frozen scientific design.
- `run_95985b7671144a45b6a0600bfafcdebf`: a small ordinary PTS A input with valid configured radial observations, two steps. Results directly displayed mean distance, inward displacement, per-radius enrichment/arrival trends, cumulative-sphere tables, founder/descendant counts, residence and first-arrival values. Downloaded CSV contained the recorded radial columns. This is a UI contract check, not an N5 scientific result.

## Display and persistence

Workflow and Results were checked at 390×844, 844×390, 1024×768 and 1440×1000; no document-level horizontal overflow. Long field legends wrap below controls and no longer overlap run provenance. Keyboard resizing of the Data panel was exercised.

`TaskStore.storedRecords()` excludes replay, manifest, and duplicate project/settings. Its 16 MiB bound covers frozen submissions and recovery metadata, not replay arrays. On refresh, replay/manifest start empty and are recovered from the run ID. Runtime browser memory is not bounded by that storage limit: decoded chunk cache plus assembled replay can exceed serialized JSON size. No browser peak-memory or wall-clock acceleration claim is made here.

## Automated checks

`node --test tests/*.test.mjs`: 149 tests, 145 passed, 4 skipped, 0 failed. Added checks cover exact owner-bound parameter references and locks, batch preference restoration, Task 0.5 backend/preview negotiation, preview physical extent validation, and configured radial metric validation. JavaScript syntax checks passed.
