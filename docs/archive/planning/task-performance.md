# Task transport performance and long-duration preparation

**Current priority (2026-10-03):** the actual N5 256³/200-cell task took 25m50s to advance 745 steps and was paused by the user. The [full-scene diagnosis](../verification/verification-n5-performance.md) records real profiles, scientific observations, and P01–P05 follow-up work. This analysis has not delivered a new speedup. Historical small-scene numbers below do not establish acceptable performance for N5. The product target includes wall time until observable chemotactic aggregation, not only step throughput.

Current long-duration implementation (2026-10-03): Task 0.6 provides 4,320,000-step admission, sparse output, bounded array transport, complete binary checkpoints, and automatic committed-boundary worker rotation. The 43,200 × 1 s foundation task has run to completion; it is not a PTS 4,320,000-step result. See [Task 0.6](../legacy-protocols/task-contract-0.6.md) and [verification](../verification/verification-system-tasks.md). Sections below retain their dated historical measurements.

## Conservative collision broad phase (2026-10-02)

`collision.guard_motion` retains the exact capsule spine/segment, capsule/box,
wall clearance, shortest-arc interpolation, stable ID ordering, whole-proposal
rejection and blocked-cell propagation. `use_broad_phase=False` selects the
unchanged full-pair reference for comparisons. No RNG call or tolerance changed.

The optimized path first compares center coordinates using each capsule's
**total length / 2** bounding sphere, including both hemispherical caps.
Initial pairs separated beyond these radii are excluded from overlap tests.
For a moving pair, the filter uses midpoint centers, the two bounding radii,
and half the sum of the original `_Path.bound` values. It skips the pair only
when this lower bound exceeds the original tolerance plus a conservative
floating-point margin (`512 * eps * coordinate_scale`). The same condition
filters distant boxes. This proves that the old `_certify` would pass its first
midpoint test; mere nonintersection of swept AABBs is intentionally insufficient
because it could otherwise change `budget_exhausted` diagnostics. All uncertain
and nearby cases enter the original narrow phase unchanged. Filtering is
recomputed after blocked paths change. Arrays use N × N workspace without an
N × N × 3 temporary; the current runtime population limit bounds this workspace.

### Measured guard cost

Windows 10 build 22621, Python 3.11.9, shared development host. Each measurement
uses **200 capsules**, length 2 µm, diameter 0.8 µm, 128³ µm domain, translation
0.1 µm. ABBA order is full-pair, optimized, optimized, full-pair, with all output
capsules, blocked IDs and contact/reason tuples checked for exact equality.
Times below are means of the two observations, not statistical confidence bounds.

| Geometry workload | Full-pair seconds | Optimized seconds | Guard speedup |
| --- | ---: | ---: | ---: |
| Sparse, 11 µm spacing | 5.0540 | 0.0300 | 168.4× |
| Compact, 2.4 µm spacing | 5.6694 | 0.0314 | 180.8× |
| Sparse plus 32 distant boxes | 5.8880 | 0.0518 | 113.8× |
| Near contact, 2.02 µm spacing | 4.7229 | 0.4955 | 9.5× |
| Near contact plus 90° turns | 5.2916 | 1.0531 | 5.0× |

Actual narrow-phase evaluation counts decrease: sparse 20,100 → 200;
near-contact 22,620 → 3,404; turning 26,184 → 6,968. This diagnostic counts
work actually performed and is allowed to change. Physical results and contact
reasons are unchanged in these checks.

A separate full-run ABBA check used the existing `foundation-pts-a` recipe,
32 cells, original coarse demonstration field, 3 steps at dt=0.01 s: reference
1.6011/1.6562 s versus optimized 0.2361/0.2283 s (7.0×). Complete checkpoint,
including RNG state, matched exactly. This excludes preparation time and does
**not** measure the requested large fine-grid 10,000-step acceptance case.

### Evidence and remaining work

