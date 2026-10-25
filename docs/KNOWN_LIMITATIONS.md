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

- Regular block models, optionally rotated about the vertical (ADR-0009:
  azimuth of the rows). No dip/plunge rotation and no sub-cells yet; sub-blocked
  imports are merged into parent cells.
- Variogram GUI fits the first structure automatically; a second structure
  is entered by hand (no joint automatic fit of nested structures).
- Validation: swath plots and leave-one-hole-out cross-validation exist
  (isotropic search in the dialog); no kriging neighbourhood analysis,
  no change-of-support checks yet. Swath sample means are not declustered.
- Estimation kernels run in Rust (`mc-core::kriging`) on all cores; neighbour
  search is a SciPy KD-tree. Measured on 4 cores (3000 samples, 16 per block,
  2x2x2 discretisation, release build): IDW about 230k blocks/s, ordinary
  kriging about 143k blocks/s.
- CLI covers the chain (`drillholes`, `surface`, `bm`, `pit` commands); the
  CLI variogram is omnidirectional and isotropic only; `bm swath` exists,
  cross-validation is GUI and Python API only.
- Grade-tonnage is a report, not a resource classification (rule 8).

## Block model import

- CSV/text with centroid columns (and optional per-row sizes for sub-blocked
  models). Sub-blocks are merged into parent cells by volume-weighted mean;
  categorical text columns are skipped (convert codes to numbers first).
  A rotated model is read when its azimuth is given (text exports do not carry
  it); native Datamine/Surpac/Vulcan formats are not read.

## Sample statistics

- Histogram, log-probability plot and top-cut effect on unweighted samples
  (use equal-length composites). Cell declustering (GSLIB DECLUS method)
  reports the declustered mean; its weights are not yet used by the
  grade-tonnage or estimation steps.

## Confidence categories

- `blockmodel.classify` assigns categories from user thresholds on mean
  sample distance, sample count and kriging variance / total sill. It is a
  screening aid, not a JORC/NI 43-101/PERC classification (rule 8); no
  drill-spacing, slope-of-regression or smoothing of isolated blocks yet.
- Grade-tonnage reports can be restricted to an attribute range (domain,
  category, pit shell).

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
- Several metals: a main grade plus co-products (`--coproduct
  ATTR:PRICE:UNIT:REC[:PAYABLE[:SELLING[:R2/R3]]]`, GUI "Yan ürünler"), each
  with its own price, recovery, payable fraction, selling cost and recovery
  per process route; or an NSR value per tonne. Not modelled: penalty
  elements (As, Hg, F deductions), minimum deductions and treatment/refining
  charges per concentrate tonne, concentrate grade and mass pull, price
  escalation per metal. The schedule's metal columns report the main metal
  only; co-product revenue is in the period revenue.
- Process routes (`--route NAME:RECOVERY:COST`, GUI "Ek işleme yolları"):
  each block goes to the route with the best margin at the shell's revenue
  factor (`pit_route` stores the choice at factor 1). Routes differ only in
  recovery and processing cost per tonne; mining recovery and dilution are
  common. Not in NSR mode. The constrained scheduler gives each route its own
  capacity (`--route-capacity`, GUI "Ek yol kapasiteleri"; unlimited when not
  given) and stockpile; the route of a block is fixed by the optimisation and
  is not re-chosen when a route is full. Lane cut-off uses the main route only.
  Mining recovery and dilution are global factors applied to ore blocks
  (dilution at zero grade); no edge-dilution or regularisation model.
