"""Solid verification, repair and voxel booleans on unit cubes with known volumes."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.solids import Solid
from minecore.solidtools import Boolean, boolean, cut_by_surface, repair, verify
from minecore.surfaces import Surface

FACES = np.array(
    [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
     [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
)  # fmt: skip


def cube(origin: tuple[float, float, float], size: float) -> Solid:
    o = np.asarray(origin, float)
    corners = np.array(
        [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        float,
    )
    return Solid(o + size * corners, FACES.copy())


def test_verify_and_repair_a_flipped_cube() -> None:
    good = cube((0, 0, 0), 10)
    assert verify(good).valid
    assert verify(good).volume == pytest.approx(1000.0)
    bad = Solid(good.vertices, np.vstack([good.triangles[:5], good.triangles[5:][:, ::-1]]))
    report = verify(bad)
    assert not report.valid
    assert report.flipped_edges > 0
    fixed = repair(bad)
    assert verify(fixed).valid
    assert verify(fixed).volume == pytest.approx(1000.0)


def test_voxel_booleans_match_the_overlap() -> None:
    a, b = cube((0, 0, 0), 10), cube((5, 0, 0), 10)  # overlap 5 x 10 x 10 = 500
    union = boolean(a, b, Boolean.UNION, 0.5)
    inter = boolean(a, b, Boolean.INTERSECTION, 0.5)
    diff = boolean(a, b, Boolean.DIFFERENCE, 0.5)
    assert union.volume() == pytest.approx(1500.0, rel=0.08)
    assert inter.volume() == pytest.approx(500.0, rel=0.1)
    assert diff.volume() == pytest.approx(500.0, rel=0.1)
    assert union.open_edges() == 0


def test_cut_by_a_flat_surface() -> None:
    s = cube((0, 0, 0), 10)
    flat = Surface(np.array([[-5, -5, 4.0], [20, -5, 4.0], [-5, 20, 4.0], [20, 20, 4.0]]))
    below = cut_by_surface(s, flat, True, 0.5)
    assert below.volume() == pytest.approx(400.0, rel=0.1)
