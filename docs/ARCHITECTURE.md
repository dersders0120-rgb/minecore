# MineCore architecture

Status: approved 2026-10-24 (ADR-0001). Owner: architect. Changes need an ADR.

## 1. Layers

```
Desktop workbench (Python, PySide6/Qt 6, VTK)      python/minecore/workbench
        |  depends on
Domain modules (Python workflows + Rust kernels)   python/minecore/<domain>, mc-core::<domain>
        |  depends on
Core engine (Rust, no GUI, no Python)              crates/mc-core
        |  persists to
Project package .mcproj (SQLite + Parquet + chunked block files)
```

Dependencies point down only. `mc-core` never depends on Python or GUI code.
Every capability is reachable from the `minecore` CLI and the Python API, so it
is testable without a window. The workbench is a client of the same API that
users script against.

## 2. What lives where

| Concern | Location | Rule |
| --- | --- | --- |
| Units, CRS, geometry, block model schema, project format | `mc-core` | Single owner: architect. ADR + compatibility test to change. |
| Numerical kernels (desurvey, compositing, variograms, kriging, pit optimisation, scheduling solvers) | `mc-core::<domain>` | Rust; release the GIL; parallel with rayon; cancellable. |
| Workflows, parameter sets, validation reports | `python/minecore/<domain>` | Python; calls kernels through `minecore._core`. |
| File import/export (CSV, XLSX, DXF, OMF, ...) | `python/minecore/io` | Python adapters writing to core data contracts. |
| GUI | `python/minecore/workbench` | Never contains engineering logic. |
| Bindings | `crates/mc-py` | Translation only; no logic. |

`mc-core` starts as one crate with one module per domain. It is split into
several crates when compile time or ownership boundaries justify it (ADR).

## 3. Engineering conventions (binding from M1)

- **Frame:** right-handed X = easting, Y = northing, Z = elevation (up), metres, `f64`.
- **Project CRS:** every project has one CRS: an EPSG projected CRS, or a local
  mine grid defined by a documented transform to one. All stored geometry is in
  the project CRS; transformations happen only at import/export (PROJ).
- **Rendering:** large map coordinates (e.g. UTM northings ~4.5e6 m) are shifted
  to a local render origin before conversion to `f32` GPU buffers.
- **Angles:** azimuth in degrees clockwise from project grid north, [0, 360).
  Dip in degrees, negative below horizontal (-90 = vertically down). Importers
  declare the source convention explicitly; nothing is guessed.
- **Downhole intervals:** depths in metres along the hole from the collar;
  intervals are half-open [from, to).
- **Quantities:** SI internally (m, m^3, t, t/m^3). Grades always carry an
  explicit unit (%, ppm, ppb, g/t, ...). No unit-less numeric attribute is stored.
- **Missing and censored values:** stored as nulls with a reason code, never as
  sentinels (-99, -999). Below-detection values keep the detection limit and a
  flag; the substitution policy is an explicit, logged parameter.
- **Reproducibility:** every derived dataset records its inputs, parameters,
  engine version and random seed in the project operation log.
- **Time:** UTC, ISO 8601.

## 4. Project package (`.mcproj`, detailed in M1)

A directory package:
- `project.sqlite` — metadata, object catalogue, CRS, units, schema version,
  operation log, undo history.
- `tables/*.parquet` — large tabular data (collars, surveys, assays, composites).
- `blocks/` — chunked block model storage (3D tiles, loaded on demand).
- `geometry/` — versioned binary meshes, strings and solids.

The format is versioned; opening an older version runs tested migrations.

## 5. Runtime behaviour

- Long operations run as jobs with progress reporting and cooperative
  cancellation; the GUI never blocks.
- Rust errors map to a typed Python exception hierarchy; no silent failures.
- Logging is structured; crash reports never include project data without consent.

## 6. Interoperability

Open, documented formats first: CSV/XLSX (including `;` delimiters, decimal
commas, UTF-8 BOM and Windows-1254), DXF, OBJ/STL, OMF (Open Mining Format),
and documented text formats such as Surpac string/DTM. Undocumented proprietary
binary formats are not reverse engineered.
