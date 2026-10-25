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
