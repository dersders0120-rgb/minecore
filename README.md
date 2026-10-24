# MineCore

MineCore is an integrated, desktop-first mining engineering platform: drillhole
data, geological modelling, geostatistics, block modelling, resource evaluation,
open-pit and underground design, and scheduling, built on one shared project
model, unit system and geometry kernel.

Proprietary software. All rights reserved. See `LICENSE`.

## Layout

| Path | Contents |
| --- | --- |
| `crates/mc-core` | Rust core engine: contracts, geometry, numerics. No GUI, no Python. |
| `crates/mc-py` | PyO3 bindings, built as `minecore._core`. |
| `python/minecore` | Python application layer, CLI, scripting API. |
| `tests/python` | Python tests (pytest + hypothesis). |
| `tools` | Development tooling, including `verify_submission.py`. |
| `docs` | Architecture, roadmap, ADRs, task briefs, session handoff. |

## Development

Requirements: Rust (pinned in `rust-toolchain.toml`) and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                      # create .venv and build the native extension
uv run minecore info         # check the Rust-Python bridge
uv run pytest                # Python tests
cargo test --workspace       # Rust tests
```

Quality gates (all must pass before merge):

```sh
uv run ruff check . && uv run ruff format --check . && uv run mypy
cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace && uv run pytest
```

Read `CLAUDE.md` before contributing; it holds the rules every human and AI
contributor follows.
