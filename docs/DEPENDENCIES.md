# Dependency licence register

Every dependency that ships with MineCore is listed here with its licence and
the date it was approved. Rust dependencies are also enforced by `deny.toml`
in CI. Development-only tools are listed separately; they are not distributed.

## Shipped (runtime)

| Dependency | Side | Licence | Purpose | Approved |
| --- | --- | --- | --- | --- |
| pyo3 | Rust | MIT OR Apache-2.0 | Python bindings | 2026-10-24 |
| thiserror | Rust | MIT OR Apache-2.0 | Error types | 2026-10-24 |
| numpy | Python | BSD-3-Clause | Array computing | 2026-10-24 |
| numpy (rust-numpy) | Rust | BSD-2-Clause | NumPy arrays in bindings (ADR-0006) | 2026-10-25 |
| pyproj | Python | MIT | CRS and datum transformations (ADR-0005) | 2026-10-25 |
| PROJ (bundled in pyproj wheel) | C | MIT | Projection engine | 2026-10-25 |
| libtiff, libcurl, nghttp2, sqlite (bundled in pyproj wheel) | C | libtiff (BSD-like), curl (MIT-like), MIT, public domain | PROJ dependencies | 2026-10-25 |
| certifi (pyproj dependency) | Python | MPL-2.0 (unmodified) | CA bundle | 2026-10-25 |
| EPSG dataset (in proj.db) | data | EPSG Terms of Use (attribution, no modified redistribution as EPSG) | CRS definitions | 2026-10-25 |

The Windows pyproj wheel bundles its own library set; re-audit it when the installer is built (M12).

## Development only

| Tool | Licence | Purpose |
| --- | --- | --- |
| maturin | MIT OR Apache-2.0 | Build backend |
| proptest | MIT OR Apache-2.0 | Rust property tests |
| pytest, hypothesis | MIT, MPL-2.0 | Python tests |
| ruff | MIT | Lint and format |
| mypy | MIT | Type checking |
| cargo-deny | MIT OR Apache-2.0 | Licence policy |
