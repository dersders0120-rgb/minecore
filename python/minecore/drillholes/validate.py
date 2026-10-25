"""Drillhole validation (ADR-0008 §5).

:func:`validate` is a pure function of a database's tables and the settings.
It never changes data. It returns every issue it found and, for each table, a
mask of the rows that downstream processing may use. Rules:

* Only errors exclude. ``exclude_row`` removes that row, ``exclude_hole``
  removes every row of the hole in every table.
* Overlapping intervals are all excluded; MineCore does not guess which one
  is right.
* A hole's depth is its collar ``max_depth`` when given, otherwise the deepest
  usable station, interval end or point (reported as ``HOLE_DEPTH_DERIVED``).
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from minecore._core import QuantityKind, Unit
from minecore.drillholes.issues import CATALOGUE, Effect, Issue, Severity
from minecore.drillholes.model import (
    SRC_LINE,
    DrillholeDatabase,
    DrillholeError,
    FieldSpec,
    FieldType,
    Flag,
    Role,
    TableInfo,
)

_Bools = npt.NDArray[np.bool_]
_Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ValidationSettings:
    """Thresholds, recorded in every report."""

    dogleg_deg_per_30m: float = 3.0
    depth_tolerance_m: float = 0.1
    gap_tolerance_m: float = 0.001
    overlap_tolerance_m: float = 1e-6

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not (math.isfinite(value) and value >= 0):
                raise DrillholeError(f"validation setting {name} must be >= 0, not {value}")


@dataclass(frozen=True)
class ValidationReport:
    settings: ValidationSettings
    issues: tuple[Issue, ...]
    usable: dict[str, _Bools]
    """Per table: True where a row may be used downstream."""
    holes: tuple[str, ...]
    """Usable holes: collar present and not excluded."""
    hole_depth: dict[str, float] = field(default_factory=dict)
    """Per usable hole: declared or derived depth (NaN when there is no data)."""

    def count(self, code: str | None = None, severity: Severity | None = None) -> int:
        return sum(
            1
            for i in self.issues
            if (code is None or i.code == code) and (severity is None or i.severity is severity)
        )

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for issue in self.issues:
            counts[issue.code] += 1
        return dict(sorted(counts.items()))

    def to_json(self) -> dict[str, Any]:
        return {
            "settings": asdict(self.settings),
            "summary": self.summary(),
            "usable_rows": {t: int(m.sum()) for t, m in self.usable.items()},
            "usable_holes": len(self.holes),
            "issues": [i.to_json() for i in self.issues],
        }


@dataclass
class _Table:
    info: TableInfo
    ids: npt.NDArray[np.object_]
    lines: list[int | None]
    data: pa.Table
    usable: _Bools

    def floats(self, name: str) -> _Floats:
        return np.asarray(self.data.column(name).to_numpy(zero_copy_only=False), dtype=np.float64)

    def has(self, name: str) -> bool:
        return name in self.data.column_names


class _Validator:
    def __init__(self, db: DrillholeDatabase, settings: ValidationSettings) -> None:
        self.settings = settings
        self.issues: list[Issue] = []
        self.tables: dict[str, _Table] = {}
        for info in db.tables().values():
            data = db.table(info.name)
            ids = np.array(
                [
                    None if v is None or not v.strip() else v
                    for v in data.column("hole_id").to_pylist()
                ],
                dtype=object,
            )
            self.tables[info.name] = _Table(
                info, ids, data.column(SRC_LINE).to_pylist(), data, np.ones(data.num_rows, bool)
            )
        collars = [t for t in self.tables.values() if t.info.role is Role.COLLAR]
        if not collars:
            raise DrillholeError(f"database {db.name!r} has no collar table")
        self.collar = collars[0]
        self.excluded_holes: set[str] = set()
        self.max_depth: dict[str, float] = {}

    # -- helpers --------------------------------------------------------------

    def add(self, code: str, t: _Table, message: str, row: int | None = None, **kw: Any) -> None:
        hole = kw.pop("hole_id", None)
        if row is not None:
            hole = hole if hole is not None else t.ids[row]
            kw.setdefault("src_line", t.lines[row])
            if CATALOGUE[code].effect is Effect.EXCLUDE_ROW:
                t.usable[row] = False
        self.issues.append(Issue(code, t.info.name, message, hole_id=hole, row=row, **kw))
        if CATALOGUE[code].effect is Effect.EXCLUDE_HOLE and hole is not None:
            self.excluded_holes.add(hole)

    def by_hole(self, t: _Table) -> Iterator[tuple[str, npt.NDArray[np.intp]]]:
        """Usable row indices per hole, holes in sorted order, rows in table order."""
        groups: dict[str, list[int]] = defaultdict(list)
        for i in _where(t.usable):
            groups[t.ids[i]].append(int(i))
        for hole in sorted(groups):
            yield hole, np.array(groups[hole], dtype=np.intp)

    # -- collar ---------------------------------------------------------------

    def check_collar(self) -> None:
        t = self.collar
        self.check_ids(t)
        counts: dict[str, list[int]] = defaultdict(list)
        for i in _where(t.usable):
            counts[t.ids[i]].append(int(i))
        for hole, rows in counts.items():
            if len(rows) > 1:
                lines = ", ".join(str(t.lines[r]) for r in rows)
                for r in rows:
                    self.add(
                        "COLLAR_DUPLICATE",
                        t,
                        f"{len(rows)} collars for {hole!r} (lines {lines})",
                        r,
                    )
        xyz = np.column_stack([t.floats(c) for c in ("x", "y", "z")])
        for r in _where(t.usable & ~np.isfinite(xyz).all(axis=1)):
            self.add("COLLAR_COORD_INVALID", t, "x, y and z must be finite numbers", int(r))
        depth = t.floats("max_depth") if t.has("max_depth") else np.full(len(t.ids), np.nan)
        for r in _where(t.usable):
            hole = t.ids[r]
            if np.isnan(depth[r]):
                continue
            if not (np.isfinite(depth[r]) and depth[r] > 0):
                self.add("COLLAR_MAX_DEPTH_INVALID", t, f"max_depth {depth[r]} is not > 0", int(r))
            else:
                self.max_depth[hole] = float(depth[r])
        self.collar_ids = {h for h in t.ids if h is not None}

    def check_ids(self, t: _Table) -> None:
        for r in _where(t.ids == None):  # noqa: E711 - object array comparison
            self.add("HOLE_ID_MISSING", t, "hole_id is empty", int(r))

    def check_orphans(self, t: _Table) -> None:
        self.check_ids(t)
        orphans: dict[str, list[int]] = defaultdict(list)
        for r in _where(t.usable):
            if t.ids[r] not in self.collar_ids:
                orphans[t.ids[r]].append(int(r))
        for hole, rows in sorted(orphans.items()):
            for r in rows:
                self.add("ORPHAN_ROW", t, f"no collar for hole {hole!r}", r)

    def check_case(self) -> None:
        spellings: dict[str, dict[str, str]] = defaultdict(dict)
        for name, t in self.tables.items():
            for hole in {h for h in t.ids if h is not None}:
                spellings[hole.casefold()].setdefault(hole, name)
        for group in spellings.values():
            if len(group) > 1:
                names = ", ".join(f"{h!r} ({tbl})" for h, tbl in sorted(group.items()))
                first = sorted(group)[0]
                self.add(
                    "HOLE_ID_CASE",
                    self.tables[group[first]],
                    f"IDs differ only by case: {names}",
                    hole_id=first,
                )

    # -- downhole tables ------------------------------------------------------

    def check_depths(self, t: _Table, code: str, *columns: str) -> None:
        for name in columns:
            values = t.floats(name)
            for r in _where(t.usable & ~(np.isfinite(values) & (values >= 0))):
                self.add(code, t, f"{name} {values[r]} is not a finite depth >= 0", int(r))

    def check_survey(self, t: _Table) -> None:
        self.check_depths(t, "SURVEY_DEPTH_INVALID", "depth")
        az, dip = t.floats("azimuth"), t.floats("dip")
        for r in _where(t.usable & ~(np.isfinite(az) & (az >= 0) & (az <= 360))):
            self.add("SURVEY_AZIMUTH_INVALID", t, f"azimuth {az[r]} is not in [0, 360]", int(r))
        for r in _where(t.usable & ~(np.isfinite(dip) & (np.abs(dip) <= 90))):
            self.add("SURVEY_DIP_INVALID", t, f"dip {dip[r]} is not in [-90, 90]", int(r))
        depth = t.floats("depth")
        tol = self.settings.depth_tolerance_m
        limit = self.settings.dogleg_deg_per_30m
        for hole, group in self.by_hole(t):
            rows = group[np.argsort(depth[group], kind="stable")]
            d = depth[rows]
            for k in _where(np.diff(d) == 0):
                a, b = int(rows[k]), int(rows[k + 1])
                self.add(
                    "SURVEY_DUPLICATE_DEPTH",
                    t,
                    f"stations at lines {t.lines[a]} and {t.lines[b]} share depth {d[k]}",
                    int(b),
                )
            if hole in self.max_depth:
                for r in rows[d > self.max_depth[hole] + tol].tolist():
                    self.add(
                        "SURVEY_BEYOND_DEPTH",
                        t,
                        f"depth {depth[r]} is below max_depth {self.max_depth[hole]}",
                        int(r),
                    )
            vectors = _directions(az[rows], dip[rows])
            for k in range(len(rows) - 1):
                length = d[k + 1] - d[k]
                if length <= 0:
                    continue
                cosine = float(np.clip(vectors[k] @ vectors[k + 1], -1.0, 1.0))
                severity = math.degrees(math.acos(cosine)) * 30.0 / length
                if severity > limit:
                    self.add(
                        "SURVEY_DOGLEG",
                        t,
                        f"dogleg {severity:.2f}°/30 m between {d[k]} and {d[k + 1]} m "
                        f"exceeds {limit}°/30 m",
                        int(rows[k + 1]),
                    )

    def check_intervals(self, t: _Table) -> None:
        self.check_depths(t, "INTERVAL_DEPTH_INVALID", "from", "to")
        start, end = t.floats("from"), t.floats("to")
        for bad in _where(t.usable & (start >= end)):
            self.add("INTERVAL_REVERSED", t, f"from {start[bad]} >= to {end[bad]}", int(bad))
        self.check_overlaps(t, start, end)
        gap = self.settings.gap_tolerance_m
        depth_tol = self.settings.depth_tolerance_m
        for hole, group in self.by_hole(t):
            rows = group[np.argsort(start[group], kind="stable")]
            for a, b in itertools.pairwise(rows):
                if start[b] - end[a] > gap:
                    self.add(
                        "INTERVAL_GAP",
                        t,
                        f"gap of {start[b] - end[a]:.3f} m from {end[a]} to {start[b]}",
                        int(b),
                    )
            if hole in self.max_depth:
                for deep in rows[end[rows] > self.max_depth[hole] + depth_tol].tolist():
                    self.add(
                        "INTERVAL_BEYOND_DEPTH",
                        t,
                        f"to {end[deep]} is below max_depth {self.max_depth[hole]}",
                        int(deep),
                    )

    def check_overlaps(self, t: _Table, start: _Floats, end: _Floats) -> None:
        tol = self.settings.overlap_tolerance_m
        overlapping: list[tuple[int, int]] = []
        for _hole, group in self.by_hole(t):
            rows = group[np.lexsort((end[group], start[group]))]
            deepest = int(rows[0])
            for row in rows[1:]:
                i = int(row)
                if start[i] < end[deepest] - tol:
                    overlapping.append((i, deepest))
                if end[i] > end[deepest]:
                    deepest = i
        marked: set[int] = set()
        for first, second in overlapping:
            for this, partner in ((first, second), (second, first)):
                if this not in marked:
                    marked.add(this)
                    self.add(
                        "INTERVAL_OVERLAP",
                        t,
                        f"[{start[this]}, {end[this]}) overlaps line {t.lines[partner]} "
                        f"[{start[partner]}, {end[partner]})",
                        this,
                    )

    def check_points(self, t: _Table) -> None:
        self.check_depths(t, "POINT_DEPTH_INVALID", "depth")

    def check_values(self, t: _Table) -> None:
        for spec in t.info.fields:
            if spec.type is not FieldType.NUMERIC:
                continue
            values = t.floats(spec.name)
            flags = np.asarray(t.data.column(spec.flag_column).to_numpy(zero_copy_only=False))
            for flag in Flag:
                if flag is Flag.MEASURED:
                    continue
                n = int(np.count_nonzero(t.usable & (flags == flag)))
                if n:
                    self.add(
                        "VALUE_QUALIFIED",
                        t,
                        f"{n} values flagged {flag.name.lower()}",
                        field=spec.name,
                    )
            low, high = _valid_range(spec)
            measured = t.usable & (flags == Flag.MEASURED)
            outside = measured & ((values < low) | (values > high))
            for r in _where(outside):
                self.add(
                    "VALUE_OUT_OF_RANGE",
                    t,
                    f"{spec.name} = {values[r]} {spec.unit or ''}".strip()
                    + f" is outside [{low}, {high}]",
                    int(r),
                    field=spec.name,
                )

    # -- holes ----------------------------------------------------------------

    def check_holes(self) -> tuple[tuple[str, ...], dict[str, float]]:
        holes = sorted(
            {self.collar.ids[r] for r in _where(self.collar.usable)} - self.excluded_holes
        )
        deepest: dict[str, float] = defaultdict(lambda: -math.inf)
        has_data: set[str] = set()
        surveyed: set[str] = set()
        for t in self.tables.values():
            role = t.info.role
            if role is Role.COLLAR:
                continue
            column = "to" if role is Role.INTERVAL else "depth"
            values = t.floats(column)
            for hole, rows in self.by_hole(t):
                has_data.add(hole)
                if role is Role.SURVEY:
                    surveyed.add(hole)
                deepest[hole] = max(deepest[hole], float(values[rows].max()))
        oriented = self.collar.has("azimuth") and self.collar.has("dip")
        depth: dict[str, float] = {}
        for hole in holes:
            if hole not in has_data:
                self.add("HOLE_NO_DATA", self.collar, "collar has no downhole data", hole_id=hole)
            if hole not in surveyed:
                fallback = "collar orientation" if oriented else "a vertical hole"
                self.add(
                    "SURVEY_NONE",
                    self.collar,
                    f"no survey stations; {fallback} is used",
                    hole_id=hole,
                )
            if hole in self.max_depth:
                depth[hole] = self.max_depth[hole]
            elif hole in has_data:
                depth[hole] = deepest[hole]
                self.add(
                    "HOLE_DEPTH_DERIVED",
                    self.collar,
                    f"no max_depth; deepest data at {deepest[hole]} m is used",
                    hole_id=hole,
                )
            else:
                depth[hole] = math.nan
        return tuple(holes), depth

    def run(self) -> ValidationReport:
        self.check_collar()
        downhole = [t for t in self.tables.values() if t.info.role is not Role.COLLAR]
        for t in downhole:
            self.check_orphans(t)
        self.check_case()
        for t in downhole:
            match t.info.role:
                case Role.SURVEY:
                    self.check_survey(t)
                case Role.INTERVAL:
                    self.check_intervals(t)
                case Role.POINT:
                    self.check_points(t)
                case _:  # pragma: no cover - roles are exhaustive
                    raise AssertionError(t.info.role)
        for t in self.tables.values():
            for r in _where(t.usable):
                if t.ids[r] in self.excluded_holes:
                    t.usable[r] = False
        holes, depth = self.check_holes()
        for t in self.tables.values():
            self.check_values(t)
        return ValidationReport(
            settings=self.settings,
            issues=tuple(self.issues),
            usable={name: t.usable.copy() for name, t in self.tables.items()},
            holes=holes,
            hole_depth=depth,
        )


def _where(mask: _Bools) -> list[int]:
    """Indices of the true entries, as Python ints."""
    return [int(i) for i in np.flatnonzero(mask)]


def _directions(azimuth: _Floats, dip: _Floats) -> _Floats:
    """Unit vectors (east, north, up) for azimuth/dip in degrees (dip negative down)."""
    az, dp = np.radians(azimuth), np.radians(dip)
    return np.column_stack([np.sin(az) * np.cos(dp), np.cos(az) * np.cos(dp), np.sin(dp)])


def _valid_range(spec: FieldSpec) -> tuple[float, float]:
    """Physically possible values of a numeric field, in the field's own unit."""
    if spec.quantity is None or spec.unit is None:
        return -math.inf, math.inf
    kind = QuantityKind.from_name(spec.quantity)
    if kind == QuantityKind.GRADE:
        # A grade is a mass fraction: 0 to 1 in coherent units (100 %, 1e6 ppm).
        return 0.0, 1.0 / Unit(spec.unit).si_factor
    if kind in (QuantityKind.DENSITY, QuantityKind.LENGTH, QuantityKind.MASS):
        return 0.0, math.inf
    return -math.inf, math.inf


def validate(db: DrillholeDatabase, settings: ValidationSettings | None = None) -> ValidationReport:
    """Validate every table of ``db`` and return the report."""
    return _Validator(db, settings or ValidationSettings()).run()
