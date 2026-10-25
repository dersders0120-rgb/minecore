"""Bench design around a block pit."""

from __future__ import annotations

import math

import numpy as np
import pytest

from minecore.blockmodel import Grid
from minecore.pitopt.benchdesign import BenchGeometry, design_pit
from minecore.pitopt.economics import PitOptError


def test_stair_profile_by_hand() -> None:
    g = BenchGeometry(face_angle=45.0, bench_height=10.0, berm=5.0)
    # Face run 10 m, cycle 15 m.
    d = np.array([0.0, 5.0, 10.0, 12.0, 15.0, 20.0, 30.0])
    assert g.rise(d).tolist() == pytest.approx([0.0, 5.0, 10.0, 10.0, 10.0, 15.0, 20.0])
    assert g.overall_angle == pytest.approx(math.degrees(math.atan2(10, 15)))
    with pytest.raises(PitOptError):
        BenchGeometry(95.0, 10.0, 5.0)


def test_single_column_cone() -> None:
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (21, 21, 5))
    mined = np.zeros((5, 21, 21), bool)
    mined[2:, 10, 10] = True  # column floor at z = 20, model top at 50
    g = BenchGeometry(60.0, 10.0, 4.0)
    pit = design_pit(grid, mined.ravel(), g, resolution=1.0, margin=0.0)
    assert not pit.reaches_edge
    j = int(np.argmin(np.abs(pit.y - 105.0)))
    for dx in (0.0, 3.0, 8.0, 20.0, 40.0):
        i = int(np.argmin(np.abs(pit.x - (105.0 + 5.0 + dx))))  # east of the column edge
        d = max(pit.x[i] - 110.0, 0.0)
        expect = min(20.0 + float(g.rise(np.array([d]))[0]), 50.0)
        assert pit.z[j, i] == pytest.approx(expect, abs=1.0)
    # The design contains the column and is never deeper than its floor.
    assert pit.z.min() == pytest.approx(20.0)
    centres = np.array([[105.0, 105.0, 25.0], [105.0, 105.0, 45.0], [105.0, 105.0, 15.0]])
    assert pit.inside(centres).tolist() == [True, True, False]
    assert len(pit.strings()) > 0
    assert pit.volume(outside_model_only=True) == 0.0


def test_design_contains_every_mined_block() -> None:
    rng = np.random.default_rng(3)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (16, 14, 6))
    nx, ny, nz = grid.count
    depth = rng.integers(0, 4, size=(ny, nx))
    mined = np.zeros((nz, ny, nx), bool)
    for j in range(ny):
        for i in range(nx):
            if depth[j, i]:
                mined[nz - depth[j, i] :, j, i] = True
    pit = design_pit(grid, mined.ravel(), BenchGeometry(70.0, 10.0, 3.0))
    c = grid.centroids()[mined.ravel()]
    assert pit.inside(c).all()
    assert pit.volume() >= mined.sum() * grid.block_volume * 0.99
