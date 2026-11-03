# Contributing to MineCore

Thank you for helping build an open source mining engineering platform.
Mining engineers, geologists, geostatisticians and software developers are all
welcome. You do not need to know Rust to contribute: most of the application,
the CLI and the tests are Python.

## Where to start

- Issues labelled [`good first issue`](https://github.com/dersders0120-rgb/minecore/labels/good%20first%20issue)
  are small and self-contained.
- [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) lists what MineCore
  cannot do yet. Each line is a possible contribution.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) shows the milestones and their exit criteria.
- Domain knowledge counts: test datasets with known answers, worked examples
  from textbooks, and reviews of the engineering methods are as valuable as code.

Comment on an issue before starting larger work so that we can agree on the
approach.

## Development setup

Requirements: Rust (version pinned in `rust-toolchain.toml`, installed by
`rustup`) and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/dersders0120-rgb/minecore.git
cd minecore
uv sync                      # create .venv and build the native extension
uv run minecore info         # check the Rust-Python bridge
uv run pytest                # Python tests
cargo test --workspace       # Rust tests
```

## Before you open a pull request

All quality gates must pass (CI runs them on Windows and Linux):

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy
cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace && uv run pytest
```

Read [`CLAUDE.md`](CLAUDE.md). It holds the rules every human and AI
contributor follows. The most important ones:

1. **Evidence over claims.** A change is done when tests you ran pass.
2. **No placeholders.** No inert buttons, hardcoded results or stub algorithms.
   Unfinished work goes to `docs/KNOWN_LIMITATIONS.md`.
3. **Independent verification.** Numerical algorithms are checked against
   analytic cases, synthetic data with known answers, or an independent
   implementation.
4. **Contracts need an ADR.** Units, coordinates, geometry, block model schema
   and the project format change only through an ADR in `docs/adr/`.
5. **Permissive licences only.** No GPL/AGPL dependencies (see `deny.toml`).
6. **Never copy proprietary code, assets or undocumented file formats** of
   other mining software vendors.
7. **No compliance claims.** Resource classification is never presented as
   JORC, NI 43-101, PERC or similar compliant.

## Pull requests

- Keep each pull request to one change, with tests.
- Describe what you changed, how you verified it, and any limitation you found.
- Code, identifiers and documentation are in English. User-facing text may be
  English or Turkish.

By contributing you agree that your contribution is licensed under the
[MIT License](LICENSE).
