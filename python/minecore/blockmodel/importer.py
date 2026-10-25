"""Import block models exported from other software as text (CSV).

Expected: one row per block (or sub-block) with centroid coordinates and
numeric attributes. Column names are matched case-insensitively:

* centroids: ``x``/``xc``/``xcentre``/``east``..., ``y``..., ``z``...;
* optional block sizes: ``xinc``/``dx``/``xsize``/``size_x``... (sub-blocked
  models carry one size per row).

The regular grid uses the largest block size per axis (the parent cell) or,
without size columns, the smallest spacing between distinct centroid
coordinates. Several rows in one parent cell (sub-blocks) are merged by a
volume-weighted mean (missing values are ignored). Text columns are skipped
and reported. A rotated model is read with its ``azimuth`` (bearing of the
model's +j axis, degrees clockwise from north; ADR-0009): centroids are world
coordinates and sizes are along the model axes. Text exports rarely carry the
rotation, so it is given by the user.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from minecore.blockmodel.model import BlockModel, BlockModelError, Grid
from minecore.io.sniff import TableFormatError, parse_number, read_rows
from minecore.project import Project

Floats = npt.NDArray[np.float64]

CENTRE_NAMES = {
    0: ("x", "xc", "xcentre", "xcenter", "x_centre", "x_center", "xcen", "east", "easting"),
    1: ("y", "yc", "ycentre", "ycenter", "y_centre", "y_center", "ycen", "north", "northing"),
    2: ("z", "zc", "zcentre", "zcenter", "z_centre", "z_center", "zcen", "elev", "rl"),
}
SIZE_NAMES = {
    0: ("xinc", "dx", "xsize", "size_x", "x_size", "xdim", "_dx"),
    1: ("yinc", "dy", "ysize", "size_y", "y_size", "ydim", "_dy"),
    2: ("zinc", "dz", "zsize", "size_z", "z_size", "zdim", "_dz"),
}


@dataclass(frozen=True)
class BlockImport:
    model: BlockModel
    rows: int
    blocks_filled: int
    merged_rows: int  # rows that shared a parent cell with another row
    attributes: tuple[str, ...]
    skipped_columns: tuple[str, ...]


def _find(header: list[str], names: tuple[str, ...]) -> int | None:
    lower = [h.strip().lower() for h in header]
    for n in names:
        if n in lower:
            return lower.index(n)
    return None


def _spacing(values: Floats) -> float:
    """Smallest spacing of distinct values (NaN when there is only one value)."""
    u = np.unique(np.round(values, 6))
    d = np.diff(u)
    d = d[d > 1e-6]
    return float(d.min()) if d.size else math.nan


def import_block_csv(  # noqa: PLR0912, PLR0913, PLR0915 - one parsing pass, keyword options
    project: Project,
    path: Path,
    name: str,
    *,
    size: tuple[float, float, float] | None = None,
    units: dict[str, str] | None = None,
    azimuth: float = 0.0,
) -> BlockImport:
    dialect, rows = read_rows(path)
    header = rows[dialect.header_row]
    body = [r for r in rows[dialect.header_row + 1 :] if any(c.strip() for c in r)]
    if not body:
        raise BlockModelError(f"{path.name}: no data rows")
    centre = [_find(header, CENTRE_NAMES[a]) for a in range(3)]
    if None in centre:
        raise BlockModelError(f"{path.name}: x, y and z centroid columns are required")
    sizes = [_find(header, SIZE_NAMES[a]) for a in range(3)]
    width = len(header)
    table = np.full((len(body), width), np.nan)
    text_cols: set[int] = set()
    for r, row in enumerate(body):
        for c in range(min(width, len(row))):
            cell = row[c].strip()
            if not cell:
                continue
            try:
                value = parse_number(cell, dialect)
            except TableFormatError:
                value = None
            if value is None:
                text_cols.add(c)
            else:
                table[r, c] = value
    world = table[:, [int(c) for c in centre if c is not None]]
    if not np.isfinite(world).all():
        raise BlockModelError(f"{path.name}: missing or non-numeric centroid coordinates")
    frame = Grid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1, 1, 1), azimuth)
    xyz = frame.to_local(world)  # model axes, so spacing and sizes are read along them
    have_sizes = all(s is not None for s in sizes)
    row_size = (
        table[:, [int(s) for s in sizes if s is not None]] if have_sizes else np.ones_like(xyz)
    )
    if size is not None:
        parent = np.asarray(size, np.float64)
    elif have_sizes:
        parent = np.nanmax(row_size, axis=0)
    else:
        parent = np.array([_spacing(xyz[:, a]) for a in range(3)])
        if np.isnan(parent).all():
            raise BlockModelError("cannot infer a block size from a single block; give --size")
        # An axis with one coordinate only (a single row or level) borrows the others' size.
        parent[np.isnan(parent)] = np.nanmin(parent)
    if not (np.isfinite(parent).all() and (parent > 0).all()):
        raise BlockModelError("block sizes must be positive")
    lo = xyz - (0.5 * row_size if have_sizes else 0.5 * parent)
    origin = lo.min(axis=0)
    idx = np.floor((xyz - origin) / parent + 1e-9).astype(np.int64)
    count = idx.max(axis=0) + 1
    o = frame.to_world(origin[None, :])[0]
    grid = Grid(
        (float(o[0]), float(o[1]), float(o[2])),
        (float(parent[0]), float(parent[1]), float(parent[2])),
        (int(count[0]), int(count[1]), int(count[2])),
        float(azimuth),
    )
    flat = idx[:, 0] + count[0] * (idx[:, 1] + count[1] * idx[:, 2])
    weight = np.prod(row_size, axis=1) if have_sizes else np.ones(len(body))
    skip = {int(c) for c in centre if c is not None} | {int(s) for s in sizes if s is not None}
    attrs: dict[str, Floats] = {}
    skipped = []
    for c, title in enumerate(header):
        if c in skip:
            continue
        label = title.strip()
        if c in text_cols:
            skipped.append(label)
            continue
        key = label if label.isidentifier() and not label.startswith("_") else _clean(label)
        col = table[:, c]
        ok = np.isfinite(col)
        num = np.bincount(flat[ok], weight[ok] * col[ok], minlength=grid.blocks)
        den = np.bincount(flat[ok], weight[ok], minlength=grid.blocks)
        with np.errstate(invalid="ignore", divide="ignore"):
            attrs[key] = np.where(den > 0, num / den, np.nan)
    if not attrs:
        raise BlockModelError(f"{path.name}: no numeric attribute columns")
    model = BlockModel.create(project, name, grid)
    unit_map = units or {}
    model.set_attributes(
        attrs,
        {k: {"unit": unit_map.get(k), "source": path.name} for k in attrs},
        f"Import block model {path.name}",
    )
    filled = np.unique(flat)
    return BlockImport(
        model,
        len(body),
        int(filled.size),
        int(len(flat) - filled.size),
        tuple(attrs),
        tuple(skipped),
    )


def _clean(label: str) -> str:
    out = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in label).strip("_")
    if not out or out[0].isdigit():
        out = f"a_{out}"
    return out


def parse_units(text: str) -> dict[str, str]:
    """``"au=g/t; cu=%"`` -> ``{"au": "g/t", "cu": "%"}``."""
    out: dict[str, str] = {}
    for part in text.replace(",", ";").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out
