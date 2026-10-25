"""Minimum mining width for pushbacks.

On every bench the increment of a pushback (its blocks on that bench) must
be wide enough for the equipment. Parts narrower than the minimum width are
found by a morphological opening with a ``w x w`` block square (scipy
``binary_opening``): cells of the increment not covered by any full square
placed inside it are slivers.

Slivers of pushback ``p > 1`` are merged into pushback ``p - 1`` (mined
earlier); slivers of pushback 1 are deferred to pushback 2. Either move can
break slope precedence, so phases are then propagated along the precedence
arcs until consistent: blocks a block depends on are never in a later phase
(``phase[required] <= phase[block]``). The final pit (the set of mined
blocks) never changes. Propagation can create new slivers, so the passes are
repeated until nothing moves or ``max_rounds`` is reached; the result reports
the slivers that remain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.ndimage import binary_opening

from minecore.blockmodel import Grid
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.precedence import SlopeProfile, precedence_arcs

Ints = npt.NDArray[np.int64]
Arcs = npt.NDArray[np.uint32]


@dataclass(frozen=True)
class WidthResult:
    phase: Ints
    moved: int  # blocks whose phase changed
    remaining_slivers: int  # sliver blocks left after the last round
    rounds: int


def _structure(grid: Grid, width: float) -> npt.NDArray[np.bool_]:
    wx = max(1, math.ceil(width / grid.size[0] - 1e-9))
    wy = max(1, math.ceil(width / grid.size[1] - 1e-9))
    return np.ones((wy, wx), bool)


def slivers(grid: Grid, phase: Ints, width: float) -> npt.NDArray[np.bool_]:
    """Blocks in a part of their pushback's bench increment narrower than ``width``."""
    nx, ny, nz = grid.count
    cube = np.asarray(phase).reshape(nz, ny, nx)
    square = _structure(grid, width)
    out = np.zeros(cube.shape, bool)
    for k in range(nz):
        for p in np.unique(cube[k][cube[k] > 0]).tolist():
            region = cube[k] == p
            out[k] |= region & ~binary_opening(region, structure=square)
    return out.ravel()


def _propagate_down(phase: Ints, arcs: Arcs) -> None:
    """Pull required blocks forward: phase[req] = min(phase[req], phase[block])."""
    blk, req = arcs[:, 0].astype(np.int64), arcs[:, 1].astype(np.int64)
    live = (phase[blk] > 0) & (phase[req] > 0)
    blk, req = blk[live], req[live]
    while True:
        before = phase[req].copy()
        np.minimum.at(phase, req, phase[blk])
        if np.array_equal(before, phase[req]):
            return


def _propagate_up(phase: Ints, arcs: Arcs) -> None:
    """Push dependent blocks back: phase[block] = max(phase[block], phase[req])."""
    blk, req = arcs[:, 0].astype(np.int64), arcs[:, 1].astype(np.int64)
    live = (phase[blk] > 0) & (phase[req] > 0)
    blk, req = blk[live], req[live]
    while True:
        before = phase[blk].copy()
        np.maximum.at(phase, blk, phase[req])
        if np.array_equal(before, phase[blk]):
            return


def enforce_mining_width(
    grid: Grid, phase: Ints, arcs: Arcs, width: float, max_rounds: int = 10
) -> WidthResult:
    """Rework pushbacks so every bench increment is at least ``width`` wide."""
    if width <= 0:
        raise PitOptError("minimum mining width must be > 0")
    start = np.asarray(phase, np.int64)
    if start.shape != (grid.blocks,):
        raise PitOptError("one phase per block expected")
    work = start.copy()
    rounds = 0
    for rounds in range(1, max_rounds + 1):  # noqa: B007 - reported
        thin = slivers(grid, work, width)
        if not thin.any() or work.max() < 2:
            break
        before = work.copy()
        first = thin & (work == 1)
        later = thin & (work > 1)
        work[later] -= 1
        _propagate_down(work, arcs)
        work[first & (work == 1)] = 2
        _propagate_up(work, arcs)
        if np.array_equal(before, work):
            break
    remaining = int(slivers(grid, work, width).sum())
    moved = int((work != start).sum())
    # Renumber phases 1..n without gaps (a phase can be absorbed completely).
    used = np.unique(work[work > 0])
    renum = np.zeros(int(work.max()) + 1, np.int64)
    renum[used] = np.arange(1, len(used) + 1)
    work = renum[work]
    return WidthResult(work, moved, remaining, rounds)


def apply_mining_width(grid: Grid, phase: Ints, slope: SlopeProfile, width: float) -> WidthResult:
    """``enforce_mining_width`` with the optimiser's default precedence pattern."""
    return enforce_mining_width(grid, phase, precedence_arcs(grid, slope), width)
