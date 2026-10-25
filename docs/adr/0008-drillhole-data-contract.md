# ADR-0008: Drillhole data contract, bulk tables and validation

- Status: accepted 2026-10-25 (T-015)
- Deciders: architect

## Context

M2 imports collar, survey and interval data from field and laboratory files,
validates it, desurveys it and composites it. Everything downstream (solids,
block models, estimation, reported resources) inherits the quality of this
step, and an auditor must be able to trace any reported grade back to a line
in a source file. Real data is messy: duplicate collars, orphan samples,
overlapping intervals, assay text such as `<0.005`, `-99` or `NS`, mixed dip
sign conventions and azimuths referenced to magnetic north.

Two rules follow from that. MineCore never repairs data silently: what it
cannot use, it reports and excludes, and every exclusion is visible and
explained. And the imported record is kept as it arrived, so a different
decision later (a corrected file, a different detection-limit policy) does not
require re-reading the source.

## Decision

### 1. Drillhole database object

A drillhole database is one catalogue object of kind `drillhole_db`.
Its `metadata_json` is the **manifest**:

```json
{
  "format": 1,
  "tables": {
    "collar":  {"role": "collar",   "file": "tables/<db>/collar-<id>.parquet", "rows": 812,
                "sha256": "...", "fields": [...], "source": "<source id>"},
    "survey":  {"role": "survey",   ...},
    "assay":   {"role": "interval", ...},
    "litho":   {"role": "interval", ...}
  },
  "sources": {"<source id>": {"file": "sources/<sha256>.csv", "name": "...", "sha256": "...",
                              "dialect": {...}, "mapping": {...}, "imported_utc": "..."}},
  "conventions": {"dip": "negative_down", "azimuth": "grid", ...}
}
```

The manifest lives in a tracked table, so import, replace and delete are
ordinary operations: undo restores the previous manifest, and the previous
files still exist because bulk files are immutable (§4).

Table roles:

| Role | Required columns | Key |
| --- | --- | --- |
| `collar` | `hole_id`, `x`, `y`, `z` | `hole_id` |
| `survey` | `hole_id`, `depth`, `azimuth`, `dip` | `hole_id`, `depth` |
| `interval` | `hole_id`, `from`, `to` | `hole_id`, `from` |
| `point` | `hole_id`, `depth` | `hole_id`, `depth` |

A database has one collar table, at most one survey table and any number of
interval and point tables. Optional collar columns: `max_depth`, `azimuth`,
`dip` (orientation of holes without surveys). Every table also carries
`_src_line` (int64), the 1-based line of the row in its source file.

### 2. Canonical values

- `hole_id` is text. Leading and trailing whitespace is removed on import and
  reported. Matching is exact and case-sensitive; IDs that differ only by case
  are reported, never merged.
- `x`, `y`, `z` are metres in the project's working system (ADR-0005). Collars
  in another CRS are transformed on import with the operation recorded.
- `depth`, `from`, `to`, `max_depth` are metres along the hole. Input in feet
  is converted on import, and the conversion is recorded.
- `azimuth` is in degrees, clockwise from the north of the working grid,
  normalised to [0, 360). `dip` is in degrees from horizontal, in [-90, 90],
  **negative below horizontal** (a vertical down hole has dip -90).
- The import declares the source conventions explicitly, never by guessing:
  - dip sign (`negative_down` or `positive_down`);
  - azimuth reference (`grid`, `true` or `magnetic`), with a declination for
    `magnetic`, constant or per hole;
  - the conversion from true or magnetic north to the working grid (grid
    convergence from the CRS at the collar; for a local grid, the grid
    rotation in addition).

  The applied correction is stored per hole in the survey table
  (`azimuth_correction`), so it can be audited.

### 3. Attribute fields

Each non-key column is a field with a manifest entry
`{"name", "type", "unit"?, "quantity"?, "source_column", "missing_codes"?}`:

- `numeric`: float64. `unit` is parsed by `minecore.units` and checked
  against `quantity` (ADR-0003). Values are stored in the source unit; a
  conversion happens only where a computation needs another unit.
- `category`: text codes such as lithology, stored dictionary-encoded.
- `text`: free text such as comments.

Field names keep the user's spelling (Unicode allowed, e.g. `LİTOLOJİ`) and
must be unique within a table ignoring case. The key column names, `_src_line`
and names ending in `__flag` are reserved.

**Assay qualifiers.** Every numeric field has a companion `<name>__flag`
column (uint8) that says what the value means:

| Flag | Meaning | Stored value |
| --- | --- | --- |
| 0 | measured | the value |
| 1 | below detection (`<0.005`, or `-0.005` under the negative-DL convention) | the detection limit, positive |
| 2 | above the upper limit (`>10000`) | the limit |
| 3 | missing: empty cell or a declared missing code (`-99`, `NS`) | NaN |
| 4 | not sampled (declared code) | NaN |
| 5 | insufficient or lost sample (declared code) | NaN |

The import never turns a qualifier into a number. How flagged values enter a
calculation (for example, half the detection limit for flag 1) is a stated,
recorded policy of that calculation, never a property of the stored data.
Missing is never zero.

### 4. Bulk files

- Tables are Parquet files (pyarrow, zstd), written to
  `tables/<db object id>/<table>-<uuid>.parquet`. The schema metadata records
  `minecore.table_format = 1` and the role.
- **Atomic write:** write to a hidden temporary name in the target directory,
  flush and fsync, `os.replace` into place, then fsync the directory where the
  OS allows it. A file is referenced from the manifest only after it is
  complete, in the same operation that changes the manifest.
