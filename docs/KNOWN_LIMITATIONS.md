# Known limitations

What MineCore cannot do yet, and what each gap depends on. Updated every session.

| Area | Limitation | Depends on |
| --- | --- | --- |
| Whole product | Units contract and project package exist; no data import, modelling or estimation yet. | M1 onward |
| Project package | No operation log or undo yet; bulk-file atomic write rule not yet defined; no single-file archive/export. | T-004, T-006, M2 |
| Coordinates | Working CRS cannot change once a project holds objects (no reprojection); one local grid per project; feet-based working CRSs refused; vertical datum transformations need geoid grids that are not installed, so they are refused; Turkish official ED50↔ITRF96 practice not yet reviewed. | T-010, later M1/M11 |
| Units | No affine units (degrees Celsius), no currency dimension, no years (ambiguous). | ADR when economics or thermal data arrive |
| Licences (Python) | Python runtime dependencies are tracked by hand in `docs/DEPENDENCIES.md`; only Rust is checked automatically (`cargo-deny`). | Packaging work (M12) or more Python dependencies |
| i18n | CLI messages are English only. Turkish/English UI strings arrive with the workbench. | Workbench milestone |
| Packaging | No Windows installer or signed build yet. | M12 |
