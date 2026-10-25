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

## Submitted, awaiting architect review
- T-008 (DeepSeek) synthetic drillhole generator.
- T-010 (Gemini) Turkish CRS practice research.
- T-011 (Haiku) EN–TR terminology glossary.

## Next actions
1. Review and integrate T-008, T-010, T-011.
2. T-006 operation log and undo/redo (migration 0002).
3. T-007 error hierarchy and job/cancellation model; then close M1.
4. M2 drillhole import contracts; delegate importers.

## Owner owes
- Merge the T-004 PR.
- An anonymised drillhole sample (collar, survey, assay, lithology) for M2.
