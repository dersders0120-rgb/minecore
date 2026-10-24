# Session handoff

Updated: 2026-10-24 (session 1, architect)

## Current milestone
M0 Foundation — submitted as PR from branch `m0-foundation`, awaiting CI and merge.

## Done this session (with evidence)
- Repository was empty; created Rust workspace (`mc-core`, `mc-py`) and Python
  package `minecore` built with maturin (abi3, Python >= 3.12).
- CLI `minecore info [--json]` reports the Rust core version from Python.
- `tools/verify_submission.py` + 28 unit tests.
- Local gate results (Linux, Python 3.13, Rust 1.97.0): ruff check/format clean,
  mypy strict clean (7 files), clippy `-D warnings` clean, cargo test 2 passed,
  pytest 34 passed, `cargo deny check licenses bans sources` ok.
- Governance: CLAUDE.md, AGENTS.md, ARCHITECTURE.md, ROADMAP.md, ADR-0001
  (stack), ADR-0002 (delegation), KNOWN_LIMITATIONS.md, project-status.json.
- Briefs ready for external models: T-002 (Gemini research), T-009 (ChatGPT
  desurvey reference cases).

## Not yet verified
- CI on Windows has not run yet (first run happens on the PR).

## Next actions
1. Get M0 CI green on Windows and Linux; merge.
2. T-003 units and quantities contract in `mc-core` (architect).
3. T-005 project package v1 with versioning and migration tests (architect).
4. T-004 CRS + local mine grid (architect, after T-003).
5. T-006 operation log / undo, T-007 errors and job cancellation.
6. Commit T-002 and T-009 deliverables when the owner returns them; verify T-009
   numbers independently before using them in M2 tests.

## Owner owes
- Paste T-002 prompt into Gemini and T-009 prompt into ChatGPT; upload results.
- An anonymised drillhole sample (collar, survey, assay, lithology) for M2;
  it stays out of the repository unless the owner approves a fixture subset.
