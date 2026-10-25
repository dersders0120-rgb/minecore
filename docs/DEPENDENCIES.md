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

## Desktop application (M2 first release)

| Package | Licence | Use | Note |
| --- | --- | --- | --- |
| pyarrow | Apache-2.0 | Parquet bulk tables | |
| PySide6-Essentials (Qt 6) | LGPL-3.0 | Workbench window | Shipped as separate, replaceable DLLs (PyInstaller one-folder build); not statically linked or modified |
| VTK | BSD-3-Clause | 3D rendering | |
| pyvista, pyvistaqt | MIT | VTK convenience layer and Qt widget | |
| PyInstaller | GPL-2.0 with bootloader exception | Build tool only | The exception permits distributing the bundled application under our licence |
| scipy | BSD-3-Clause | KD-tree search for estimation | |
| matplotlib | Matplotlib licence (PSF-based, permissive) | Variogram and grade-tonnage charts in the workbench | already a pyvista dependency; now declared directly |
