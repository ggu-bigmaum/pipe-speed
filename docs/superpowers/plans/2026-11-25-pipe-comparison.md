# Pipe comparison stages 2–4 implementation plan

**Goal:** Preserve and complete the existing Seoul water comparison, show per-pipe evidence and counterparts, and emphasize only identical-ID/identical-whole-geometry pipes when both water layers are visible.

**Authorization:** User approved all three stages on 2026-11-25 and delegated visual decisions. Stage 2 and stage 3 require separate commits. Original sources, existing DB tables, old worktrees and unrelated local files remain intact. No push or production deployment.

**Architecture:** Offline reproducible comparison package with provenance; read-only bounded web lookups; separate business state and strict display flag. Reuse the original sustained-direction and continuous-overlap work. Do not identify pipes by nonunique original IDs alone or interpret missing exact equality as absence.

**Tech stack:** Python, SQLite/PostGIS read-only audit, existing Flask and OpenLayers frontend.

## Stage 2 — comparison evidence and six business states
- [x] Locate current final comparison results and inspect saved sustained rules and regression examples.
- [x] Verify source-to-baseline mapping using original identifiers AND geometry/source evidence.
- [x] Document exact status decision precedence and thresholds; retain uncertain cases as CONFLICT with explicit reason, not artificial certainty.
- [x] Add failing tests for exact, substantial, partial, crossing, absent-counterpart, no-source and duplicate-ID cases.
- [x] Build a separate result package with all source rows, all counterpart relations, reasons, and an independent strict equality flag.
- [x] Verify conservation, duplicate handling, old regression examples, boundary differences and provenance. Commit phase 2 alone.

## Stage 3 — per-pipe lookup
- [x] Verify actual MVT identifiers and geometry lineage before establishing lookup.
- [x] Test bounded requests, ambiguous identifiers, safe text, stale requests and source-unavailable states.
- [x] Add read-only backend lookup and click-panel comparison section with status, reason and paged counterparts.
- [x] Preserve existing selection, grid inspection, layer toggles and source attributes. Commit phase 3 alone.

## Stage 4 — strict map emphasis
- [x] Inspect current map palette; implement a separate strict-match style and legend, distinguishable from click selection.
- [x] Enable only when both water layers are on. Turning either off removes emphasis and cancels stale requests.
- [x] Test strict vs similar/partial/crossing matches, viewport updates, duplicate IDs, failures and bounded caches.
- [x] Run backend, frontend and actual-browser checks; install verified local result; commit.

## Completion evidence
- [x] Record each stage commit, result counts and validation in a small tracked report; large generated data stay ignored.
- [x] Confirm local app renders comparison data and all existing groups; preserve original geometry and startup selection behavior.
