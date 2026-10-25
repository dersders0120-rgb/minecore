# Known limitations

What MineCore cannot do yet, and what each gap depends on. Updated every session.

| Area | Limitation | Depends on |
| --- | --- | --- |
| Project package | Bulk-file writer and garbage collection of unreferenced files arrive with the first bulk data (M2); no single-file archive/export. | M2 |
| Coordinates | Working CRS cannot change once a project holds objects (no reprojection); one local grid per project; feet-based working CRSs refused; vertical datum transformations need geoid grids that are not installed, so they are refused; Turkish official ED50↔ITRF96 practice not yet reviewed. | T-010, later M1/M11 |
| Units | No affine units (degrees Celsius), no currency dimension, no years (ambiguous). | ADR when economics or thermal data arrive |
| Licences (Python) | Python runtime dependencies are tracked by hand in `docs/DEPENDENCIES.md`; only Rust is checked automatically (`cargo-deny`). | Packaging work (M12) or more Python dependencies |
| i18n | CLI messages are English only. Turkish/English UI strings arrive with the workbench. | Workbench milestone |
| Packaging | No Windows installer or signed build yet. | M12 |

## Drillhole data (M2, ADR-0008)

- Bulk files stay on disk while any operation in the history can restore them.
  Trimming history to reclaim space is not implemented yet.
- QA/QC thresholds (T-014) are not sourced yet; MineCore ships no QA/QC defaults.

## Block model, variograms and estimation (M4–M6 alpha)

- Regular, unrotated block models only; no sub-blocks, no rotation, no wireframe
  (solid) flagging or domain-restricted estimation yet.
- Variogram GUI fits one structure; nested structures are supported by the
  engine and JSON but are entered one structure at a time in the dialog.
- No cross-validation, no swath plots, no kriging neighbourhood analysis yet.
- Estimation loop is Python/NumPy. Measured (3000 samples, 16 per block, 2x2x2
  discretisation): IDW about 67k blocks/s, ordinary kriging about 1.8k blocks/s,
  so a 1 M block OK run takes about 10 minutes. Rust port of the OK loop planned.
- No CLI commands for import, block model, estimation or reporting yet (rule 5
  debt): the workbench calls the public Python API, which scripts can call.
- Grade-tonnage is a report, not a resource classification (rule 8).
