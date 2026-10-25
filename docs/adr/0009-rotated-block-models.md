# ADR-0009: Rotated block models

- Status: accepted 2026-10-25
- Deciders: architect

## Context

Ore bodies rarely strike north-south or east-west. A model aligned to world
axes around a body striking N35E wastes blocks (for a synthetic 200 x 60 m
body turned by 50 degrees, the world-aligned model needs over 1.6 times the
blocks of one aligned with the body), and the blocks do not
follow the geology, which makes along-strike swaths, stope layouts and
ore-drive design awkward. Datamine, Surpac, Vulcan and Micromine all store a
model rotation. Block models exported from those packages are often rotated,
and the CSV importer had to refuse them.

The block model contract is the `Grid` in `python/minecore/blockmodel/model.py`
(ADR-0004 metadata of a `block_model` object). It is not in `mc-core`: the
Rust kernels receive world coordinates (kriging) or block indices (pit
optimisation) and never see the grid.

## Decision

1. `Grid` gains `azimuth`: the bearing of the model's +j (row) axis in degrees
   clockwise from grid north, about the vertical through `origin`. 0 is an
   unrotated model. Bearing of the rows is how mining engineers state a strike
   direction, so the value can be typed from a plan.
2. Only rotation about the vertical is supported. Columns stay vertical and
   benches stay horizontal, so pit precedence, bench inventories, topography
   flags and level-based underground design keep working unchanged in index
   space. Dip and plunge rotations need sheared benches and are out of scope.
3. `origin` stays the minimum corner in world coordinates and is the pivot.
   Local (model frame) coordinates are metres from the origin along i, j, k:
   `world = origin + R @ local`, `R = [[cos a, sin a, 0], [-sin a, cos a, 0],
   [0, 0, 1]]` (columns are the world directions of i, j, k).
4. `centroids()` always returns world coordinates. New helpers:
   `to_local`, `to_world`, `cell_index`, `corners`, `bounds`, `local`,
   `local_centroids`, `axes`, `rotated`.
5. Storage: `grid.azimuth` is written only for a rotated grid, and such an
   object is written with `format: 2`. Unrotated models keep `format: 1` and
   are byte-for-byte what earlier versions wrote. `BlockModel` refuses formats
   newer than it knows, so a future change cannot be misread silently. No SQL
   migration: the change is inside the object's metadata JSON.
6. Consumers that map world geometry to cells go through the grid helpers:
   kriging discretisation is turned with the model (`block_axes`), solid
   fractions and iso-surfaces are computed in the model frame and turned back,
   bench outlines and stopes are turned to the world, bench design samples a
   world-aligned raster through `cell_index` (sectors and ramp exits keep world
   azimuths), section plots cut rotated blocks obliquely, the CSV importer takes
   the azimuth (text exports rarely carry it), re-blocking keeps it.

## Consequences

- Compatibility: models written before this ADR open unchanged
  (`test_rotated_grid_is_stored_with_format_2_and_round_trips`); a rotated
  model opened by an older MineCore would be read as unrotated, which is why
  the format number changes.
- Verification (`tests/python/test_rotated_models.py`): every check solves the
  same problem unrotated and rotated and requires equal answers: IDW and OK
  estimates and variances (to 1e-9), solid fractions, iso-surface volume and
  vertices, bench outlines, design volume (exact at 90 degrees, within 3 % at
  30 degrees where the raster is resampled), stopes, a CSV round trip, and
  analytic section widths of a 45-degree block. On the sample data the CLI
  chain gives the same tonnage above 0.5 g/t within 0.2 %.
- Not covered: sub-cells (a separate decision), dip/plunge rotation, reading a
  rotation from native Datamine/Surpac/Vulcan files.
