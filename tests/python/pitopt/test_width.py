"""Minimum mining width for pushbacks."""

from __future__ import annotations

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st

from minecore._core import max_closure
from minecore.blockmodel import Grid
from minecore.pitopt.precedence import SlopeProfile, precedence_arcs
from minecore.pitopt.width import enforce_mining_width, slivers


def _valid(phase: np.ndarray, arcs: np.ndarray) -> bool:
    blk, req = arcs[:, 0].astype(int), arcs[:, 1].astype(int)
    live = phase[blk] > 0
    return bool(np.all((phase[req[live]] > 0) & (phase[req[live]] <= phase[blk[live]])))


def test_thin_ring_merges_into_previous_pushback() -> None:
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (12, 12, 1))
    cube = np.zeros((1, 12, 12), np.int64)
    cube[0, 2:10, 2:10] = 2  # one-block ring of pushback 2 ...
    cube[0, 3:9, 3:9] = 1  # ... around a 6 x 6 pushback 1
    phase = cube.ravel()
    assert slivers(grid, phase, 30.0).sum() == 64 - 36
    arcs = precedence_arcs(grid, SlopeProfile(45.0))
    out = enforce_mining_width(grid, phase, arcs, 30.0)
    assert out.remaining_slivers == 0
    assert set(np.unique(out.phase)) == {0, 1}  # the ring joined pushback 1
    assert out.moved == 28
    # A wide ring survives.
    wide_cube = np.zeros((1, 12, 12), np.int64)
    wide_cube[0, :, :] = 2
    wide_cube[0, 4:8, 4:8] = 1
    wide = wide_cube.ravel()
    kept = enforce_mining_width(grid, wide, arcs, 30.0)
    assert kept.moved == 0
    assert np.array_equal(kept.phase, wide)


@settings(max_examples=25, deadline=None)
@given(st.integers(0, 2**31 - 1), st.floats(10.0, 40.0))
def test_precedence_and_pit_preserved(seed: int, width: float) -> None:
    rng = np.random.default_rng(seed)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (14, 12, 4))
    arcs = precedence_arcs(grid, SlopeProfile(45.0))
    # Nested pits from scaled random block values, as the optimiser makes them.
    base = rng.normal(-1.0, 3.0, grid.blocks)
    phase = np.zeros(grid.blocks, np.int64)
    for p, factor in ((3, 1.6), (2, 1.2), (1, 0.8)):
        values = np.round(np.where(base > 0, base * factor, base) * 1000).astype(np.int64)
        phase[np.asarray(max_closure(values, arcs), bool)] = p
    assert _valid(phase, arcs)
    out = enforce_mining_width(grid, phase, arcs, width)
    assert _valid(out.phase, arcs)
    assert np.array_equal(out.phase > 0, phase > 0)
