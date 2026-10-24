# ADR-0001: Technology stack

- Status: accepted (product owner approved 2026-10-24)
- Deciders: architect, product owner

## Context

MineCore must handle block models with tens of millions of cells, large
triangulations and heavy estimation workloads on Windows desktops, be
distributed commercially as closed source, and be built largely by AI models
under architect review. The stack must be fast, protect intellectual property,
catch defects early and have permissive licences.

## Decision

- **Core engine: Rust** (`crates/mc-core`). Compiled, memory safe, C++-class
  performance; the compiler rejects many defects in generated code before review;
  compiled binaries protect the core algorithms.
- **Bindings: PyO3 + maturin** (`crates/mc-py`), abi3 wheels for Python >= 3.12.
- **Application, workflows, scripting, tests: Python** with numpy, scipy and
  pyarrow. Python scripting is a user-facing feature.
- **Desktop GUI: Qt 6 via PySide6** (LGPL, dynamically linked).
- **3D: VTK** (BSD), through pyvista where useful.
- **CRS: PROJ** via pyproj (MIT). **Optimisation: HiGHS** (MIT), OR-Tools (Apache-2.0).
- **Storage:** SQLite (metadata), Apache Parquet (tables), own chunked block format.
- **Tooling:** uv, ruff, mypy (strict), pytest + hypothesis, rustfmt, clippy, cargo-deny.
- **Platforms:** Windows 10/11 primary; Linux in CI.

## Alternatives considered

- *Pure Python + numba, port hot spots later:* fastest start, but large-model
  performance and source protection fail at commercial scale, and a late port
  of the core is expensive. Rejected.
- *C++ core:* industry norm, but memory-safety defects in AI-generated C++ are
  hard to catch in review. Rejected.
- *.NET (C#) with WPF/Avalonia:* good Windows desktop story, weaker scientific
  ecosystem and Python scripting. Rejected.
- *Web/Electron first:* poor fit for local GPU-heavy 3D and offline field use.
  A web layer may be added later on top of the headless core.

## Consequences

- Contributors need both Rust and Python; task briefs state which side a task is on.
- GPL/AGPL libraries (e.g. CGAL, PyQt) are excluded; `deny.toml` enforces the Rust side.
- Each Rust kernel is exposed to Python with type stubs (`_core.pyi`) kept in sync.
