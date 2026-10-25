"""Estimate block grades from drillhole intervals (usually composites)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from minecore.blockmodel.model import BlockModel
from minecore.drillholes import DrillholeDatabase, DrillholeError, FieldType, Flag, validate
from minecore.drillholes.workflow import hole_geometry
from minecore.estimation import Method, Search, VariogramModel, estimate
from minecore.jobs import Progress

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class SampleSet:
    xyz: Floats
    values: Floats
    hole_index: npt.NDArray[np.int64]
    unit: str | None


def interval_samples(
    db: DrillholeDatabase, table: str, field: str, *, below_detection_factor: float = 0.5
) -> SampleSet:
    """Interval midpoints and values of a numeric field, usable rows only.

    Uses stored ``mid_x/mid_y/mid_z`` (composites) when present, else desurveys.
    """
    report = validate(db)
    info = db.table_info(table)
    spec = next((f for f in info.fields if f.name == field), None)
    if spec is None or spec.type is not FieldType.NUMERIC:
        raise DrillholeError(f"table {table!r} has no numeric field {field!r}")
    data = db.table(table)
    usable = report.usable[table]
    values = np.asarray(data.column(field).to_numpy(zero_copy_only=False), np.float64).copy()
    flags = np.asarray(data.column(spec.flag_column).to_numpy(zero_copy_only=False))
    values[flags == Flag.BELOW_DETECTION] *= below_detection_factor
    values[flags >= Flag.MISSING] = np.nan
    ids = np.array(data.column("hole_id").to_pylist(), dtype=object)
    if {"mid_x", "mid_y", "mid_z"} <= set(data.column_names):
        xyz = np.column_stack(
            [
                np.asarray(data.column(c).to_numpy(zero_copy_only=False), float)
                for c in ("mid_x", "mid_y", "mid_z")
            ]
        )
    else:
        geometry = hole_geometry(db, report)
        mid = (
            np.asarray(data.column("from").to_numpy(), float)
            + np.asarray(data.column("to").to_numpy(), float)
        ) / 2
        xyz = np.full((data.num_rows, 3), np.nan)
        for hole, g in geometry.items():
            sel = usable & (ids == hole)
            if sel.any():
                xyz[sel] = g.at(mid[sel])
    keep = usable & np.isfinite(values) & np.isfinite(xyz).all(axis=1)
    names = {h: i for i, h in enumerate(sorted(set(ids[keep].tolist())))}
    holes = np.array([names[h] for h in ids[keep]], np.int64)
    return SampleSet(xyz[keep], values[keep], holes, spec.unit)


def estimate_blocks(  # noqa: PLR0913 - options are keyword-only
    model: BlockModel,
    samples: SampleSet,
    attribute: str,
    *,
    method: Method,
    search: Search,
    variogram: VariogramModel | None = None,
    power: float = 2.0,
    discretisation: tuple[int, int, int] = (2, 2, 2),
    progress: Progress | None = None,
) -> int:
    """Estimate ``attribute`` into ``model``; returns the number of unestimated blocks."""
    grid = model.grid
    result = estimate(
        samples.xyz,
        samples.values,
        grid.centroids(),
        search,
        method,
        variogram=variogram,
        power=power,
        block_size=grid.size,
        discretisation=discretisation if method is Method.ORDINARY_KRIGING else (1, 1, 1),
        hole_ids=samples.hole_index,
        progress=progress,
    )
    info: dict[str, dict[str, Any]] = {
        attribute: {
            "unit": samples.unit,
            "method": method.value,
            "variogram": variogram.to_json() if variogram else None,
            "search": {
                "ranges": [search.ellipsoid.major, search.ellipsoid.semi, search.ellipsoid.minor],
                "angles": [search.ellipsoid.azimuth, search.ellipsoid.dip, search.ellipsoid.rake],
                "min_samples": search.min_samples,
                "max_samples": search.max_samples,
                "max_per_hole": search.max_per_hole,
            },
            "power": power if method is Method.INVERSE_DISTANCE else None,
        },
        f"{attribute}_n": {"unit": None},
        f"{attribute}_dist": {"unit": None},
    }
    values = {
        attribute: result.value,
        f"{attribute}_n": result.samples_used.astype(np.float64),
        f"{attribute}_dist": result.mean_distance,
    }
    if method is Method.ORDINARY_KRIGING:
        values[f"{attribute}_kv"] = result.variance
        info[f"{attribute}_kv"] = {"unit": None}
    model.set_attributes(values, info, f"Estimate {attribute!r} by {method.value}")
    return result.unestimated
