"""Project cash flow: capital, depreciation, tax, closure, NPV, IRR, payback by hand."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.pitopt import (
    Economics,
    PriceUnit,
    SlopeProfile,
    block_values,
    optimise_pit,
    pushbacks,
)
from minecore.pitopt.cashflow import Capital, FinancialTerms, cash_flow, parse_capital
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.scheduling import Limits, PeriodRow, schedule_constrained
from minecore.project import Project


def _row(period: int, ebitda: float) -> PeriodRow:
    """A schedule period whose revenue - processing - mining equals ``ebitda``."""
    revenue, processing = ebitda + 300.0, 200.0
    return PeriodRow(period, 0, 0, 0, 0, 0, 0, ebitda, 0, (), revenue, processing, 100.0)


def test_capital_depreciation_and_tax_by_hand() -> None:
    terms = FinancialTerms(
        capital=(Capital(0, 1000.0),), tax_rate=0.3, depreciation_years=2, discount_rate=0.1
    )
    cf = cash_flow([_row(1, 700.0), _row(2, 700.0)], terms)
    assert [r.period for r in cf.rows] == [0, 1, 2]
    # Depreciation 500 per year; taxable 200; tax 60; net 640.
    assert [r.depreciation for r in cf.rows] == [0.0, 500.0, 500.0]
    assert [r.tax for r in cf.rows] == pytest.approx([0.0, 60.0, 60.0])
    assert [r.cash for r in cf.rows] == pytest.approx([-1000.0, 640.0, 640.0])
    assert cf.npv == pytest.approx(-1000 + 640 / 1.1 + 640 / 1.21)  # 110.74
    irr = cf.irr()
    assert sum(c / (1 + irr) ** t for c, t in ((-1000, 0), (640, 1), (640, 2))) == pytest.approx(
        0.0, abs=1e-6
    )
    x = (-640 + math.sqrt(640**2 + 4 * 640 * 1000)) / (2 * 640)  # 1 / (1 + irr), quadratic
    assert irr == pytest.approx(1 / x - 1, abs=1e-9)  # 18.16 %
    assert cf.payback() == pytest.approx(1 + 360 / 640)
    assert cf.undiscounted == pytest.approx(280.0)


def test_losses_are_carried_forward_not_refunded() -> None:
    terms = FinancialTerms(tax_rate=0.25, discount_rate=0.0)
    cf = cash_flow([_row(1, -300.0), _row(2, 500.0)], terms)
    assert [r.tax for r in cf.rows] == pytest.approx([0.0, 0.0, 0.25 * 200.0])
    assert cf.rows[2].taxable == pytest.approx(200.0)


def test_closure_cost_and_write_off_in_the_period_after_production() -> None:
    terms = FinancialTerms(
        capital=(Capital(0, 900.0),),
        closure_cost=50.0,
        tax_rate=0.2,
        depreciation_years=10,
        discount_rate=0.0,
    )
    cf = cash_flow([_row(1, 400.0), _row(2, 400.0)], terms)
    assert [r.period for r in cf.rows] == [0, 1, 2, 3]
    last = cf.rows[-1]
    assert last.closure == 50.0
    assert last.depreciation == pytest.approx(900.0 - 2 * 90.0)  # remaining book value
    assert last.tax == 0.0
    assert last.cash == pytest.approx(-50.0)
    # Taxes: (400 - 90) x 20 % in each production period.
    assert [r.tax for r in cf.rows[1:3]] == pytest.approx([62.0, 62.0])


def test_mid_period_discounting_and_fixed_costs() -> None:
    end = cash_flow([_row(1, 110.0)], FinancialTerms(discount_rate=0.1))
    assert end.npv == pytest.approx(100.0)
    assert len(end.rows) == 2  # construction period, production period; no closure row
    mid = cash_flow([_row(1, 110.0)], FinancialTerms(discount_rate=0.1, mid_period=True))
    assert mid.npv == pytest.approx(110.0 / math.sqrt(1.1))
    fixed = cash_flow([_row(1, 110.0)], FinancialTerms(fixed_cost=10.0, discount_rate=0.1))
    assert fixed.rows[1].ebitda == pytest.approx(100.0)
    assert fixed.npv == pytest.approx(100.0 / 1.1)


def test_royalty_and_payable_enter_the_block_value() -> None:
    econ = Economics(10_000.0, PriceUnit.TONNE, 0.8, 20.0, 30.0, payable=0.96, royalty=0.04)
    out = block_values(np.array([1.0]), "%", np.array([1000.0]), np.zeros(1, np.int64), econ)
    revenue = 1000 * 0.01 * 0.8 * 0.96 * 10_000 * 0.96  # payable 96 %, royalty 4 %
    assert out.revenue[0] == pytest.approx(revenue)
    assert out.value[0] == pytest.approx(revenue - 50_000.0)
    terms = FinancialTerms(royalty=0.04)
    row = PeriodRow(1, 0, 0, 0, 0, 0, 0, 0, 0, (), revenue, 0.0, 0.0)
    cf = cash_flow([row], terms)
    assert cf.rows[1].gross_revenue == pytest.approx(revenue / 0.96)
    assert cf.rows[1].royalty == pytest.approx(revenue / 0.96 * 0.04)
    with pytest.raises(PitOptError):
        Economics(1.0, PriceUnit.TONNE, 0.9, 1.0, 1.0, payable=0.0)
    with pytest.raises(PitOptError):
        Economics(1.0, PriceUnit.TONNE, 0.9, 1.0, 1.0, royalty=1.0)


def test_without_terms_the_cash_flow_npv_equals_the_schedule_npv(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "c.mcproj", "c")
    rng = np.random.default_rng(2)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (12, 8, 6))
    model = BlockModel.create(project, "m", grid)
    c = grid.centroids()
    grade = np.where(np.hypot(c[:, 0] - 60, c[:, 1] - 40) < 30, rng.uniform(0.5, 4, grid.blocks), 0)
    model.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "au")
    econ = Economics(60.0, PriceUnit.GRAM, 0.9, 2.0, 15.0, royalty=0.05, payable=0.99)
    result = optimise_pit(model, "au", econ, SlopeProfile(45.0), [0.7, 1.0])
    sched = schedule_constrained(
        result, pushbacks(result, 2, 20_000.0), Limits(60_000.0, 15_000.0, discount_rate=0.1)
    )
    for r in sched.rows:  # the schedule's cash splits into its parts
        assert r.revenue - r.processing - r.mining_cost == pytest.approx(r.cash)
    cf = cash_flow(sched.rows, FinancialTerms(discount_rate=0.1, royalty=0.05))
    assert cf.npv == pytest.approx(sched.npv)
    total_revenue = sum(r.revenue for r in sched.rows)
    assert total_revenue == pytest.approx(result.revenue[result.shell > 0].sum())
    assert sum(r.royalty for r in cf.rows) == pytest.approx(total_revenue / 0.95 * 0.05)
    project.close()


def test_parse_and_validation() -> None:
    assert parse_capital("0:250e6; 4:40e6") == (Capital(0, 250e6), Capital(4, 40e6))
    with pytest.raises(PitOptError):
        parse_capital("x:1")
    with pytest.raises(PitOptError):
        Capital(-1, 1.0)
    with pytest.raises(PitOptError):
        FinancialTerms(tax_rate=1.0)
    with pytest.raises(PitOptError):
        cash_flow([_row(1, 1.0)], FinancialTerms(capital=(Capital(5, 1.0),)))
    with pytest.raises(PitOptError):
        cash_flow([], FinancialTerms())
