# Local finite-inventory fields: M4 reference

`engine/local_fields.py` implements independently callable spatial fields. Its
species identifiers have no built-in nutritional meaning. In the current PTS
example, soluble nutrient is both the uptake species and the chemotactic input;
solid material is a separate object that releases this nutrient. An independent
MCP ligand can instead have no uptake node. Diffusivity is required for each species and is not
inferred from its name. This implementation does not replace a spatial field with
a well-mixed bulk reservoir.

## Public inputs and snapshots

- `FieldSpecies(id, initial_uM, diffusivity_um2_s)`: initial concentration is a
  nonnegative scalar or a nonnegative array with `GridDomain.shape == (nz,ny,nx)`.
  Scalars initialize fluid voxels only. Explicit arrays must have zero in solids.
- `LocalSource(id, species, center_um, radius_um, remaining_molecules,
  release_rate_molecules_s)`: center is XYZ in micrometres, stock is molecule,
  rate is molecule/s. A radius of zero injects into the containing voxel. A
  positive radius selects voxel centers inside a sphere, also retaining the
  center's voxel when the radius is smaller than grid spacing. Release is uniform
  among these voxels. The center must lie inside the domain, and the discrete
  support must not overlap solid voxels. A sphere at a domain edge uses only
  in-domain voxels; all released mass stays inside the domain.
- `SolidAABB(min_um, max_um)`: XYZ coordinates bound whole voxels. Bounds must
  align with grid faces (an eight-ULP arithmetic allowance handles values such
  as `3 * 0.1`). Partial/cut cells and arbitrary obstacle geometry are unsupported.
- `make_local_field_state(grid, species, sources=(), obstacles=())` validates
  these inputs and builds an immutable `LocalFieldState`. Concentrations are
  flattened ZYX tuples, and species mappings are read-only. `sources` stores the
  current finite stock, `blocked` stores the voxel mask, and `revision` advances
  once per proposed step. Inputs are copied, including initial arrays.
- `copy_concentrations(state)` returns independent writable ZYX NumPy arrays.
- `local_field_state_to_dict` and `local_field_state_from_dict` round-trip a
  versioned JSON-compatible checkpoint, validating the grid, species, masks,
  concentrations, source stocks and supports. There is no pickle execution.

Default construction/checkpoint budgets are 250,000 voxels and 1,000,000
species/source-by-voxel values. The pure reference stores immutable Python
tuples, so its memory footprint exceeds that of a contiguous float array. It is
intended for small grids and auditability, not a production large-grid backend.

Transfers additionally enforce a precision budget relative to the released or
accepted amount (`1e-10 * abs(amount) + 8 ulp(amount)`), using exact rational
differences of the stored binary64 values. A large reservoir cannot hide a
badly rounded small debit. A zero-uptake step does not rewrite a voxel through a
concentration/molecule unit round-trip. Unrepresentable transfers reject the
candidate; they are not silently discarded.

## One full field step and cell coupling

`propose_local_field_step(state, dt_s, cell_ids=(), positions_um=(),
requested_uptake_molecules_s=None)` performs this first-order operator split:

1. Release `min(rate * dt, remaining stock)` from each finite source. Each source
   records its release and retains the remainder. Exhausted and zero-rate sources
   are valid. A source does not disappear when exhausted.
2. Diffuse each field for `dt`, using enough common explicit substeps to preserve
   nonnegative diffusion weights. Outer boundaries and solid faces have zero flux.
3. Sample concentration and gradient at the supplied, fixed cell positions.
   These values are returned as `samples_before_uptake`.
4. Settle all explicit cell uptake requests simultaneously within each sampled
   voxel, then subtract the accepted amount from that same voxel's inventory.
   Return accepted molecule amounts in each species tuple, in `cell_ids` order.

