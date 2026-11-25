# Sewer pair highlighting and delivery-size estimate

## Implemented decision — 2026-11-26

The user subsequently approved sewer ID-independent whole-geometry equality after comparison-only XY rounding to three decimals (half-away-from-zero), per-object storage, click lookup, highlighting and separate stage commits. Implemented and verified: see [current result](../../sewer-comparison-20261126.md). Water criteria and source geometry remain unchanged. The portable-PC bundle is still explicitly excluded. The following text is the historical pre-approval plan, not the current runtime state.

**Current scope:** Improve emphasis contrast, prepare independent water/sewer pair handling, audit sewer evidence, and estimate a local runnable distribution without building it. Actual sewer matching and a sewer comparison package are **on hold pending the user's decision** on the geometric tolerance and identifier criterion.

**Decision update (2026-11-25):** Sewer original identifier domains have no overlap, and whole-source geometry equality at zero tolerance yields zero pairs. The user explicitly requested more consideration after a proposed 1 mm / 99% length criterion. That proposal is not approved. Diagnostic files are not operational classifications or tile flags. `/comparison/sewer/metadata` returns `503 criteria_pending`; no sewer tile sidecar route is registered. Existing water criteria remain unchanged.

**Authorized pairs:** `kict_national` + `sa001` (existing water); `kict_sewer_seoul` + `sb001` (new sewer). Other combinations never enable emphasis. With all four enabled, evaluate each pair independently.

**Design:** Keep exact source evidence separate from tile rendering. Sewer source file hashes must match the tile-conversion manifests. Retained unique `id` attributes identify sewer source objects. Establish the cross-source original-ID meaning from source evidence before applying the same-ID/full-geometry rule. Compare full source geometries with zero distance tolerance; never compare stretched/quantized MVT geometry. Preserve added correction lines and all source objects.

**Visual decision:** A near-black navy core (`#17212f`) with opaque white halo distinguishes emphasis from the orange Seoul water layer, blue/teal/purple pipe layers, red click selection, and dark distance rasters. Each pair has an explicit legend label. Selection remains above emphasis. Disable one member to restore ordinary colors.

**Scope boundary:** This prepares sewer highlighting but does not activate it or implement a new six-state sewer classification algorithm. A failure of strict equality does not mean a pipe is absent or conflicting. Do not alter source geometry, sewer topology, DB tables, or existing water decisions. Do not copy/zip/build the portable delivery requested for size estimation.

## Work and checks

- [x] Audit sewer source hashes, CRS, original identifiers, unique retained MVT IDs, connector roles, exact-equality candidates and actual tile lineage.
- [ ] **HOLD — requires a user-approved criterion:** Build a separate, reproducible sewer matching package. Do not use diagnostic tolerance results as production flags.
- [x] Parameterize bounded sidecar reads for the two explicit municipal target layers; preserve default water API and guard against cross-pair tile access. Sewer sidecar remains unregistered.
- [x] Add independent pair activation/cancellation, contrasting styles and labeled legends. Test all four layers, wrong combinations, one missing comparison package, stale responses and selection priority.
- [x] Expose explicit pending sewer metadata only; no flags, counts, or business states are assigned.
- [x] Measure existing delivery data/runtime sizes by resolved physical files, separate active assets from raw sources and duplicates, and report size ranges. No distribution assembly.
- [ ] Run backend and JS regressions plus actual-map checks, obtain independent review, commit, and install the verified local code/data.

## Files and verification responsibilities

- External audit directory: source equality diagnostics and provenance checks; not a deployed comparison package.
- `backend/comparison_tiles.py`: fixed-pair routing, unchanged MVT bytes, bounded flags.
- `backend/sewer_comparison.py`: explicit criteria-pending metadata only.
- `frontend/pipe-match-highlight.js`, `distance.js`, `distance.css`: independent pair emphasis and legend.
- `backend/tests/`: source-equality, API, two-pair rendering and existing water regression tests.
- `docs/`: source/decision evidence, pending scope and size estimate.

Current-scope completion requires source files unchanged, current source hashes verified, unknown tile IDs rejected, existing water regressions unchanged, water emphasis working while sewer explicitly stays pending, and no portable package created. The full sewer-highlighting request remains unfinished until its criterion is agreed and separately implemented and verified.