- **Immutable:** a bulk file is never modified. A change writes a new file and
  points the manifest at it.
- The manifest stores the size and SHA-256 of each file. A read checks the
  size; `Project.integrity_errors()` checks the hashes.
- **Source archive:** each imported source file is copied once to
  `sources/<sha256>.<ext>`, so every row is traceable to the bytes it came from
  (`_src_line`).
- **Garbage collection:** `Project.collect_garbage()` removes files under
  `tables/` and `sources/` that no current object and no operation in the
  history references, and any leftover temporary files. It runs on demand and
  when a project is opened for writing; the project lock guarantees no other
  writer. Files therefore live as long as an operation that can restore them;
  trimming history to reclaim space is future work.

### 5. Validation

Validation is a pure, repeatable function of a database's tables and settings.
It returns a report of issues:

`Issue(code, severity, table, hole_id, src_line, field, message, effect)`

- `severity` is `error`, `warning` or `info`.
- `effect` is `exclude_row`, `exclude_hole` or `none`. Only errors exclude.
- Codes are stable identifiers that users, scripts and documentation can rely
  on. The catalogue lives in `minecore.drillholes.issues` and is documented in
  both languages.

Desurveying, compositing and everything downstream use only the rows and holes
that validation leaves usable, and report how many they skipped and why.
Nothing downstream re-implements a check.

**Overlaps:** when two intervals in the same hole and table overlap, both are
excluded. MineCore does not guess which one is right.

Initial catalogue (M2):

| Code | Severity | Effect | Condition |
| --- | --- | --- | --- |
| `COLLAR_DUPLICATE` | error | exclude_hole | Two collar rows with the same `hole_id` (all rows of that ID) |
| `COLLAR_COORD_INVALID` | error | exclude_hole | `x`, `y` or `z` missing or not finite |
| `COLLAR_MAX_DEPTH_INVALID` | error | exclude_hole | `max_depth` present and ≤ 0 |
| `HOLE_ID_CASE` | warning | none | IDs differ only by letter case (across all tables) |
| `HOLE_ID_TRIMMED` | info | none | Whitespace was removed from an ID |
| `HOLE_NO_DATA` | info | none | Collar without survey, interval or point rows |
| `ORPHAN_ROW` | error | exclude_row | Row whose `hole_id` has no collar |
| `SURVEY_DEPTH_INVALID` | error | exclude_row | Depth missing, not finite or negative |
| `SURVEY_DUPLICATE_DEPTH` | error | exclude_hole | Two stations at the same depth |
| `SURVEY_AZIMUTH_INVALID` | error | exclude_row | Azimuth missing, not finite or outside [0, 360] |
| `SURVEY_DIP_INVALID` | error | exclude_row | Dip missing, not finite or outside [-90, 90] |
| `SURVEY_BEYOND_DEPTH` | warning | none | Station deeper than `max_depth` |
| `SURVEY_DOGLEG` | warning | none | Dogleg between consecutive stations above the limit (default 3° per 30 m) |
| `SURVEY_NONE` | warning | none | Hole without survey stations; collar orientation or vertical is used |
| `INTERVAL_DEPTH_INVALID` | error | exclude_row | `from` or `to` missing, not finite or negative |
| `INTERVAL_REVERSED` | error | exclude_row | `from` ≥ `to` |
| `INTERVAL_OVERLAP` | error | exclude_row | Overlaps another interval of the same hole and table (both rows) |
| `INTERVAL_GAP` | info | none | Unlogged gap between consecutive intervals, with its length |
| `INTERVAL_BEYOND_DEPTH` | warning | none | `to` deeper than `max_depth` plus tolerance |
| `VALUE_OUT_OF_RANGE` | warning | none | Numeric value outside its quantity's valid range (negative grade, a fraction above 100 %) |
| `VALUE_UNREADABLE` | warning | none | Import: text that is neither a number, a qualifier nor a declared code; kept as missing |
| `ROW_WIDTH` | warning | none | Import: rows with a different number of fields than the header |
| `VALUE_QUALIFIED` | info | none | Count of qualified values per field and flag |

`max_depth` missing is not an error: the hole's depth is the deepest of its
survey stations, interval ends and point depths, reported as `info`
(`HOLE_DEPTH_DERIVED`).

Thresholds such as the dogleg limit and depth tolerance are database settings
with documented defaults, recorded in every report.

## Alternatives considered

- *Repair on import (drop duplicates, clip overlaps, swap from/to):* fast to
  use but hides data problems and destroys the audit trail. Rejected.
- *SQLite tables for drillhole data:* simple, tracked by the operation log,
  but large assay tables bloat the database and history images, and columnar
  reads are slow. Rejected; the manifest gives undo without copying rows.
- *Each table as its own catalogue object:* `(kind, name)` is unique across
  the project, so two databases could not both have an `assay` table without
  name mangling. Rejected.
- *Store grades converted to one canonical unit:* simpler arithmetic, but
  users expect their own units and display rounding would change values.
  Rejected; ADR-0003 makes conversion exact and cheap.

## Consequences

- pyarrow becomes a runtime dependency (Apache-2.0).
- Desurvey and compositing kernels receive arrays of usable rows only, and
  return their own skip counts for the report.
- History now keeps bulk files alive. KNOWN_LIMITATIONS records that space is
  reclaimed only when history is trimmed, which is not implemented yet.
- Importers (CSV/XLSX with the T-013 dialect detector) produce a mapping and
  conventions, then call the same database API as scripts do.