The caller may first call `sample_local_fields(state, positions)` at the start
of the enclosing simulation step and compute kinetic uptake requests from those
values. Such requests remain fixed during the field proposal; the field engine
does not recompute them after release/diffusion. In this split, newly released or
diffused molecules can satisfy an already requested uptake. The post-diffusion
sample is not a substitute for the caller's step-start kinetic sample. Cells do
not move during a field proposal; movement and geometry belong to the enclosing
simulation integrator. Re-sampling after a move uses the new containing voxel.

`propose_settlement(..., policy="proportional")` supplies the existing
exact-rational finite-stock reference settlement. All cells in one voxel share
one reservoir. Shortage scales requests proportionally, without borrowing from
another voxel or species. This avoids constructing a dense cell-by-grid support
matrix. Omitting a species request means zero uptake. Empty cell populations,
zero concentration, absent sources and exhausted sources are normal states.

The proposal contains `before`, `after`, `samples_before_uptake`,
`accepted_uptake_molecules`, `source_released_molecules`, per-species `ledgers`,
and the diffusion `substeps` count. No input is changed. A surrounding step can
discard this proposal after a reaction, motion, or population failure. Commit is
an explicit replacement of the owning simulation's field snapshot after every
coupled operation succeeds; the caller must reject stale revisions if it supports
concurrent proposals.

## Numerical definition and accounting

The conversion is the existing `GridDomain.molecules_per_uM_voxel`:

```text
voxel molecule = concentration_uM × 602.214076 × dx_um × dy_um × dz_um
```

For every unblocked pair of face-neighbor voxels `i,j`, diffusion adds
`D * h / spacing² * (Cj - Ci)` to `i` and its negative to `j`. No transfer is
computed across a solid face. Equal voxel volumes therefore make the pairwise
update conservative. An interior voxel's own coefficient is at least
`1 - 2 D h Σ(1/spacing²)`; axes with only one voxel contribute no term. Taking
`h <= 0.9 / (2 D Σ(1/spacing²))` leaves a nonnegative convex combination with
roundoff margin. A single-voxel field or `D=0` has no diffusion restriction.
The implementation derives the bound from the existing diffusion operator and
uses `ceil(dt / (0.9 * limit))` common substeps for all species.

Source release, diffusion and uptake receive separate roundoff checks. The
ledger also reports field mass before release, after release, after diffusion,
and after uptake, plus source stock before/after, released and accepted amounts,
and the whole-step residual:

```text
residual = field_before + source_before - source_after - accepted - field_after
```

Binary64 rounding is reported with a bound proportional to `ulp(mass scale)`
and the operation count. Negative concentration is never clipped to zero and
mass is never silently normalized. Invalid numerics, an unrepresentable inventory
transfer or a residual outside the bound rejects the proposal. Per-cell and
per-voxel settlement retain the stricter exact-rational accounting implemented
inside `local_fields.py`.

Default step budgets allow 10,000 diffusion substeps and 20,000,000 estimated
work items. Grid-by-species-by-substep work, source support work and cell sampling
work are estimated before allocating field-sized step buffers. Budget excess
raises `SimulationError`; there is no silent coarsening or concentration clipping.
The work budget is a deterministic workload guard, not a measured time guarantee.

## Sampling limitations and checks

Concentration and uptake use the containing voxel (nearest voxel center except
ties); samples are piecewise constant, with discontinuities at grid faces.
Gradients use central face-neighbor finite differences. An out-of-domain or solid
neighbor contributes a reflected ghost value equal to the current voxel, so a
gradient never reads concentration through a wall. The returned boundary-cell
gradient is the voxel-center estimate; it is not a reconstructed boundary-face
derivative. No interpolation, arbitrary cut-cell support, adaptive grid,
advection, surface adsorption or obstacle permeability is claimed.

Tests cover anisotropic 3D and thin-layer diffusion, conservation and positivity,
automatic substeps and resource rejection, source exhaustion and source radius,
empty cells, shared-voxel shortage, spatial support at a voxel boundary, explicit
substrate versus unconsumed attractant, impermeable aligned walls, invalid inputs,
checkpoint validation, and immutable/discardable proposals.
