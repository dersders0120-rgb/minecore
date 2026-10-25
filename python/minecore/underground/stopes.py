"""Stope envelopes for underground mining on a regular block model.

* Floating stope (Alford 1995): a stope of fixed size (whole blocks in x, y,
  z) is moved over every position of the grid; every block covered by at
  least one position whose tonnage-weighted mean grade reaches the cut-off
  belongs to the envelope. Window sums use 3-D prefix sums (summed-area
  tables), so all positions cost O(blocks).
* Stope selection: candidate positions above the cut-off, sorted by metal
  content, are accepted greedily when they do not overlap an accepted stope.
  This gives a practical, non-overlapping stope list; it is not an optimal
  (MIP) selection.

Missing grades count as zero (waste). Air blocks can be excluded with a
rock mask (their tonnage is zero, a stope containing air is rejected).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel, Grid
from minecore.io.dxf import write_dxf

Floats = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]


class StopeError(MineCoreError):
    """Invalid stope input."""


@dataclass(frozen=True)
class Stope:
    i: int  # lowest block index in x, y, z
    j: int
    k: int
    tonnes: float
    grade: float

    @property
    def metal(self) -> float:
        return self.tonnes * self.grade


@dataclass(frozen=True)
class StopeResult:
    envelope: Mask  # per block
    envelope_tonnes: float
    envelope_grade: float
    stopes: tuple[Stope, ...]
    stope_mask: Mask  # per block, blocks inside accepted stopes
    size: tuple[int, int, int]


def _window_sums(cube: Floats, size: tuple[int, int, int]) -> Floats:
    """Sum of every ``size`` window (z, y, x order), shape ``n - size + 1`` per axis."""
    sz, sy, sx = size
    c = np.zeros((cube.shape[0] + 1, cube.shape[1] + 1, cube.shape[2] + 1))
    c[1:, 1:, 1:] = cube.cumsum(0).cumsum(1).cumsum(2)
    return np.asarray(
        c[sz:, sy:, sx:]
        - c[:-sz, sy:, sx:]
        - c[sz:, :-sy, sx:]
        - c[sz:, sy:, :-sx]
        + c[:-sz, :-sy, sx:]
        + c[:-sz, sy:, :-sx]
        + c[sz:, :-sy, :-sx]
        - c[:-sz, :-sy, :-sx]
    )


def stope_envelope(  # noqa: PLR0913 - one pass over the windows
    grid: Grid,
    grade: Floats,
    tonnes: Floats,
    size_blocks: tuple[int, int, int],
    cutoff: float,
    *,
    rock: Mask | None = None,
) -> StopeResult:
    nx, ny, nz = grid.count
    bx, by, bz = size_blocks
    if min(size_blocks) < 1 or bx > nx or by > ny or bz > nz:
        raise StopeError("stope size must be 1..model size blocks on each axis")
    g = np.where(np.isfinite(grade), np.maximum(grade, 0.0), 0.0).reshape(nz, ny, nx)
    t = np.asarray(tonnes, np.float64).reshape(nz, ny, nx)
    air = np.zeros((nz, ny, nx))
    if rock is not None:
        air = (~np.asarray(rock, bool)).reshape(nz, ny, nx).astype(np.float64)
        t = np.where(air > 0, 0.0, t)
    win = (bz, by, bx)
    t_sum = _window_sums(t, win)
    m_sum = _window_sums(t * g, win)
    air_sum = _window_sums(air, win)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(t_sum > 0, m_sum / t_sum, 0.0)
    good = (mean >= cutoff) & (t_sum > 0) & (air_sum == 0)
    # Envelope: dilate the qualifying window origins by the stope size.
    pad = ((bz - 1, bz - 1), (by - 1, by - 1), (bx - 1, bx - 1))
    covered = _window_sums(np.pad(good.astype(np.float64), pad), win)
    envelope = covered > 0
    env_t = float(t[envelope].sum())
    env_grade = float((t * g)[envelope].sum() / env_t) if env_t > 0 else float("nan")
    # Greedy non-overlapping selection by metal.
    kk, jj, ii = np.nonzero(good)
    metal = m_sum[kk, jj, ii]
    order = np.argsort(-metal, kind="stable")
    taken = np.zeros((nz, ny, nx), bool)
    stopes = []
    for o in order.tolist():
        k, j, i = int(kk[o]), int(jj[o]), int(ii[o])
        box = taken[k : k + bz, j : j + by, i : i + bx]
        if box.any():
            continue
        box[...] = True
        stopes.append(Stope(i, j, k, float(t_sum[k, j, i]), float(mean[k, j, i])))
    return StopeResult(
        envelope.ravel(),
        env_t,
        env_grade,
        tuple(stopes),
        taken.ravel(),
        (bx, by, bz),
    )


def stope_boxes(grid: Grid, result: StopeResult) -> Floats:
    """Corner coordinates of every accepted stope: ``m x 2 x 3`` (min, max)."""
    o = np.asarray(grid.origin)
    s = np.asarray(grid.size)
    size = np.asarray(result.size)
    lo = np.array([[st.i, st.j, st.k] for st in result.stopes], np.float64).reshape(-1, 3)
    return np.stack([o + lo * s, o + (lo + size) * s], axis=1)


def box_triangles(boxes: Floats) -> Floats:
    """12 triangles per axis-aligned box (``m x 2 x 3`` corners) for DXF/solids."""
    faces = (
        (0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3),
    )  # fmt: skip
    out = []
    for lo, hi in boxes:
        c = np.array(
            [
                [(hi if b & 1 else lo)[0], (hi if b & 2 else lo)[1], (hi if b & 4 else lo)[2]]
                for b in range(8)
            ]
        )
        for a, b, cc, d in faces:
            out.append([c[a], c[b], c[cc]])
            out.append([c[a], c[cc], c[d]])
    return np.asarray(out, np.float64).reshape(-1, 3, 3)


def stopes_from_model(  # noqa: PLR0913 - options are keyword-only
    model: BlockModel,
    grade_attribute: str,
    stope_size: tuple[float, float, float],
    cutoff: float,
    *,
    density: float | str = 2.7,
    rock_attribute: str | None = None,
    dxf: Path | None = None,
) -> StopeResult:
    """Run the envelope and selection on a model; stores ``ug_zarf`` and ``ug_pano``."""
    grid = model.grid
    blocks = tuple(max(1, round(s / d)) for s, d in zip(stope_size, grid.size, strict=True))
    if isinstance(density, str):
        rho = np.nan_to_num(model.values(density), nan=0.0)
    else:
        rho = np.full(grid.blocks, float(density))
    rock = model.values(rock_attribute) > 0.5 if rock_attribute else None
    result = stope_envelope(
        grid,
        model.values(grade_attribute),
        rho * grid.block_volume,
        (blocks[0], blocks[1], blocks[2]),
        cutoff,
        rock=rock,
    )
    model.set_attributes(
        {
            "ug_zarf": result.envelope.astype(np.float64),
            "ug_pano": result.stope_mask.astype(np.float64),
        },
        {
            "ug_zarf": {"unit": None, "cutoff": cutoff, "stope_blocks": list(blocks)},
            "ug_pano": {"unit": None, "cutoff": cutoff, "stope_blocks": list(blocks)},
        },
        "Stope envelope and stopes",
    )
    if dxf is not None:
        write_dxf(dxf, faces=[("STOPES", box_triangles(stope_boxes(grid, result)))])
    return result
