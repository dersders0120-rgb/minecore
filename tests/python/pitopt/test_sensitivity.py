"""Sensitivity analysis: base case identity, hand-checkable changes, monotonic responses."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.pitopt import Economics, PriceUnit, SlopeProfile, optimise_pit, pushbacks
from minecore.pitopt.cashflow import Capital, FinancialTerms, cash_flow
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import revalue
from minecore.pitopt.scheduling import Limits, schedule_constrained
from minecore.pitopt.sensitivity import Input, Plan, parse_changes, sensitivity
from minecore.project import Project

ECON = Economics(60.0, PriceUnit.GRAM, 0.9, 2.0, 15.0, 3.0, royalty=0.03)
LIMITS = Limits(80_000.0, 20_000.0, discount_rate=0.1)
FACTORS = (0.6, 0.8, 1.0)


@pytest.fixture
def model(tmp_path: Path) -> Iterator[BlockModel]:
    project = Project.create(tmp_path / "s.mcproj", "s")
    rng = np.random.default_rng(5)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (14, 10, 7))
    bm = BlockModel.create(project, "m", grid)
    c = grid.centroids()
    near = np.hypot(c[:, 0] - 70, c[:, 1] - 50) < 35
    grade = np.where(near, rng.uniform(0.3, 4.0, grid.blocks), 0.0)
    bm.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "au")
    yield bm
    project.close()


def _plan(model: BlockModel, terms: FinancialTerms) -> Plan:
    return Plan(
        model,
        "au",
        ECON,
        SlopeProfile(45.0),
        LIMITS,
        terms,
        revenue_factors=FACTORS,
        final_rf=1.0,
        phase_ore=30_000.0,
    )


def _chain(model: BlockModel, econ: Economics, terms: FinancialTerms) -> float:
    pit = optimise_pit(model, "au", econ, SlopeProfile(45.0), FACTORS, mining_rate=80_000.0)
    phase = pushbacks(pit, 3, 30_000.0)
    return cash_flow(schedule_constrained(pit, phase, LIMITS).rows, terms).npv


def test_base_case_is_the_plain_chain(model: BlockModel) -> None:
    terms = FinancialTerms(capital=(Capital(0, 2e6),), tax_rate=0.25, discount_rate=0.1)
    s = sensitivity(_plan(model, terms), [Input.MINING], [0.1])
    assert s.base.npv == pytest.approx(_chain(model, ECON, terms))


def test_price_factor_equals_a_higher_price(model: BlockModel) -> None:
    pit = optimise_pit(model, "au", ECON, SlopeProfile(45.0), FACTORS)
    a = revalue(model, "au", ECON, pit, price_factor=1.15)
    b = revalue(model, "au", replace(ECON, price=ECON.price * 1.15), pit)
    assert a.value == pytest.approx(b.value)
    c = optimise_pit(model, "au", ECON, SlopeProfile(45.0), FACTORS, price_factor=1.15)
    d = optimise_pit(model, "au", replace(ECON, price=69.0), SlopeProfile(45.0), FACTORS)
    assert (c.shell == d.shell).all()
    same = revalue(model, "au", ECON, pit)
    assert same.value == pytest.approx(pit.value)  # revaluing with the same inputs is exact


def test_capital_change_by_hand(model: BlockModel) -> None:
    capital = (Capital(0, 3e6), Capital(2, 1e6))
    terms = FinancialTerms(capital=capital, discount_rate=0.1)  # no tax: capex is not deducted
    s = sensitivity(_plan(model, terms), [Input.CAPITAL], [-0.2, 0.2])
    pv = 3e6 + 1e6 / 1.1**2
    assert s.npv(Input.CAPITAL, 0.2) == pytest.approx(s.base.npv - 0.2 * pv)
    assert s.npv(Input.CAPITAL, -0.2) == pytest.approx(s.base.npv + 0.2 * pv)


def test_fixed_design_responses_have_the_right_sign(model: BlockModel) -> None:
    terms = FinancialTerms(capital=(Capital(0, 1e6),), tax_rate=0.2, discount_rate=0.1)
    s = sensitivity(_plan(model, terms), changes=[-0.1, 0.1])
    up = {i: s.npv(i, 0.1) for i in Input}
    down = {i: s.npv(i, -0.1) for i in Input}
    for i in (Input.PRICE, Input.RECOVERY):
        assert down[i] < s.base.npv < up[i]
    for i in (Input.MINING, Input.PROCESSING, Input.DISCOUNT, Input.CAPITAL):
        assert down[i] > s.base.npv > up[i]
    fixed = {c.pit_t for c in s.cases}
    assert fixed == {s.base.pit_t}  # the design does not move
    # The discount rate changes only the timing value: same tonnes and metal.
    disc = [c for c in s.cases if c.input is Input.DISCOUNT]
    assert all(c.metal == pytest.approx(s.base.metal) for c in disc)


def test_reoptimised_pit_grows_with_price(model: BlockModel) -> None:
    terms = FinancialTerms(discount_rate=0.1)
    s = sensitivity(_plan(model, terms), [Input.PRICE, Input.MINING], [-0.2, 0.2], reoptimise=True)
    by = {(c.input, c.change): c for c in s.cases}
    assert by[(Input.PRICE, -0.2)].pit_t <= s.base.pit_t <= by[(Input.PRICE, 0.2)].pit_t
    assert by[(Input.PRICE, 0.2)].pit_t > by[(Input.PRICE, -0.2)].pit_t
    assert by[(Input.MINING, 0.2)].pit_t <= s.base.pit_t <= by[(Input.MINING, -0.2)].pit_t
    assert s.reoptimised


def test_parse_and_validation(model: BlockModel) -> None:
    assert parse_changes("-20; -10; 0; 10, 20") == (-0.2, -0.1, 0.1, 0.2)
    with pytest.raises(PitOptError):
        parse_changes("x")
    with pytest.raises(PitOptError):
        sensitivity(_plan(model, FinancialTerms()), changes=[-1.0])
    with pytest.raises(PitOptError):
        sensitivity(replace(_plan(model, FinancialTerms()), final_rf=0.9))
    # Without capital there is no capital input.
    s = sensitivity(_plan(model, FinancialTerms()), changes=[0.1])
    assert Input.CAPITAL not in {c.input for c in s.cases}
