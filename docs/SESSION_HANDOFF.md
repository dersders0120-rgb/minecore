# Session handoff

Updated: 2026-10-24 (session 1, architect)

## Current milestone
M0 done (PR #1, CI green, awaiting merge). M1 in progress.

## Pull requests (merge in this order)
1. #1 `m0-foundation` — M0 foundation. CI green on Windows + Linux.
2. #2 `m1-units` (base `m0-foundation`) — T-003 units contract.
3. #3 `m1-project-package` (base `m1-units`) — T-005 project package, plus the
   verified T-002 and T-009 deliverables.
After each merge, retarget the next PR to `main`.

## Done this session (with evidence)
- M0: Rust workspace + Python package + CLI, quality gates, CI, licence policy,
  verification script, governance docs (ADR-0001, ADR-0002).
- T-003 units (ADR-0003): exact rational conversions, strict parser, quantity
  kinds, Python API. 30 Rust tests, 24 Python tests.
- T-005 project package (ADR-0004): `.mcproj`, versioned SQLite schema,
  transactional migrations with backup, OS single-writer lock, CLI. 21 tests.
- T-009 (ChatGPT) desurvey reference cases: independently re-computed by the
  architect with a different formulation; max deviation 7.8e-7 m (output
  rounding). Check runs in CI (`tests/python/test_reference_desurvey.py`).
- T-002 (Gemini) capability matrix reviewed and committed with a review note;
  findings folded into ROADMAP (M3, M4, M7, later estimation work).
- Local results on the last commit: ruff, mypy strict, clippy -D warnings,
  cargo-deny clean; cargo test 30 passed; pytest 136 passed.

## Not yet verified
- CI for #2 and #3 (runs on push).
- Gemini's citations (web access is restricted in the dev environment).

## Next actions
1. T-004 CRS + local mine grid contract (PROJ via pyproj; licence check first).
2. T-006 operation log and undo/redo (migration 0002 — first real migration).
3. T-007 error hierarchy and job/cancellation model.
4. M2 planning: drillhole import contracts; first DeepSeek/Claude-2 briefs
   (CSV/XLSX importers with Turkish locale handling, synthetic data generator).

## Owner owes
- Merge #1, then #2, then #3.
- An anonymised drillhole sample (collar, survey, assay, lithology) for M2.
