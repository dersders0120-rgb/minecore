"""Sample statistics and top-cut effect."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.estimation.statistics import probability_points, summary, top_cut_effect


def test_summary_and_top_cut_by_hand() -> None:
    v = np.array([1.0, 2.0, 3.0, 4.0, 90.0, np.nan])
    s = summary(v)
    assert s.n == 5
    assert s.mean == pytest.approx(20.0)
    assert s.median == 3.0
    e = top_cut_effect(v, 10.0)
    assert e.capped == 1
    assert e.mean_after == pytest.approx(4.0)
    assert e.metal_removed == pytest.approx(0.8)


def test_lognormal_is_straight_on_log_probability() -> None:
    rng = np.random.default_rng(0)
    v = rng.lognormal(0.0, 1.0, 20000)
    x, q = probability_points(v)
    slope, intercept = np.polyfit(q, np.log(x), 1)
    assert slope == pytest.approx(1.0, abs=0.03)
    assert intercept == pytest.approx(0.0, abs=0.03)


def test_declustering_removes_cluster_bias() -> None:
    from minecore.estimation.statistics import cell_decluster  # noqa: PLC0415

    # A regular 10 x 10 grid (value 1) plus a tight cluster of 100 samples of value 5
    # inside one 10 m cell: the naive mean is 3.02, the declustered mean (99 + 5) / 100.
    gx, gy = np.meshgrid(np.arange(5.0, 100, 10), np.arange(5.0, 100, 10))
    grid = np.column_stack([gx.ravel(), gy.ravel(), np.zeros(100)])
    rng = np.random.default_rng(0)
    cluster = np.column_stack([rng.uniform(56, 64, 100), rng.uniform(56, 64, 100), np.zeros(100)])
    xyz = np.vstack([grid, cluster])
    values = np.r_[np.ones(100), np.full(100, 5.0)]
    values[55] = 5.0  # the grid sample in the cluster cell is high too
    d = cell_decluster(xyz, values, np.array([1.0, 5.0, 10.0, 20.0]), offsets=1)
    assert np.mean(values) == pytest.approx(3.02)
    assert d.best_size == 10.0
    assert d.means[2] == pytest.approx(1.04)
    assert d.weights.sum() == pytest.approx(200.0)
