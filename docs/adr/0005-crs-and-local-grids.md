# ADR-0005: Coordinate reference systems and local mine grids

- Status: accepted 2026-10-25 (T-004)
- Deciders: architect

## Context
Mines store geometry in a national projected CRS (in Turkey: ED50 UTM, ITRF96/TUREF
3° TM) or in a local mine grid. Data arrives in other systems (GPS in WGS 84,
legacy ED50 surveys). A silently wrong datum shift moves data by ~100 m; a
silently wrong grid rotation corrupts every drillhole.

## Decision
1. **Working system.** Each project has one working coordinate system holding
   all geometry: a projected CRS in metres (optionally compound with a vertical
   CRS), a local mine grid on such a base CRS, or an explicit *unreferenced*
   system. Stored in `settings.coordinate_system` as versioned JSON (format 1).
   It cannot change once the project holds objects (no reprojection yet).
2. **CRS definitions** are stored as WKT2:2019 with name and authority code;
   the WKT, not the code, is authoritative, so PROJ upgrades cannot change a
   stored definition. Feet-based CRSs are refused as working CRSs.
3. **PROJ via pyproj** (Python layer). One operation per dataset, selected by
   PROJ for the data's area; its description, published accuracy and pipeline
   are reported. Ballpark operations are never used. If the best operation
   needs a missing grid, the transformation is refused and the grid named. PROJ
   network access is disabled. Axis order is always x = easting, y = northing.
4. **Local mine grid** lives in `mc-core::coords` (Rust): 2D similarity
   (origin pair, rotation = azimuth of local north clockwise from base grid
   north, scale) plus vertical offset; least-squares fit from control points
   (free or fixed scale) on centroid-reduced coordinates, with per-point
   residuals, RMS, max and sigma0.
5. **Verification.** PROJ's TM/UTM results for all Turkish zones are checked
   against an independent Krüger-series implementation (< 1 mm); the fit is
   checked by exact recovery, least-squares optimality and property tests.

## Consequences
- Turkish official ED50↔ITRF96 parameters and geoid models are not yet
  reviewed (T-010); vertical datum transformations need grids and are refused.
- Reprojecting a populated project and multiple named alternate grids are
  future work.