- Pushbacks group whole shells by ore tonnage, or by NPV
  (`pitopt.phases.best_pushbacks`, CLI `--best-pushbacks`, GUI "Pushback'leri
  NPV'ye göre seç"): each grouping of consecutive shells that gives every
  pushback the ore target is valued by its greedy constrained schedule NPV.
  All groupings are tried when they number at most 300 (exact for that
  schedule); otherwise a local search (move, merge, split one boundary)
  starts from the ore-target grouping and never returns a worse one, but
  may stop at a local optimum. Whole shells only (no split shells), the MILP
  schedule is not used in the search, and the minimum width is applied after
  the choice, so the NPV after width repair can differ. Minimum mining width
  (`pitopt.width`) reworks pushbacks so each bench increment is at least the
  given width (morphological opening; thin parts join the previous pushback,
  slope precedence is restored by propagation). The final pit itself is not
  changed, so a pit bottom narrower than the width stays narrow (the number of
  remaining narrow blocks is reported). Thin pushbacks are absorbed rather than
  widened. Measured: 123k blocks, 3.2 M arcs, 3.4 s. Ramp access is not
  enforced.
- Schedule (`pitopt.scheduling`): greedy, rule-based sequence with mining and
  plant capacity, bench lag, sinking rate and stockpiles; no blending
  targets, no cut-off optimisation, no equipment or haulage model; periods
  are years of equal capacity.
- MILP schedule (`pitopt.milp`, CLI `--schedule milp`, GUI "NPV
  optimizasyonu"): maximises the NPV under the same rules with HiGHS
  (scipy). Mining units stay increments (pushback x bench), so it is not a
  block-level or OES optimisation; the pushbacks are fixed inputs; the
  horizon is the greedy length plus two periods; the stockpile is selective
  per increment, route and grade bin (no blending inside a source). Time
  limit (default 60 s) and MIP gap (0.5 %) apply; when the solver stops
  without beating the greedy schedule, the greedy one is kept and the
  reason is reported. Measured on the sample data (2 pushbacks, 23 years):
  about 40 s. The sensitivity analysis uses the greedy schedule.
- Stockpiles: one per route and grade bin (`--grade-bins`, GUI "Stok tenör
  sınıfları"), fed highest bin first, fresh before stock within a bin;
  capacity per route (`--stockpile-capacity`) with the overflow sent to waste,
  and a rehandling cost per tonne reclaimed. Bins use the main feed grade (or
  the NSR), not the co-product grades. No stockpile losses, oxidation or grade
  degradation, no reclaim rate limit, no change of bin boundaries over time;
  a full stockpile does not slow the mine (the overflow is lost ore, reported
  per period).

## Project cash flow

- `pitopt.cashflow` adds capital by period (period 0 = construction), fixed
  costs, closure cost, income tax with straight-line depreciation and
  unlimited loss carry-forward to the constrained schedule; NPV (end or
  mid-period discounting), IRR (bisection) and payback. Payables and royalty
  are part of the block value. Not modelled: working capital, inflation and
  escalation, financing, salvage value, tax refunds of losses, depreciation
  pools other than straight line, sustaining capital per tonne.
- The simple fixed-rate schedule (without a plant capacity) has no cash-flow
  breakdown; the project cash flow needs the constrained schedule.

## Sensitivity analysis

- `pitopt.sensitivity` (CLI `pit optimise --sensitivity`, GUI "Duyarlılık"
  tab) changes one input at a time by relative amounts and reruns block
  values, schedule and project cash flow, on the fixed pit design or with the
  pit re-optimised. Not available: two-way tables, correlated or
  probabilistic (Monte Carlo) cases, break-even search, per-metal prices,
  selling cost or fixed cost as inputs. Price scales every metal price (or the
  NSR) with selling costs unchanged; recovery is capped at 100 %. Scenarios
  are not stored in the project; each run is recomputed.

## Pit scenarios

- `pitopt.scenarios` (GUI "Senaryolar", CLI `pit optimise --save-scenario`,
  `pit scenarios`): a scenario keeps the form inputs and headline numbers in
  the project settings. Results are a snapshot: they are not recomputed when
  the model changes, and reloading inputs does not rerun the study. No
  block-level results (shells, schedules) are kept per scenario; comparison
  covers headline metrics only. Lightly tested (save, list, load, delete).

## Cut-off grade optimisation (Lane)

- `pitopt.cutoff`: Lane's method with mine, mill and market capacities, fixed
  cost and opportunity cost d·V, iterated to convergence. The best cut-off
  per year is searched over every block grade (exact for the discrete
  grade-tonnage curve) plus the balancing points between two capacities.
  Lane's iteration finds a fixed point, which is not guaranteed to be the
  best cut-off path: when one cut-off kept every year has a higher NPV, that
  schedule is returned instead (`policy = "constant"`, reported), and when
  the passes cycle without converging the best pass is kept.
- Lane's depletion assumption: the remaining reserve keeps the pit's grade
  distribution every year (no link to the pushback/bench sequence). Mining
  recovery, dilution and bench-dependent costs are not applied; stockpiles
  are not modelled. Co-products enter as an exact main-metal equivalent
  grade, so the reported cut-off and metal are equivalent units.

## Pit design outputs

- `pitopt.design`: bench crest/toe loops of the block pit (block edges, no
  smoothing), a closed pit solid (marching cubes of the mined indicator, so
  corners are chamfered; volume slightly below the block volume), and the
  mined-out surface at block-column centres (staircase interpolated linearly).
- Bench design (`pitopt.benchdesign`): face angle, bench height and berm
  around the optimised pit by a distance-transform envelope on a raster
  (rounded corners, raster resolution half a block by default). Toe/crest
  strings are contours of that surface. No string editing.
- Geotechnical sectors in bench design (`--sector AZ:FACE:BERM`, GUI field
  "Geoteknik sektörler"): the sector is chosen by the azimuth from each
  cell's nearest floor cell (the wall's outward normal), so sector changes
  are abrupt steps, not blended transitions (the pit optimiser's slope
  sectors interpolate between azimuths; the two can differ slightly near
  boundaries). One bench height for all sectors; no sectors by rock type or
  depth; the ramp is placed using the per-sector overall slope and cut with
  the flattest sector.
- Ramp: one haul road per pit, traced bottom-up around the pit centroid and
  cut into the wall (benches above are set back): a spiral at constant
  gradient, or a switchback road on one wall arc with flat turning pads. The
  switchback turn is a hairpin of raster steps, not a designed curve.
  Separate pits (separate openings at the original surface) each get their
  own road, traced as if the other pits were absent; exits are given per pit
  in order of excavated volume, and a pit whose road cannot be traced is
  reported without one. Roads of different pits are not checked against
  each other. A single opening with several bottoms (pits merged above some
  level) gets one road from its deepest bottom; the other bottoms are only
  counted, no connecting road is designed. No road batters or safety bunds,
  and the road is not checked against a minimum curve radius. Because the optimiser treats the block model edge as
  unconstrained, a design can need ground outside the model; the volume
  outside is reported (create the model with padding).
- DXF export writes R12 ASCII `POLYLINE` (3D) and `3DFACE` entities only, no
  header/table sections, no blocks or text.

## Section plots

- `plots.section_plot` (CLI `plot`, GUI "Kesiti yazdır"): block slice,
  drillhole traces and samples in the slab, surface and solid cuts, on A3.
  No title block/legend template, no scale bar or grid annotations beyond the
  axes, no strings, no plan contours of surfaces, no multi-section sheets.

## Surfaces

- 2.5D DTMs only (one z per x, y): Delaunay in plan, linear interpolation;
  outside the hull the nearest point's elevation is used (counted, reported).
- DTM import from x, y, z text files only; no DXF/string files, no breaklines.

## Solids

- Import OBJ, STL (ASCII/binary) and ASCII DXF 3DFACE; no DXF POLYFACE/MESH,
  no binary DXF, no Datamine/Surpac native formats (undocumented, rule 7).
- Section linking (`strings.link_sections`): closed outlines on successive
  sections are stitched by normalised arc length and capped by ear clipping.
  One outline per section (no branching/bifurcation, no tag strings), outlines
  must not self-intersect; very different shapes on adjacent sections can give
  twisted bands. No solid editing or boolean operations.
- Strings import: ASCII DXF LWPOLYLINE (with elevation) and POLYLINE/VERTEX;
  no bulges (arcs) or splines.
- Block flags use the centroid; partial-block volume fractions (`_pct`) are
  estimated by n³ sub-cell sampling of blocks near the surface (error about
  one sub-cell layer), not by exact polyhedron clipping. Domain estimation
  still selects blocks by centroid.
- Grade shells: marching cubes over block centres (VTK), not an implicit model.

## Implicit domain modelling (M10 start)

- `geology.implicit`: one domain at a time from interval classification
  (numeric threshold or category codes). Contacts are placed only where an
  inside and an outside interval touch. Midpoints carry the distance to the
  nearest drilled contact (capped), which overestimates the distance to the
  true surface between holes, so domains tend to be slightly conservative
  with wide drill spacing (synthetic sphere: -7 % volume at 15 m spacing).
- Global polyharmonic RBF (dense solve); above 4,000 points a regular subset
  of interval points is used (all contacts kept). No structural trends,
  faults, younging, vein/hanging-wall-footwall modelling, interval selection
  (short internal waste is not merged) or editing of the surface. The surface
  resolution is the block size of the chosen model.

## Underground (M9 start)

- `underground.stopes`: floating stope envelope (fixed stope size in whole
  blocks, tonnage-weighted mean grade >= cut-off) and a greedy,
  non-overlapping stope selection by metal. Not a Mineable Shape Optimiser:
  no variable stope shapes, dip/strike following, pillars, dilution skins,
  development or scheduling. Stopes are aligned with the block grid.
- `underground.development`: concept access layout only: one straight portal
  leg plus a circular spiral decline at constant gradient beside the orebody
  (standoff checked in plan against ore block centroids), and one straight
  horizontal crosscut per level to the nearest ore block. One straight ore
  drive per level along the principal plan axis of that level's ore blocks,
  through the crosscut end (no footwall offset, no drawpoints, no curved
  drives for folded ore). No ventilation raises, sumps, passing bays, switchbacks, geotechnical or
  clash checks against other excavations, and no development scheduling.
