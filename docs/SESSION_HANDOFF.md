# Session handoff

Updated: 2026-10-25 01:25 UTC (architect, end of thread 1)

## Owner direction (binding)
- Windows 11 is the only target. Owner wants a usable program fast ("jet hızında"),
  accepts some bugs, tests the GUI himself. Ship vertical slices with a runnable
  Windows build; fewer docs; no stacked PRs; one PR to merge at a time.
- Reply to the owner in Turkish, short, plain (he is not a GitHub/CI user).

## State of `main`
- Merged: #1–#5 (M0, units, project package, CRS/local grid, delegated T-008/10/11),
  #7 (M1 close-out: oplog/undo/jobs/errors; ADR-0008 drillhole contract,
  `minecore.project.bulk`, `minecore.drillholes` model + validate, T-013 sniffer,
  T-012 compositing references, T-014 QA/QC research with review note).
- #6 closed (superseded by #7).

## Open PR #8 — branch `m2-app` (all CI green incl. Windows build)
First workbench release. Importer (`drillholes/importer.py`), min-curvature
desurvey, fixed/domain compositing (matches T-012), `drillholes/workflow.py`,
PySide6+VTK GUI (`python/minecore/gui/app.py`, Turkish), Help → sample project
(bundled `gui/sample/*.csv`), `.github/workflows/windows-app.yml` (PyInstaller,
artifact `MineCore-windows`, ~243 MB). Owner launched the .exe on Windows 11 —
it opens. Owner should merge #8 (latest commit 6f1fb54 still needs its CI run).

## Work in progress — branch `m3-estimation` (based on `m2-app`, pushed, NO PR yet)
Done and tested (302 pytest pass, ruff, mypy strict clean):
- `minecore.estimation.variogram`: Ellipsoid (azimuth/dip/rake, right-handed
  frame, documented convention), spherical/exponential/gaussian practical-range
  structures, nested VariogramModel (+ JSON), experimental variogram
  (omni/directional, pair subsampling), `fit_spherical` (weighted LSQ grid search).
- `minecore.estimation.kriging.estimate`: NN, IDW, ordinary kriging (point or
  block with discretisation), anisotropic KD-tree search, min/max samples,
  max per hole, kriging variance, progress.
- `minecore.blockmodel`: regular `Grid` (covering, centroids), `BlockModel`
  object with Parquet attributes (undoable), `grade_tonnage` (constant or
  attribute density, metal in g for g/t|ppm else t).
- `minecore.blockmodel.workflow`: `interval_samples` (composites mid_x/y/z or
  desurvey), `estimate_blocks` (stores value, _n, _dist, _kv + parameters).
- Tests: `tests/python/estimation/test_estimation.py` (analytic cases).
- New dependency: scipy (BSD) — add to docs/DEPENDENCIES.md.

## Next actions (in order)
1. After #8 merges: open PR for `m3-estimation` → `main` (rebase/merge main first).
2. GUI for M3–M6 in `gui/app.py`: "Blok modeli" menu — create grid from
   composites extent; experimental variogram + fit dialog (matplotlib Qt plot);
   estimation dialog (method, search ellipsoid, min/max, variogram); 3D block
   view (pyvista ImageData/threshold by cut-off); grade-tonnage table + curve,
   CSV/XLSX export. Rebuild Windows artifact and give owner the link.
3. Delegate T-016 to ChatGPT: independent OK/IDW reference values using the
   conventions in `estimation/variogram.py` docstring; compare in tests.
4. Rust port of the kriging loop only if performance requires (10M blocks).
5. Known: synthetic generator injects error rows by copying the first sample row,
   so `--errors` data shows ~111 overlaps on RC0001 (noisy but correct).

## Owner owes
- Merge #8. Try his real drillhole data in the app and report problems.