The first profile (32 cells, 3 steps, coarse field) spent 2.435 of 3.044 profiled
seconds in 16 `guard_motion` calls (~80%). Segment distance and repeated
Capsule construction dominated; generic deep-copy was not a leading hotspot.
Motion divides the whole population at each sampled turn time, so one numerical
step can call the guard many times. Physiology also checks the final capsules
even when no growth is proposed. Those call sites are unchanged here.

`test_collision_broadphase.py` compares full-pair results for seeded random
translation/rotation, high-speed crossings, near-parallel/tangent cases, walls,
obstacles, initial overlap, blocked propagation, tiny certification budgets,
empty groups and sphere limits. It additionally compares complete six-step
checkpoints for PTS/reserve and lifecycle examples, including random streams.
Combined existing collision/cross-product suites passed 44 tests, followed by
18 broad-phase tests including the two added checkpoint cases. This is not a
claim of universal bitwise equivalence across every floating-point platform.

Benchmark scripts and outputs remain in the temporary audit directory, outside
the source package. A 10,000-step full-model throughput estimate must include
field diffusion, lifecycle population changes and the actual turn count; the
isolated guard speedup must not be presented as the full simulation speedup.

## Measured bottleneck (2026-10-02)

The test uses the unchanged `chemotaxis-pts-a` example, 200 numerical steps,
`dt_s=0.05`, seed 17, default 200-voxel domain, complete field output, and
`TaskLimits(wall_time_s=1800)`. Each run uses a fresh temporary task directory.
The parent `_publish` is instrumented with cProfile; `_write_atomic` and `_dump`
also record time and counts. Thus these are instrumented wall times, not an
unprofiled throughput promise. Other development/test processes shared the host.

| Output interval | Before | After | Parent publish before/after | Publish calls before/after |
| --- | ---: | ---: | ---: | ---: |
| Every numerical step | 45.92 s | 18.71 s | 31.05 / 5.35 s | 201 / 201 |
| Every 10 numerical steps | 29.98 s | 11.72 s | 16.58 / 0.70 s | 201 / 42 |

For interval 1, the old `_publish` spent 18.86 s in `canonical_loads`, repeatedly
reading and validating the growing manifest plus unchanged plan/provenance.
Result-file writes accounted for 1.29 s. This supported removing repeated work
on committed metadata before attempting any numerical or filesystem-durability
changes. With interval 10, all 201 steps still previously caused publication,
including 180 steps with no requested output frame.

The new interval-1 run serialized 499,074 database bytes through `_dump`, down
from 9,246,270. Interval 10 serialized 123,612, down from 4,448,470. The precise
number of progress-only events depends on elapsed compute time; requested output
frames are deterministic.

Benchmark scripts, profiles, complete frame arrays and summaries are outside the
repository in the session temporary directory
`friskoli-task-perf-57c29b935a3e4cfeb9984883054cacc4`.

## Preserved execution and publication rules

- Every numerical step still calls the solver with the submitted `dt_s`. Cell,
  voxel, lifecycle-event and output-state checks remain active.
- The worker sends one byte-bounded canonical message over a duplex pipe, then
  waits. There is at most one in-flight message, bounded by `chunk_bytes + 4096`;
  a slow publisher cannot accumulate an unbounded queue of output frames.
- The parent still writes each requested frame as its original single-frame
  chunk, fsyncs the result file, atomically replaces it, and commits the task,
  event and chunk index in one SQLite transaction before acknowledging it.
  Temporary worker spool files were never durable results and are no longer
  written. Public chunk layout, frame sequence, hash and scientific contents are
  unchanged.
- The initial frame, every requested output frame and final frame are always
  published. Between them, progress-only messages are emitted after roughly
  0.25 seconds of computation, at the next complete step. No calculation is
  skipped. Cancellation is checked by the parent at these publication points,
  so sparse output does not delay cancellation until the next saved frame.
