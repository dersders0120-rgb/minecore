"""Variograms, kriging, block models and grade-tonnage against analytic results."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.estimation import (
    Ellipsoid,
    EstimationError,
    Method,
    ModelType,
    Search,
    Structure,
    VariogramModel,
    estimate,
    experimental,
    fit_spherical,
)
from minecore.project import Project


def sph(nugget: float = 0.1, sill: float = 0.9, a: float = 50.0) -> VariogramModel:
    return VariogramModel(nugget, (Structure(ModelType.SPHERICAL, sill, Ellipsoid.isotropic(a)),))


def test_model_shapes_at_known_points() -> None:
    v = sph()
    lags = np.array([[0, 0, 0], [25, 0, 0], [50, 0, 0], [80, 0, 0]], float)
    expected = [0.0, 0.1 + 0.9 * (0.75 - 0.0625), 1.0, 1.0]
    np.testing.assert_allclose(v.gamma(lags), expected)
    exp = VariogramModel(0.0, (Structure(ModelType.EXPONENTIAL, 1.0, Ellipsoid.isotropic(30.0)),))
    assert exp.gamma(np.array([[30.0, 0, 0]]))[0] == pytest.approx(1 - math.exp(-3))
    gau = VariogramModel(0.0, (Structure(ModelType.GAUSSIAN, 2.0, Ellipsoid.isotropic(10.0)),))
    assert gau.gamma(np.array([[0, 5.0, 0]]))[0] == pytest.approx(2 * (1 - math.exp(-0.75)))
    assert v.covariance(np.zeros((1, 3)))[0] == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("az", "dip", "rake"), [(0, 0, 0), (30, 0, 0), (120, 40, 25), (300, -15, 70)]
)
def test_ellipsoid_axes_are_orthonormal_and_right_handed(
    az: float, dip: float, rake: float
) -> None:
    axes = Ellipsoid(100, 50, 10, az, dip, rake).axes()
    np.testing.assert_allclose(axes @ axes.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(axes) == pytest.approx(1.0)


def test_anisotropy_reduces_along_each_axis() -> None:
    e = Ellipsoid(100, 50, 10, azimuth=45, dip=30)
    axes = e.axes()
    reduced = e.reduce(np.vstack([axes[0] * 100, axes[1] * 50, axes[2] * 10, axes[0] * 50]))
    np.testing.assert_allclose(reduced, [1, 1, 1, 0.5])
    # Major axis at azimuth 45, dipping 30 degrees down: it points north-east and down.
    assert axes[0][0] > 0 and axes[0][1] > 0 and axes[0][2] < 0  # noqa: PT018


def test_experimental_variogram_and_fit_recover_a_known_model() -> None:
    rng = np.random.default_rng(1)
    # 1D-ish transect with a deterministic linear trend: gamma(h) = 0.5 * (slope*h)^2.
    x = np.arange(0, 200, 2.0)
    xyz = np.column_stack([x, np.zeros_like(x), np.zeros_like(x)])
    values = 0.1 * x
    ev = experimental(xyz, values, lag=10.0, nlags=5)
    d = np.abs(x[:, None] - x[None, :])[np.triu_indices(len(x), 1)]
    k = np.floor(d / 10.0 + 0.5)
    for c in range(1, 6):
        sel = k == c
        assert ev.pairs[c - 1] == sel.sum()
        assert ev.gamma[c - 1] == pytest.approx(0.5 * 0.01 * (d[sel] ** 2).mean())
    # Fit on noise-free spherical "experimental" values.
    h = np.linspace(5, 100, 20)
    model = sph(0.2, 1.0, 60.0)
    fake = type(ev)(h, model.gamma(np.column_stack([h, 0 * h, 0 * h])), np.full(20, 100), h)
    fitted = fit_spherical(fake)
    assert fitted.nugget == pytest.approx(0.2, abs=0.03)
    assert fitted.structures[0].ranges.major == pytest.approx(60.0, rel=0.05)
    assert rng is not None


def grid_samples() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    xyz = rng.uniform(0, 100, (60, 3))
    values = np.sin(xyz[:, 0] / 20) + xyz[:, 1] / 50
    return xyz, values


def test_kriging_is_exact_at_samples_and_weights_sum_to_one() -> None:
    xyz, values = grid_samples()
    search = Search(Ellipsoid.isotropic(500), min_samples=1, max_samples=60)
    v = sph(0.0, 1.0, 80.0)
    result = estimate(xyz, values, xyz[:5], search, Method.ORDINARY_KRIGING, variogram=v)
    np.testing.assert_allclose(result.value, values[:5], atol=1e-8)
    np.testing.assert_allclose(result.variance, 0.0, atol=1e-8)
    # Unbiasedness: a constant field is reproduced everywhere.
    const = estimate(
        xyz,
        np.full(60, 3.5),
        np.array([[50.0, 50, 50]]),
        search,
        Method.ORDINARY_KRIGING,
        variogram=v,
    )
    assert const.value[0] == pytest.approx(3.5)
    assert const.variance[0] > 0


def test_kriging_two_symmetric_samples_by_hand() -> None:
    # Target midway between two samples: weights 1/2 each by symmetry.
    xyz = np.array([[0.0, 0, 0], [10.0, 0, 0]])
    v = sph(0.0, 1.0, 20.0)
    search = Search(Ellipsoid.isotropic(100), min_samples=1, max_samples=2)
    r = estimate(
        xyz,
        np.array([1.0, 3.0]),
        np.array([[5.0, 0, 0]]),
        search,
        Method.ORDINARY_KRIGING,
        variogram=v,
    )
    assert r.value[0] == pytest.approx(2.0)

    def c(h: float) -> float:
        return 1 - (1.5 * h / 20 - 0.5 * (h / 20) ** 3)

    # sigma^2 = C0 - sum w c_i - mu ; with mu from the first equation w1 C11 + w2 C12 + mu = c1.
    mu = c(5) - 0.5 * (1.0 + c(10))
    assert r.variance[0] == pytest.approx(1.0 - c(5) - mu)


def test_idw_and_nearest() -> None:
    xyz = np.array([[0.0, 0, 0], [10.0, 0, 0], [0, 30.0, 0]])
    vals = np.array([1.0, 2.0, 9.0])
    search = Search(Ellipsoid.isotropic(100), min_samples=1, max_samples=3)
    t = np.array([[2.0, 0, 0]])
    nn = estimate(xyz, vals, t, search, Method.NEAREST)
    assert nn.value[0] == 1.0
    idw = estimate(xyz, vals, t, search, Method.INVERSE_DISTANCE, power=2)
    d = np.array([2, 8, math.hypot(2, 30)]) / 100
    w = 1 / d**2
    assert idw.value[0] == pytest.approx(float((w * vals).sum() / w.sum()))


def test_search_limits() -> None:
    xyz = np.array([[0.0, 0, 0], [10.0, 0, 0], [0, 30.0, 0]])
    vals = np.array([1.0, 2.0, 9.0])
    narrow = Search(Ellipsoid(20, 5, 5, azimuth=90), min_samples=2, max_samples=5)
    r = estimate(xyz, vals, np.array([[1.0, 0, 0]]), narrow, Method.NEAREST)
    assert r.samples_used[0] == 2  # the sample 30 m north is outside the east-west ellipsoid
    strict = Search(Ellipsoid.isotropic(5), min_samples=2)
    r = estimate(xyz, vals, np.array([[1.0, 0, 0]]), strict, Method.NEAREST)
    assert r.unestimated == 1
    per_hole = Search(Ellipsoid.isotropic(100), min_samples=1, max_samples=3, max_per_hole=1)
    r = estimate(
        xyz, vals, np.array([[1.0, 0, 0]]), per_hole, Method.NEAREST, hole_ids=np.array([0, 0, 1])
    )
    assert r.samples_used[0] == 2
    with pytest.raises(EstimationError):
        estimate(xyz, vals, xyz, per_hole, Method.ORDINARY_KRIGING)


def test_block_model_and_grade_tonnage(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "B.mcproj", "B")
    grid = Grid.covering(np.array([[0.0, 0, 0], [19.0, 9.0, 4.0]]), (5.0, 5.0, 5.0))
    assert grid.count == (4, 2, 1)
    model = BlockModel.create(project, "bm", grid)
    np.testing.assert_allclose(model.grid.centroids()[:2], [[2.5, 2.5, 2.5], [7.5, 2.5, 2.5]])
    grade = np.array([0.5, 1.0, 1.5, 2.0, np.nan, 3.0, 0.2, 1.0])
    model.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "test grades")
    gt = model.grade_tonnage("au", [0.0, 1.0, 2.5], density=2.5)
    tonnes_block = 125 * 2.5
    first = gt.rows[0]
    assert first.blocks == 7
    assert first.tonnes == pytest.approx(7 * tonnes_block)
    assert first.grade == pytest.approx(np.nansum(grade) / 7)
    assert first.metal == pytest.approx(first.tonnes * first.grade)  # g/t * t = g
    assert gt.metal_unit == "g"
    assert [r.blocks for r in gt.rows] == [7, 5, 1]
    assert gt.unestimated_blocks == 1
    project.undo()
    assert "au" not in BlockModel.open(project, "bm").attributes
