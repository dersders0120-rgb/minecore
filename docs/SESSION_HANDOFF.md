# Session handoff

Updated: 2026-10-25 (session 1, architect)

## Current milestone
M0 done (PR #1). M1 in progress: units (#2) and project package (#3) merged;
CRS + local grid (T-004) in the `m1-crs` PR.

## Done (with evidence)
- T-004 (ADR-0005, ADR-0006): `mc-core::coords` local grid + least-squares fit
  (14 Rust tests incl. proptests and an optimality check); `minecore.coords`
  PROJ layer with no-ballpark, single-operation, missing-grid-refusal policy;
  working system stored in the project; CLI `crs info`, `grid fit`,
  `project set-crs`, `project set-local-grid`. PROJ TM/UTM for all Turkish
  zones checked against an independent Krüger series (< 1 mm).
- Local gates on the T-004 commit: ruff, mypy strict, clippy -D warnings,
  cargo-deny clean; cargo test 44 passed; pytest 188 passed.

## Also done
- T-006/T-007 (ADR-0007): operation log with trigger-based generic undo/redo
  (migration 0002, the first real migration), jobs/progress/cancellation,
  error hierarchy, CLI `project history|undo|redo`. PR `m1-oplog`.

## Assigned to other models (prompts in project files)
- T-012 ChatGPT compositing reference cases; T-013 DeepSeek CSV dialect
  detector; T-014 Gemini QA/QC research.

## Reviewed (PR #5)
- T-008 (DeepSeek) synthetic drillhole generator.
- T-010 (Gemini) Turkish CRS practice research.
- T-011 (Haiku) EN–TR terminology glossary.

## Next actions
1. Merge #4, #5, m1-oplog PR; close M1.
2. M2 drillhole data contract (schema, validation issue model, bulk writer).
3. Review T-012/T-013/T-014 when delivered.

## Owner owes
- Merge the T-004 PR.
- An anonymised drillhole sample (collar, survey, assay, lithology) for M2.
