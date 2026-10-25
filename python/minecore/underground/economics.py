"""Stope economics and a production schedule for selected stopes.

Each stope is valued with the pit economics terms applied to the stope as a
whole: mined tonnes = in-situ tonnes x mining recovery x (1 + dilution) at
zero diluting grade, recovered metal = in-situ metal x mining recovery x
recovery x payable, revenue = metal x (price - selling) x (1 - royalty), and
costs = mined tonnes x (underground mining + processing cost) plus a fixed
development cost per stope. ``mining_cost`` is the underground cost per
tonne mined (no bench increment).

The schedule mines stopes level by level (bottom-up or top-down by the
stope's lowest block layer), richest value first within a level, filling
each year to the mining capacity; a stope can span two years. Stopes with a
negative value are left out unless asked. The rows are the pit schedule's
``PeriodRow`` so the project cash flow (capital, tax, closure) is shared.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from minecore._core import Unit
from minecore.pitopt.economics import Economics, PitOptError
from minecore.pitopt.scheduling import ConstrainedSchedule, PeriodRow
from minecore.underground.stopes import Stope


@dataclass(frozen=True)
class StopeValue:
    stope: Stope
    mined_t: float
    grade: float  # diluted grade
    metal: float  # recovered, payable metal units
    revenue: float
    cost: float
    value: float


def metal_per_grade(econ: Economics, unit: str | None) -> float:
    """Price-unit metal per (grade unit x tonne)."""
    if econ.nsr:
        return 1.0
    if unit is None:
        raise PitOptError("the grade attribute has no unit; set % or g/t to value metal")
    return 1000.0 * Unit(unit).si_factor / econ.price_unit.kilograms


def stope_values(
    stopes: tuple[Stope, ...], econ: Economics, unit: str | None, development_cost: float = 0.0
) -> list[StopeValue]:
    per_grade = metal_per_grade(econ, unit)
    price, selling, rec = (
        (1.0, 0.0, 1.0) if econ.nsr else (econ.price, econ.selling_cost, econ.recovery)
    )
    payable = 1.0 if econ.nsr else econ.payable
    out = []
    for s in stopes:
        mined = s.tonnes * econ.mining_recovery * (1.0 + econ.dilution)
        metal = s.tonnes * s.grade * per_grade * econ.mining_recovery * rec * payable
        revenue = metal * (price - selling) * (1.0 - econ.royalty)
        cost = mined * (econ.mining_cost + econ.processing_cost) + development_cost
        grade = s.grade / (1.0 + econ.dilution)
        out.append(StopeValue(s, mined, grade, metal, revenue, cost, revenue - cost))
    return out


def schedule_stopes(  # noqa: PLR0913 - options are keyword-only
    values: list[StopeValue],
    capacity_t: float,
    discount_rate: float,
    *,
    bottom_up: bool = True,
    include_negative: bool = False,
    development_cost: float = 0.0,
) -> tuple[ConstrainedSchedule, list[tuple[StopeValue, int]]]:
    """Yearly schedule and the (stope, first year) of each scheduled stope."""
    if capacity_t <= 0:
        raise PitOptError("the mining capacity must be > 0")
    chosen = [v for v in values if include_negative or v.value > 0]
    sign = 1 if bottom_up else -1
    chosen.sort(key=lambda v: (sign * v.stope.k, -v.value))
    years: list[dict[str, float]] = []
    starts: list[tuple[StopeValue, int]] = []

    def year(t: int) -> dict[str, float]:
        while len(years) <= t:
            years.append(dict.fromkeys(("t", "gt", "metal", "revenue", "cost"), 0.0))
        return years[t]

    t, room = 0, capacity_t
    for v in chosen:
        left = v.mined_t
        starts.append((v, t + 1))
        if left <= 0:
            continue
        while left > 1e-9:
            take = min(left, room)
            share = take / v.mined_t
            y = year(t)
            y["t"] += take
            y["gt"] += take * v.grade
            y["metal"] += v.metal * share
            y["revenue"] += v.revenue * share
            # the development cost is spent when the stope starts
            y["cost"] += (v.cost - development_cost) * share
            if left == v.mined_t:
                y["cost"] += development_cost
            left -= take
            room -= take
            if room <= 1e-9:
                t, room = t + 1, capacity_t
    rows = []
    for i, y in enumerate(years):
        if y["t"] <= 0:
            continue
        cash = y["revenue"] - y["cost"]
        rows.append(
            PeriodRow(
                period=i + 1,
                mined_t=y["t"],
                ore_mined_t=y["t"],
                ore_fed_t=y["t"],
                feed_grade=y["gt"] / y["t"],
                metal=y["metal"],
                stockpile_t=0.0,
                cash=cash,
                discounted=cash / (1.0 + discount_rate) ** (i + 1),
                revenue=y["revenue"],
                processing=0.0,
                mining_cost=y["cost"],
            )
        )
    left_out = sum(v.mined_t for v in values) - sum(v.mined_t for v in chosen)
    return ConstrainedSchedule(tuple(rows), np.zeros(0, np.int64), left_out), starts
