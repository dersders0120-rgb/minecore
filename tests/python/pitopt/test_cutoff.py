"""Lane cut-off optimisation against closed-form cut-offs and brute force."""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from minecore.pitopt.cutoff import LaneInputs, LaneResult, constant_cutoff_npv, optimise_cutoffs
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
# Mine-mill balance inside one grade class: needs the split class (balancing point).
@example(2525386.0, 1969849.0, 2524447.0, 0.001953125)
# Passes that cycle between two cut-offs without converging: the best pass is kept.
@example(2525296.0, 1969734.0, 2518555.0, 0.001953125)
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


def test_royalty_and_payables_lower_the_lane_net_price(tmp_path: object) -> None:
    from pathlib import Path  # noqa: PLC0415

    from minecore.blockmodel import BlockModel, Grid  # noqa: PLC0415
    from minecore.pitopt import Economics, PriceUnit  # noqa: PLC0415
    from minecore.pitopt.cutoff import cutoffs_from_model  # noqa: PLC0415
    from minecore.project import Project  # noqa: PLC0415

    project = Project.create(Path(str(tmp_path)) / "l.mcproj", "l")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (10, 10, 1))
    model = BlockModel.create(project, "m", grid)
    model.set_attributes(
        {"au": np.linspace(0.1, 5.0, grid.blocks), "pit_shell": np.ones(grid.blocks)},
        {"au": {"unit": "g/t"}, "pit_shell": {"unit": None}},
        "grades",
    )

    def run(econ: Economics) -> LaneResult:
        return cutoffs_from_model(
            model, "au", econ, mine_capacity=1e4, mill_capacity=5e3, discount=0.1
        )

    taxed = run(Economics(60.0, PriceUnit.GRAM, 0.9, 2.0, 15.0, 1.0, payable=0.95, royalty=0.1))
    share = 0.95 * 0.9
    same = run(Economics(60.0 * share, PriceUnit.GRAM, 0.9, 2.0, 15.0, share))
    assert taxed.npv == pytest.approx(same.npv)
    assert [y.cutoff for y in taxed.years] == pytest.approx([y.cutoff for y in same.years])
    project.close()


@pytest.mark.parametrize("d", [0.0, 0.1])
@pytest.mark.parametrize("mine", [2e6, math.inf])
def test_constant_policy_value_matches_the_year_by_year_schedule(d: float, mine: float) -> None:
    from minecore.pitopt.cutoff import _best_constant, _Reserve  # noqa: PLC0415

    inp = _inputs(mine_capacity=mine, mill_capacity=1.5e6, fixed=2e6, discount=d)
    k, npv = _best_constant(inp, _Reserve(GRADES, TONNES, 1.0))
    cuts = np.r_[np.inf, np.sort(GRADES)[::-1]]
    if k < len(cuts):  # a plain grade candidate: compare with the independent valuation
        assert npv == pytest.approx(constant_cutoff_npv(inp, GRADES, TONNES, cuts[k]), rel=1e-9)
    best = max(constant_cutoff_npv(inp, GRADES, TONNES, c) for c in np.r_[GRADES, 99.0])
    assert npv >= best * (1 - 1e-12)


def test_lane_falls_back_to_a_better_constant_cutoff() -> None:
    # Lane's fixed point here is 1e-6 below the best constant cut-off.
    inp = _inputs(mine_capacity=2525294.0, mill_capacity=1969735.0, fixed=2518560.0, discount=2**-9)
    result = optimise_cutoffs(inp, GRADES, TONNES)
    best = max(constant_cutoff_npv(inp, GRADES, TONNES, c) for c in np.r_[GRADES, 99.0])
    assert result.policy == "constant"
    assert result.npv >= best * (1 - 1e-12)
    assert len({y.cutoff for y in result.years}) == 1
    assert sum(y.material_t for y in result.years) == pytest.approx(TONNES.sum())
