"""Reserves of a designed pit and volumes between surfaces.

``between_fraction``: for each block, the share of its height that lies
between a lower surface (the designed pit, or the end-of-period surface) and
an upper one (topography), measured on the block's vertical centre line. It
prorates partial blocks at the surfaces, which the pit optimiser does not.

``bench_reserves``: tonnes and grade per bench inside that volume, split
into ore (grade >= cut-off) and waste, optionally per value of a category
attribute (rock type, confidence category, pushback). Blocks without a grade
estimate count as waste and are counted. This is a design inventory, not a
classified resource or reserve statement (rule 8).

``volume_between``: cut (``a`` above ``b``) and fill (``b`` above ``a``)
volumes on a regular raster of ``cell`` metres where both surfaces exist
(inside both triangulation hulls), e.g. topography against a mined-out
survey, or a design against the current pit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.spatial import Delaunay

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel
from minecore.surfaces import Surface

Floats = npt.NDArray[np.float64]


class ReservesError(MineCoreError):
    """Invalid reserve or volume input."""


def between_fraction(model: BlockModel, top: Surface | None, bottom: Surface) -> Floats:
    """Share (0..1) of each block's height between ``bottom`` and ``top`` (None: no top)."""
    grid = model.grid
    nx, ny, nz = grid.count
    c = grid.centroids()
    xy = c[: nx * ny, :2]
    z_bottom = np.tile(bottom.elevation(xy)[0], nz)
    z_top = np.tile(top.elevation(xy)[0], nz) if top is not None else np.full(grid.blocks, np.inf)
    half = 0.5 * grid.size[2]
    lo = np.maximum(c[:, 2] - half, z_bottom)
    hi = np.minimum(c[:, 2] + half, z_top)
    share: Floats = np.clip((hi - lo) / grid.size[2], 0.0, 1.0)
    return share


@dataclass(frozen=True)
class BenchRow:
    toe: float  # bench floor elevation, m
    category: str  # "" when not split
    total_t: float
    ore_t: float
    grade: float  # ore mean grade
    metal: float  # ore tonnes x grade, in t x grade units
    waste_t: float
    unestimated_t: float  # waste tonnes without a grade estimate

    @property
    def strip_ratio(self) -> float:
        return self.waste_t / self.ore_t if self.ore_t > 0 else math.inf


def bench_reserves(  # noqa: PLR0913 - one report, keyword options
    model: BlockModel,
    grade: str,
    cutoff: float,
    fraction: Floats,
    *,
    density: float | str = 2.7,
    bench_height: float | None = None,
    category: str | None = None,
) -> list[BenchRow]:
    """Per-bench ore and waste inside ``fraction`` (see the module text), top bench first.

    ``bench_height`` groups block layers into benches from the model base
    (default: one bench per block layer).
    """
    grid = model.grid
    g = model.values(grade)
    rho = model.values(density) if isinstance(density, str) else np.full(grid.blocks, density)
    if not isinstance(density, str) and not (math.isfinite(density) and density > 0):
        raise ReservesError("density must be > 0 t/m3")
    t = np.nan_to_num(rho, nan=0.0) * grid.block_volume * np.clip(fraction, 0.0, 1.0)
    height = bench_height or grid.size[2]
    if height <= 0:
        raise ReservesError("bench height must be > 0")
    nx, ny, _ = grid.count
    k = np.arange(grid.blocks) // (nx * ny)
    base = grid.origin[2]
    floor = base + k * grid.size[2]
    bench = np.floor((floor - base) / height + 1e-9).astype(np.int64)
    cats = (
        np.array([_label(v) for v in model.values(category)], dtype=object)
        if category
        else np.full(grid.blocks, "", dtype=object)
    )
    known = np.isfinite(g)
    ore = known & (g >= cutoff)
    rows = []
    inside = t > 0
    for b in sorted(set(bench[inside].tolist()), reverse=True):
        in_bench = inside & (bench == b)
        for cat in sorted(set(cats[in_bench].tolist())):
            sel = in_bench & (cats == cat)
            o = sel & ore
            ore_t = float(t[o].sum())
            total = float(t[sel].sum())
            rows.append(
                BenchRow(
                    base + b * height,
                    str(cat),
                    total,
                    ore_t,
                    float((t[o] * g[o]).sum() / ore_t) if ore_t > 0 else math.nan,
                    float((t[o] * g[o]).sum()),
                    total - ore_t,
                    float(t[sel & ~known].sum()),
                )
            )
    return rows


def _label(v: float) -> str:
    if not math.isfinite(v):
        return "-"
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


@dataclass(frozen=True)
class VolumeResult:
    cut: float  # m3 where a is above b
    fill: float  # m3 where b is above a
    area: float  # m2 where both surfaces exist
    cells: int

    @property
    def net(self) -> float:
        return self.cut - self.fill


def volume_between(a: Surface, b: Surface, cell: float = 5.0) -> VolumeResult:
    """Cut and fill volumes between two surfaces on a raster of ``cell`` metres."""
    if not (math.isfinite(cell) and cell > 0):
        raise ReservesError("cell size must be > 0")
    lo = np.maximum(a.points[:, :2].min(axis=0), b.points[:, :2].min(axis=0))
    hi = np.minimum(a.points[:, :2].max(axis=0), b.points[:, :2].max(axis=0))
    if np.any(hi <= lo):
        raise ReservesError("the surfaces do not overlap in plan")
    n = np.ceil((hi - lo) / cell).astype(np.int64)
    if int(n[0]) * int(n[1]) > 25_000_000:
        raise ReservesError("too many raster cells; use a larger cell size")
    xs = lo[0] + (np.arange(n[0]) + 0.5) * cell
    ys = lo[1] + (np.arange(n[1]) + 0.5) * cell
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    xy = np.column_stack([gx.ravel(), gy.ravel()])
    both = (Delaunay(a.points[:, :2]).find_simplex(xy) >= 0) & (
        Delaunay(b.points[:, :2]).find_simplex(xy) >= 0
    )
    xy = xy[both]
    if len(xy) == 0:
        raise ReservesError("the surfaces do not overlap in plan")
    dz = a.elevation(xy)[0] - b.elevation(xy)[0]
    area = cell * cell
    return VolumeResult(
        float(np.clip(dz, 0, None).sum() * area),
        float(np.clip(-dz, 0, None).sum() * area),
        float(len(xy) * area),
        len(xy),
    )
