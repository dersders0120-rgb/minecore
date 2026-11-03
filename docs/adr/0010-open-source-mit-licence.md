# ADR-0010: Open source under the MIT licence

- Status: accepted 2026-11-03
- Deciders: product owner

## Context

MineCore was developed as proprietary software in a private repository. The
product owner wants the project to be public so that mining engineers,
geologists and students can use, review and contribute to it, and so that it
can be distributed through PyPI.

## Decision

1. MineCore is licensed under the MIT licence from this commit on. `LICENSE`
   holds the full text.
2. `Cargo.toml` (`license = "MIT"`) and `pyproject.toml` (SPDX `license = "MIT"`)
   declare it. The `Private :: Do Not Upload` classifier is removed.
3. The dependency policy in `deny.toml` is unchanged: only permissive licences
   are allowed in the statically linked Rust core, so the whole product stays
   redistributable under MIT terms.
4. Rule 7 in `CLAUDE.md` still holds: no proprietary code, assets or
   undocumented file formats of other vendors are copied into the project.

## Consequences

- Anyone may use, modify and redistribute MineCore, including commercially,
  as long as the copyright notice is kept.
- Earlier commits become publicly visible when the repository is made public.
  They were scanned for credentials before the change.
- Crates stay `publish = false`; the Python package can be published to PyPI.
