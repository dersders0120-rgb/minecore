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
- Validation: swath plots and leave-one-hole-out cross-validation exist
  (isotropic search in the dialog); no kriging neighbourhood analysis,
  no declustered statistics, no change-of-support checks yet.
- Estimation kernels run in Rust (`mc-core::kriging`) on all cores; neighbour
  search is a SciPy KD-tree. Measured on 4 cores (3000 samples, 16 per block,
  2x2x2 discretisation, release build): IDW about 230k blocks/s, ordinary
  kriging about 143k blocks/s.
- No CLI commands for import, block model, estimation or reporting yet (rule 5
  debt): the workbench calls the public Python API, which scripts can call.
- Grade-tonnage is a report, not a resource classification (rule 8).

## Open-pit optimisation (M7 alpha)

- Ultimate pit by maximum closure (Dinic max-flow in `mc-core::pitopt`, same
  optimum as Lerchs-Grossmann); verified against exhaustive search. Measured:
  256k blocks, 6.8 M arcs, 3 shells in 19 s (release build). Each shell is a
  separate solve; no warm start or pseudoflow yet.
- Topography: a `kaya` (rock = 1, air = 0) attribute from a DTM removes air
  blocks from tonnage and cost; without it the model top is the surface.
  Partial blocks are not prorated (centroid test). Unestimated rock blocks are
  valued as waste. Mining cost depth increment counts benches from the model
  top, not from the surface.
- Single element, single process route; no cut-off optimisation (Lane), no
  stockpiles, no mining dilution/recovery factors on the block model.
- Pushbacks group whole shells by ore tonnage; minimum mining width and ramp
  access are not enforced.
- Schedule: fixed total mining rate, phases in order bench by bench, no bench
  lag between phases, no plant capacity, no blending. A first pass, not a plan.

## Surfaces

- 2.5D DTMs only (one z per x, y): Delaunay in plan, linear interpolation;
  outside the hull the nearest point's elevation is used (counted, reported).
- Import from x, y, z text files only; no DXF/string files, no breaklines,
  no 3D solids (wireframes) yet.
