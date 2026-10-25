"""Floating stope envelope and greedy stope selection."""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from minecore.blockmodel import Grid
from minecore.underground.stopes import StopeError, _window_sums, stope_boxes, stope_envelope


@settings(max_examples=30, deadline=None)
@given(st.integers(0, 10_000), st.integers(1, 3), st.integers(1, 3), st.integers(1, 3))
def test_window_sums_match_brute_force(seed: int, a: int, b: int, c: int) -> None:
    cube = np.random.default_rng(seed).uniform(0, 1, (4, 5, 6))
    out = _window_sums(cube, (a, b, c))
    for k, j, i in itertools.product(*(range(n) for n in out.shape)):
        assert out[k, j, i] == pytest.approx(cube[k : k + a, j : j + b, i : i + c].sum())


def test_vein_envelope_and_stopes_by_hand() -> None:
    grid = Grid((0.0, 0.0, 0.0), (5.0, 5.0, 5.0), (10, 4, 4))
    nx, ny, nz = grid.count
    grade = np.zeros((nz, ny, nx))
    grade[:, 1, 2:8] = 6.0  # a one-block-thick vein, 6 blocks long
    tonnes = np.full(grid.blocks, 100.0)
    # Stope 2 x 2 x 2 blocks: across the vein the mean is 3 (half vein, half waste).
    r = stope_envelope(grid, grade.ravel(), tonnes, (2, 2, 2), 3.0)
    env = r.envelope.reshape(nz, ny, nx)
    # Positions on either side of the vein qualify: the envelope is 3 rows wide.
    assert env[:, 0:3, 2:8].all()
    assert not env[:, 3, :].any()
    assert not env[:, :, :2].any()
    assert r.envelope_grade == pytest.approx(2.0)
    # Non-overlapping stopes: 3 along strike x 2 levels.
    assert len(r.stopes) == 6
    assert all(s.grade == pytest.approx(3.0) for s in r.stopes)
    assert r.stope_mask.sum() == 6 * 8
    boxes = stope_boxes(grid, r)
    assert boxes.shape == (6, 2, 3)
    assert np.allclose(boxes[:, 1] - boxes[:, 0], [10.0, 10.0, 10.0])
    # Higher cut-off: no stope reaches it.
    assert len(stope_envelope(grid, grade.ravel(), tonnes, (2, 2, 2), 3.5).stopes) == 0
    with pytest.raises(StopeError):
        stope_envelope(grid, grade.ravel(), tonnes, (11, 1, 1), 1.0)


def test_air_blocks_reject_stopes() -> None:
    grid = Grid((0.0, 0.0, 0.0), (5.0, 5.0, 5.0), (4, 1, 2))
    grade = np.full(grid.blocks, 5.0)
    rock = np.ones(grid.blocks, bool)
    rock[4:] = False  # top level is air
    r = stope_envelope(grid, grade, np.full(grid.blocks, 1.0), (1, 1, 2), 1.0, rock=rock)
    assert len(r.stopes) == 0
    r1 = stope_envelope(grid, grade, np.full(grid.blocks, 1.0), (2, 1, 1), 1.0, rock=rock)
    assert len(r1.stopes) == 2
    assert not r1.envelope[4:].any()


def test_box_triangles_are_a_closed_box() -> None:
    from minecore.solids import Solid  # noqa: PLC0415
    from minecore.underground.stopes import box_triangles  # noqa: PLC0415

    tris = box_triangles(np.array([[[0.0, 0.0, 0.0], [2.0, 3.0, 4.0]]]))
    pts = tris.reshape(-1, 3)
    verts, inv = np.unique(pts, axis=0, return_inverse=True)
    solid = Solid(verts, inv.reshape(-1, 3).astype(np.int64))
    assert solid.open_edges() == 0
    assert solid.volume() == pytest.approx(24.0)
