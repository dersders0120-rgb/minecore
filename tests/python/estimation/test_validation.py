"""Cross-validation and swath plots on cases with known answers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.blockmodel.validation import cross_validate, swath
from minecore.blockmodel.workflow import SampleSet
from minecore.estimation import Ellipsoid, Method, Search
from minecore.project import Project


def _holes() -> SampleSet:
    # Five vertical holes along x at 0, 10, 30, 60, 100 m; each hole has a constant value.
    xs = [0.0, 10.0, 30.0, 60.0, 100.0]
    xyz, val, hole = [], [], []
    for h, x in enumerate(xs):
        for z in range(5):
            xyz.append([x, 0.0, -float(z)])
            val.append(float(h * 10))
            hole.append(h)
    return SampleSet(np.array(xyz), np.array(val), np.array(hole, np.int64), "g/t")


def test_leave_one_hole_out_nearest_neighbour() -> None:
    s = _holes()
    cv = cross_validate(s, method=Method.NEAREST, search=Search(Ellipsoid.isotropic(200.0), 1, 4))
    # Nearest other hole: 0->10 m (h1), 1->0 m (h0), 2->10 m (h1), 3->30 m (h2), 4->60 m (h3).
    expected_hole = [1, 0, 1, 2, 3]
    np.testing.assert_allclose(cv.estimate, np.repeat(np.array(expected_hole) * 10.0, 5))
    assert cv.n == 25
    assert cv.mean_error == pytest.approx(np.mean(cv.estimate - cv.true))


def test_cross_validation_reports_unestimated() -> None:
    s = _holes()
    cv = cross_validate(
        s, method=Method.INVERSE_DISTANCE, search=Search(Ellipsoid.isotropic(15.0), 1, 4)
    )
    # Holes 2, 3 and 4 have no other hole within 15 m.
    assert cv.unestimated == 15


def test_swath_means(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "s.mcproj", "s")
    grid = Grid((0.0, -5.0, -5.0), (10.0, 10.0, 5.0), (11, 1, 1))
    model = BlockModel.create(project, "bm", grid)
    x = grid.centroids()[:, 0]
    model.set_attributes({"au": x / 10.0}, {"au": {"unit": "g/t"}}, "g")
    rows = swath(_holes(), model, "au", axis=0, width=20.0)
    first = rows[0]  # slice [0, 20): blocks at 5 and 15, holes at 0 and 10
    assert first.centre == pytest.approx(10.0)
    assert first.blocks == 2
    assert first.block_mean == pytest.approx(1.0)
    assert first.samples == 10
    assert first.sample_mean == pytest.approx(5.0)
    project.close()
