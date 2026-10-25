"""Whole-database operations built on validation: traces and composites.

Only rows and holes that validation leaves usable are processed; the result
says how many holes were skipped and why.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from minecore.drillholes.compositing import LastComposite, composite_by_domain, composite_fixed
from minecore.drillholes.desurvey import desurvey
from minecore.drillholes.model import (
    DrillholeDatabase,
    DrillholeError,
    FieldSpec,
    FieldType,
    Flag,
    Role,
    TableInfo,
)
from minecore.drillholes.validate import ValidationReport, validate

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class HoleGeometry:
    hole_id: str
    collar: tuple[float, float, float]
    station_depth: Floats
    azimuth: Floats
    dip: Floats
    depth: float

    def at(self, depths: Floats) -> Floats:
        return desurvey(self.collar, self.station_depth, self.azimuth, self.dip, depths)

    def trace(self, step: float = 5.0) -> Floats:
        """Polyline from collar to hole depth through every station."""
        end = self.depth if math.isfinite(self.depth) else float(self.station_depth.max())
        points = np.unique(
            np.concatenate(
                [np.arange(0.0, end, step), self.station_depth[self.station_depth < end], [end]]
            )
        )
        return self.at(points)


def hole_geometry(
    db: DrillholeDatabase, report: ValidationReport | None = None
) -> dict[str, HoleGeometry]:
    """Desurvey inputs of every usable hole."""
    report = report or validate(db)
    collar_info = db.tables_with_role(Role.COLLAR)[0]
    collar = db.table(collar_info.name)
    use = report.usable[collar_info.name]
    ids = collar.column("hole_id").to_pylist()
    xyz = np.column_stack([collar.column(c).to_numpy() for c in ("x", "y", "z")])
    names = collar.column_names
    c_az = collar.column("azimuth").to_numpy() if "azimuth" in names else np.full(len(ids), np.nan)
    c_dip = collar.column("dip").to_numpy() if "dip" in names else np.full(len(ids), np.nan)

    stations: dict[str, tuple[Floats, Floats, Floats]] = {}
    surveys = db.tables_with_role(Role.SURVEY)
    if surveys:
        s = db.table(surveys[0].name)
        mask = report.usable[surveys[0].name]
        s_ids = np.array(s.column("hole_id").to_pylist(), dtype=object)[mask]
        md, az, dp = (
            np.asarray(s.column(c).to_numpy(), float)[mask] for c in ("depth", "azimuth", "dip")
        )
        for hole in set(s_ids.tolist()):
            sel = s_ids == hole
            order = np.argsort(md[sel])
            stations[hole] = (md[sel][order], az[sel][order], dp[sel][order])

    result: dict[str, HoleGeometry] = {}
    for i, hole in enumerate(ids):
        if not use[i] or hole not in report.hole_depth:
            continue
        if hole in stations:
            md, az, dp = stations[hole]
        else:
            az0 = 0.0 if math.isnan(c_az[i]) else float(c_az[i])
            dp0 = -90.0 if math.isnan(c_dip[i]) else float(c_dip[i])
            md, az, dp = np.array([0.0]), np.array([az0]), np.array([dp0])
        result[hole] = HoleGeometry(
            hole,
            (float(xyz[i, 0]), float(xyz[i, 1]), float(xyz[i, 2])),
            md,
            az,
            dp,
            report.hole_depth[hole],
        )
    return result


@dataclass(frozen=True)
class CompositeResult:
    table: pa.Table
    skipped_holes: dict[str, str] = field(default_factory=dict)


def _grades(info: TableInfo, data: pa.Table, name: str, below_detection_factor: float) -> Floats:
    spec = next((f for f in info.fields if f.name == name), None)
    if spec is None or spec.type is not FieldType.NUMERIC:
        raise DrillholeError(f"table {info.name!r} has no numeric field {name!r}")
    values = np.asarray(data.column(name).to_numpy(zero_copy_only=False), float).copy()
    flags = np.asarray(data.column(spec.flag_column).to_numpy(zero_copy_only=False))
    values[flags == Flag.BELOW_DETECTION] *= below_detection_factor
    values[flags >= Flag.MISSING] = np.nan
    return values


def composite_table(  # noqa: PLR0913 - options are keyword-only
    db: DrillholeDatabase,
    table: str,
    fields: list[str],
    length: float,
    *,
    domain_field: str | None = None,
    min_coverage: float = 0.5,
    last: LastComposite = LastComposite.KEEP,
    below_detection_factor: float = 0.5,
    report: ValidationReport | None = None,
) -> CompositeResult:
    """Composite numeric ``fields`` of an interval table, with coordinates of each midpoint.

    Below-detection values enter as ``limit * below_detection_factor`` (stated
    policy, recorded by the caller); other qualified values are missing.
    """
    if not fields:
        raise DrillholeError("choose at least one field to composite")
    report = report or validate(db)
    info = db.table_info(table)
    if info.role is not Role.INTERVAL:
        raise DrillholeError(f"{table!r} is not an interval table")
    data = db.table(table)
    usable = report.usable[table]
    ids = np.array(data.column("hole_id").to_pylist(), dtype=object)
    start = np.asarray(data.column("from").to_numpy(), float)
    end = np.asarray(data.column("to").to_numpy(), float)
    grades = {f: _grades(info, data, f, below_detection_factor) for f in fields}
    domains = (
        np.array(data.column(domain_field).cast(pa.string()).to_pylist(), dtype=object)
        if domain_field
        else None
    )
    geometry = hole_geometry(db, report)
    rows: dict[str, list[object]] = {
        k: [] for k in ("hole_id", "from", "to", "x", "y", "z", "domain")
    }
    for f in fields:
        rows[f] = []
        rows[f + "_coverage"] = []
    skipped: dict[str, str] = {}
    for hole in sorted(set(ids[usable].tolist())):
        if hole not in geometry:
            skipped[hole] = "no usable collar"
            continue
        sel = usable & (ids == hole)
        per_field = []
        for f in fields:
            if domains is not None:
                comps = composite_by_domain(
                    start[sel],
                    end[sel],
                    grades[f][sel],
                    domains[sel].tolist(),
                    length,
                    min_coverage=min_coverage,
                    last=last,
                )
            else:
                comps = composite_fixed(
                    start[sel],
                    end[sel],
                    grades[f][sel],
                    length,
                    min_coverage=min_coverage,
                    last=last,
                )
            per_field.append(comps)
        base = per_field[0]
        mids = geometry[hole].at(np.array([(c.start + c.end) / 2 for c in base]))
        for k, c in enumerate(base):
            rows["hole_id"].append(hole)
            rows["from"].append(c.start)
            rows["to"].append(c.end)
            rows["x"].append(float(mids[k, 0]))
            rows["y"].append(float(mids[k, 1]))
            rows["z"].append(float(mids[k, 2]))
            rows["domain"].append(c.domain)
            for f, comps in zip(fields, per_field, strict=True):
                rows[f].append(comps[k].grade)
                rows[f + "_coverage"].append(comps[k].coverage)
    if domains is None:
        rows.pop("domain")
    return CompositeResult(pa.table(rows), skipped)


def save_composites(
    db: DrillholeDatabase, name: str, result: CompositeResult, source_table: str, fields: list[str]
) -> TableInfo:
    """Store composites as an interval table (coordinates as numeric fields)."""
    t = result.table
    info = db.table_info(source_table)
    units = {f.name: f for f in info.fields}
    specs = [
        FieldSpec("mid_x", FieldType.NUMERIC, unit="m", quantity="length"),
        FieldSpec("mid_y", FieldType.NUMERIC, unit="m", quantity="length"),
        FieldSpec("mid_z", FieldType.NUMERIC, unit="m", quantity="length"),
    ]
    columns: dict[str, object] = {
        "hole_id": t.column("hole_id"),
        "from": t.column("from"),
        "to": t.column("to"),
        "mid_x": t.column("x"),
        "mid_y": t.column("y"),
        "mid_z": t.column("z"),
    }
    if "domain" in t.column_names:
        specs.append(FieldSpec("domain", FieldType.CATEGORY))
        columns["domain"] = t.column("domain")
    for f in fields:
        spec = units[f]
        specs.append(FieldSpec(f, FieldType.NUMERIC, unit=spec.unit, quantity=spec.quantity))
        specs.append(FieldSpec(f + "_coverage", FieldType.NUMERIC))
        columns[f] = t.column(f)
        columns[f + "_coverage"] = t.column(f + "_coverage")
    return db.put_table(name, Role.INTERVAL, pa.table(columns), tuple(specs))
