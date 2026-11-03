# MineCore — rules for every contributor (human or AI)

MineCore is an open source (MIT), desktop-first mining engineering platform aiming at
the depth of Datamine Studio, Micromine, Deswik, Surpac and Vulcan. It is built
as professional engineering software, never as a demo. The product owner is a
mining engineer; reply to them in Turkish. Code, identifiers and docs are English.

## Non-negotiable rules
1. Evidence over claims. Work is done only when tests you actually ran pass.
   Never write that something works unless you executed it.
2. No placeholders. No inert buttons, hardcoded results, fake progress, stub
   algorithms or skipped tests. Unfinished work goes to `docs/KNOWN_LIMITATIONS.md`.
3. Contracts have one owner: the architect. Units, coordinates, geometry and
   block model schema live in `crates/mc-core`; the project format lives in
   `python/minecore/project` (SQL migrations). Changes need an ADR and a
   compatibility or migration test. Never edit an applied migration.
4. Independent verification. Numerical algorithms are checked against analytic
   cases, synthetic data with known answers, or an independent implementation.
5. One-way dependencies: core <- domain modules <- workbench. Core never imports
   GUI or Python code. Every feature must run headless from the CLI.
6. Licences: permissive dependencies only (see `deny.toml`). No GPL/AGPL. PySide6
   (LGPL) only via dynamic linking. Every new dependency needs architect approval.
7. Never copy proprietary code, assets or undocumented file formats.
8. Never present resource classification as compliant with JORC/NI 43-101/PERC
   or similar codes; that requires a competent person.

## Repository map
- `crates/mc-core` — Rust core engine (contracts, geometry, numerics).
- `crates/mc-py` — PyO3 bindings, built as `minecore._core`.
- `python/minecore` — application layer, CLI (`minecore`), scripting API.
- `python/minecore/project` — `.mcproj` package, schema migrations (ADR-0004).
- `tests/python` — pytest + hypothesis. Rust unit tests live next to the code.
- `tools/verify_submission.py` — automatic check of delegated work.
- `docs/ARCHITECTURE.md` — layers, conventions, contract ownership.
- `docs/ROADMAP.md` — milestones M0–M12 with exit criteria.
- `docs/adr/` — architecture decision records.
- `docs/tasks/` — task briefs (`T-NNN.md`) for delegated work.
- `docs/SESSION_HANDOFF.md` — current state and next actions. Read it first.
- `docs/KNOWN_LIMITATIONS.md` — what is incomplete and why.
- `project-status.json` — machine-readable milestone and task status.

## Commands
```sh
uv sync                                   # build native extension + dev env
uv run minecore info                      # CLI smoke test
uv run ruff check . && uv run ruff format --check . && uv run mypy
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace && uv run pytest
uv run python tools/verify_submission.py T-NNN   # verify a delegated task
```
All of these must pass before a merge. CI runs them on Windows and Linux.

## Working on a delegated task (T-NNN)
1. Read `docs/tasks/T-NNN.md` and only the files it names.
2. Change only the paths in `allowed_paths`. Never edit `protected_paths`,
   acceptance tests, contracts, manifests or lock files.
3. Make the brief's acceptance command pass, then run every gate above.
4. Report: files changed, commands run and their real output, open issues.

## Session protocol (architect)
- Start: read `docs/SESSION_HANDOFF.md` and `project-status.json`; reconcile
  them with the repository; list open tasks and what the owner owes.
- End: update `project-status.json`, `docs/SESSION_HANDOFF.md` and, if a
  decision was made, an ADR.
- Task states: BACKLOG, READY, ASSIGNED, SUBMITTED, VERIFIED, INTEGRATED,
  BLOCKED, REJECTED. VERIFIED requires a passing `verify_submission.py` run.