- RSS and wall-time guards remain in the parent. Forced termination, worker or
  host failure and write errors retain only previously committed partial output.
  Cancellation may take one current numerical step and durable publication time
  in addition to the progress interval; this is not a hard latency guarantee.
- A manifest is assembled on demand from the committed task and ordered chunk
  index under the publication lock. Old databases use the same authoritative
  indexes. The legacy `tasks.manifest` column remains readable in SQLite but is
  no longer maintained or used as the API's source of truth. No checkpoint,
  resume or task contract format changes are introduced.

## Output-equivalence evidence

Before/after arrays of complete frame envelopes are byte-identical after the
unchanged canonical JSON encoding (task run IDs are outside the frame envelopes):

| Interval | Frame count | Canonical frame-array SHA-256 |
| --- | ---: | --- |
| 1 | 201 | `bd8a119e0f01c27f7a02da2c6e16f38e7f18c55cbc3b36ba00428134d18e3471` |
| 10 | 21 | `0e60ef61fdd1cdae07178ca9ef22946008038b2b00cc17ff5168eeb2f00cab96` |

`tests/test_task_performance.py` additionally compares complete scientific frame
bytes to the direct solver at `(dt_s, output interval)` pairs `(0.05, 1)`,
`(0.05, 7)` and `(0.1, 11)`. It verifies frame order, chunk byte lengths and hashes,
unchanged submitted inputs, cancellation between sparse frames, and restart
recovery of that partial result. Existing real-worker tests cover wall/RSS/output
limits, six forced deaths around durable publish/ack boundaries, host crash,
write failure after rename, cancel/final-publication races and integrity checks.

## What is still required for 12 simulated hours

At `dt_s=0.05`, 12 hours requires **864,000 numerical steps**. This optimization
does not claim that such a run is supported or has been completed. No step,
output, memory or duration limit was increased in this performance change.

The following constraints must be handled explicitly before enabling it:

1. `tasks/service.py`: `TaskLimits.steps` is still 10,000. A new supported limit
   must be published consistently for the intended profiles. `_admit` currently
   searches output-interval suggestions only through 10,000; any larger interval
   capability needs a corresponding change. Static output limits and actual
   execution wall/RSS limits must remain enforced.
2. `protocol/schemas/task-v0.4.schema.json`: submission `steps` and
   `frame_every_steps` have positive-integer constraints but no hard 10,000 cap;
   the service admission provides that cap. Do not edit every historical task
   format unnecessarily. Review safe-integer handling and capability boundaries.
3. `web/workspace.mjs`: imported/saved run settings cap both steps and output
   interval at 10,000. Workspace schemas v0.4, v0.5 and v0.6 also carry these
   maxima. A versioned workspace migration/compatibility decision is needed.
4. `web/task-store.mjs` and `web/app.mjs`: output-interval validation caps it at
   10,000. `web/index.html` has an initial step max of 10,000; runtime step input
   already follows capabilities. A duration UI must preserve the chosen `dt_s`
   and explain exact step rounding without silently altering scientific input.
5. `design.py` and design schemas v0.1/v0.2 independently cap steps/interval at
   10,000. Design/batch export and import need an explicit compatibility decision
   if hours-long designs are intended.
6. Synchronous `replay_service.py` limits should remain separate. Enabling long
   asynchronous tasks must not imply that the synchronous in-memory replay can
   retain the same histories.
7. Benchmark the actual hours-long scientific workload and output plan. Output
   sampling reduces stored data, not numerical work. The default 128 MiB output
   budget, 4 MiB frame budget, lifecycle-event buffer, result-loading cost and
   30-minute local CLI wall-time setting still apply. A different output interval
   must be chosen explicitly by the user; increased `dt_s` is not a substitute.

The new `life.starvation_hazard` module is recognized as an RNG consumer in task
provenance; `metabolism.reserve_balance` by itself is deterministic. The existing
division capacity estimate remains specific to division-capable models.
