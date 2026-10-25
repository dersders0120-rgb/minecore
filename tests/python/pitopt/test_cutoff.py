"""Lane cut-off optimisation against closed-form cut-offs and brute force."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from minecore.pitopt.cutoff import LaneInputs, constant_cutoff_npv, optimise_cutoffs
from minecore.pitopt.economics import PitOptError

GRADES = np.linspace(0.05, 9.95, 100)
TONNES = np.full(100, 1e5)  # 10 Mt, uniform grade distribution 0..10


def _inputs(**kw: float) -> LaneInputs:
    base = {
        "price": 10.0,
        "selling": 0.0,
        "recovery": 1.0,
        "mining": 1.0,
        "processing": 20.0,
        "fixed": 0.0,
        "mine_capacity": 2e6,
        "mill_capacity": math.inf,
        "discount": 0.0,
    }
    base.update(kw)
    return LaneInputs(**base)


def test_mine_limited_is_breakeven() -> None:
    # No time cost: the cut-off is h / ((p - s) y) = 2.0 every year.
    result = optimise_cutoffs(_inputs(), GRADES, TONNES)
    assert all(y.cutoff == pytest.approx(2.05) for y in result.years)
    assert all(y.limit == "mine" for y in result.years)
    assert sum(y.material_t for y in result.years) == pytest.approx(1e7)
    ore = GRADES >= 2.05
    expected = (10 * (GRADES[ore] * 1e5).sum()) - 20 * ore.sum() * 1e5 - 1e7
    assert result.npv == pytest.approx(expected)


def test_mill_limited_balances_opportunity_cost() -> None:
    inp = _inputs(mine_capacity=math.inf, mill_capacity=1e6, fixed=5e6, discount=0.1)
    result = optimise_cutoffs(inp, GRADES, TONNES)
    assert result.iterations < 100
    for y in result.years:
        assert y.limit == "mill"
        # Lane's mill-limited cut-off g_h = (h + (f + dV) / H) / ((p - s) y).
        g_h = (20.0 + (5e6 + 0.1 * y.npv_remaining) / 1e6) / 10.0
        assert y.cutoff == GRADES[g_h <= GRADES].min()
    cut = [y.cutoff for y in result.years]
    assert cut == sorted(cut, reverse=True)  # cut-offs fall as the reserve depletes
    assert cut[0] > cut[-1]


@settings(max_examples=20, deadline=None)
@given(
    st.floats(1e6, 5e6),
    st.floats(2e5, 2e6),
    st.floats(0.0, 3e6),
    st.floats(0.0, 0.2),
)
def test_lane_beats_every_constant_cutoff(mine: float, mill: float, fixed: float, d: float) -> None:
    inp = _inputs(mine_capacity=mine, mill_capacity=mill, fixed=fixed, discount=d)
    lane = optimise_cutoffs(inp, GRADES, TONNES).npv
    best = max(constant_cutoff_npv(inp, GRADES, TONNES, c) for c in np.r_[GRADES, 99.0])
    assert lane >= best - 1e-6 * abs(best) - 1.0


def test_market_limit_and_validation() -> None:
    inp = _inputs(market_capacity=5e6, mine_capacity=1e7)
    result = optimise_cutoffs(inp, GRADES, TONNES)
    assert result.years[0].metal <= 5e6 * (1 + 1e-9)
    assert result.years[0].limit == "market"
    with pytest.raises(PitOptError):
        _inputs(mill_capacity=0.0)
    with pytest.raises(PitOptError):
        optimise_cutoffs(_inputs(), GRADES, np.zeros(100))
