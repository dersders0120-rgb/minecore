"""Grade control on a blast: blasthole grades to ore/waste dig lines.

1. Blasthole samples (x, y, grade) are interpolated on a fine plan raster
   inside the blast outline by inverse distance weighting (power, search
   radius and the nearest ``max_samples`` holes).
2. Each cell gets a destination class from the cut-offs (class 0 below the
   first cut-off is waste; class k is between cut-off k and k+1).
3. The class map is smoothed to a minimum mining width (selective mining
   unit): a majority filter over a square window of that width, repeated
   until stable or ``passes`` times, so no polygon is narrower than what the
   loader can dig.
4. Dig lines are the boundaries of each class region (closed polylines at
   the bench toe elevation).

The report compares the smoothed (dug) classes with the ideal cell-by-cell
classes: ore lost to waste and waste sent to ore (dilution), in tonnes and
metal. Tonnes are area x bench height x density.
"""

from __future__ import annotations

import csv
import itertools
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from scipy.ndimage import uniform_filter
from scipy.spatial import cKDTree

from minecore._core import MineCoreError

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]


class GradeControlError(MineCoreError):
    """Invalid grade control input."""


def read_holes_csv(path: Path, grade: str) -> Floats:
    """Rows of x, y, grade from a CSV (x/east, y/north columns and the grade column)."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        raise GradeControlError(f"{path.name} is empty")
    header = [h.strip().lower() for h in rows[0]]
    names = {
        "x": ("x", "east", "easting", "dogu", "doğu"),
        "y": ("y", "north", "northing", "kuzey"),
    }
    try:
        cx = next(i for i, h in enumerate(header) if h in names["x"])
        cy = next(i for i, h in enumerate(header) if h in names["y"])
        cg = header.index(grade.strip().lower())
    except (StopIteration, ValueError):
        raise GradeControlError(
            f"{path.name} needs x, y and {grade!r} columns (found: {', '.join(header)})"
        ) from None
    out = []
    for row in rows[1:]:
        try:
            out.append([float(row[c].replace(",", ".")) for c in (cx, cy, cg)])
        except (ValueError, IndexError):
            continue
    if len(out) < 3:
        raise GradeControlError(f"{path.name}: fewer than three holes with a grade")
    return np.asarray(out, np.float64)


@dataclass(frozen=True)
class ClassRow:
    cls: int  # 0 = waste
    area: float  # m2 dug as this class
    tonnes: float
    grade: float  # mean estimated grade of what is dug as this class
    ideal_tonnes: float  # tonnes of this class cell by cell (before smoothing)
    polygons: int


@dataclass(frozen=True)
class GradeControl:
    x: Floats  # raster column centres
    y: Floats  # raster row centres
    grade: Floats  # ny x nx estimate (NaN outside the outline)
    ideal: Ints  # ny x nx class before smoothing (-1 outside)
    dug: Ints  # ny x nx class after smoothing (-1 outside)
    cutoffs: tuple[float, ...]
    toe: float
    rows: tuple[ClassRow, ...]
    ore_loss_t: float  # ideal ore (class >= 1) dug as waste
    dilution_t: float  # ideal waste dug as ore
    misrouted_t: float  # ore dug into another ore class
    lines: tuple[tuple[int, Floats], ...]  # (class, closed polyline n x 3)


def grade_control(  # noqa: PLR0913 - one study, keyword options
    outline: Floats,
    holes: Floats,
    cutoffs: tuple[float, ...],
    *,
    bench_height: float = 10.0,
    density: float = 2.7,
    cell: float = 1.0,
    power: float = 2.0,
    radius: float = 15.0,
    max_samples: int = 8,
    smu: float = 5.0,
    passes: int = 4,
) -> GradeControl:
    """Dig lines for one blast (see the module text). ``outline`` is n x 3 (closed)."""
    from matplotlib.path import Path as MplPath  # noqa: PLC0415

    ring = np.asarray(outline, np.float64)
    if len(ring) < 3:
        raise GradeControlError("the blast outline needs at least three points")
    if not cutoffs or any(b <= a for a, b in itertools.pairwise(cutoffs)):
        raise GradeControlError("cut-offs must be given in ascending order")
    for name, v in (("bench height", bench_height), ("density", density), ("cell", cell)):
        if not (math.isfinite(v) and v > 0):
            raise GradeControlError(f"{name} must be > 0")
    if radius <= 0 or max_samples < 1 or power <= 0:
        raise GradeControlError("search radius, sample count and power must be > 0")
    lo, hi = ring[:, :2].min(axis=0), ring[:, :2].max(axis=0)
    x = np.arange(lo[0] + cell / 2, hi[0], cell)
    y = np.arange(lo[1] + cell / 2, hi[1], cell)
    if len(x) * len(y) > 9_000_000:
        raise GradeControlError("too many raster cells; use a larger cell")
    xx, yy = np.meshgrid(x, y)
    pts = np.column_stack([xx.ravel(), yy.ravel()])
    inside = MplPath(ring[:, :2]).contains_points(pts).reshape(xx.shape)
    est = np.full(xx.shape, np.nan)
    h = np.asarray(holes, np.float64)
    h = h[np.isfinite(h).all(axis=1)]
    if len(h) == 0:
        raise GradeControlError("no blasthole has a grade")
    tree = cKDTree(h[:, :2])
    k = min(max_samples, len(h))
    d, idx = tree.query(pts[inside.ravel()], k=k, distance_upper_bound=radius)
    d, idx = d.reshape(-1, k), idx.reshape(-1, k)
    valid = np.isfinite(d)
    w = np.where(valid, 1.0 / np.maximum(d, 1e-3) ** power, 0.0)
    g = np.where(valid, h[np.minimum(idx, len(h) - 1), 2], 0.0)
    wsum = w.sum(axis=1)
    est[inside] = np.where(wsum > 0, (w * g).sum(axis=1) / np.where(wsum > 0, wsum, 1.0), np.nan)
    # Cells without a hole in the search radius are unestimated: treated as waste.
    ideal = np.where(inside, np.searchsorted(np.asarray(cutoffs), np.nan_to_num(est, nan=-np.inf),
                                             side="right"), -1).astype(np.int64)  # fmt: skip
    dug = _smooth(ideal, len(cutoffs) + 1, max(round(smu / cell), 1), passes)
    t_cell = cell * cell * bench_height * density
    toe = float(ring[:, 2].mean())
    lines = _dig_lines(x, y, dug, len(cutoffs) + 1, toe)
    rows = []
    for c in range(len(cutoffs) + 1):
        sel = dug == c
        known = sel & np.isfinite(est)
        rows.append(
            ClassRow(
                c,
                float(sel.sum() * cell * cell),
                float(sel.sum() * t_cell),
                float(est[known].mean()) if known.any() else math.nan,
                float((ideal == c).sum() * t_cell),
                sum(1 for cls, _ in lines if cls == c),
            )
        )
    ore_ideal, ore_dug = ideal >= 1, dug >= 1
    return GradeControl(
        x,
        y,
        est,
        ideal,
        dug,
        tuple(cutoffs),
        toe,
        tuple(rows),
        float((ore_ideal & (dug == 0)).sum() * t_cell),
        float((ore_dug & (ideal == 0)).sum() * t_cell),
        float((ore_ideal & ore_dug & (ideal != dug)).sum() * t_cell),
        tuple(lines),
    )


def _smooth(classes: Ints, n: int, window: int, passes: int) -> Ints:
    """Majority filter of the class map inside the outline (outside stays -1)."""
    inside = classes >= 0
    out = classes.copy()
    if window <= 1:
        return out
    for _ in range(max(passes, 1)):
        votes = np.stack(
            [uniform_filter((out == c).astype(np.float64), size=window, mode="constant")
             for c in range(n)]
        )  # fmt: skip
        new = np.where(inside, np.argmax(votes, axis=0), -1).astype(np.int64)
        if np.array_equal(new, out):
            break
        out = new
    return out


def _dig_lines(x: Floats, y: Floats, dug: Ints, n: int, toe: float) -> list[tuple[int, Floats]]:
    from contourpy import contour_generator  # noqa: PLC0415

    # Pad by one cell so regions touching the raster edge give closed loops.
    dx, dy = x[1] - x[0] if len(x) > 1 else 1.0, y[1] - y[0] if len(y) > 1 else 1.0
    px = np.concatenate([[x[0] - dx], x, [x[-1] + dx]])
    py = np.concatenate([[y[0] - dy], y, [y[-1] + dy]])
    out = []
    for c in range(n):
        mask = np.zeros((len(py), len(px)))
        mask[1:-1, 1:-1] = dug == c
        if not mask.any():
            continue
        for seg in contour_generator(px, py, mask).lines(0.5):
            pts = np.asarray(seg, np.float64)
            if len(pts) >= 4:
                out.append((c, np.column_stack([pts, np.full(len(pts), toe)])))
    return out
