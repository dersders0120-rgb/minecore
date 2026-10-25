"""Sensitivity of the project to its economic inputs (spider analysis).

Each case changes one input by a relative amount and runs the chain again:
block values -> schedule (same constraints) -> project cash flow. Two modes:

* fixed design (default): the base pit shells and pushbacks are kept; only
  the block values, the ore/waste split, the routes and the schedule follow
  the new input. This is the usual project sensitivity of one mine plan.
* re-optimised: the pit, its pushbacks and the schedule are optimised again
  for every case (the pit grows with the price, shrinks with the costs).

Inputs and how a change ``c`` (e.g. -0.2) applies:

    price        every metal price (or the NSR) x (1 + c); selling costs stay
    mining       mining cost and its bench increment x (1 + c)
    processing   processing cost of every route x (1 + c)
    recovery     every metallurgical recovery x (1 + c), capped at 100 %
    discount     discount rate x (1 + c)
    capital      every capital item x (1 + c)

A case whose input becomes invalid (e.g. a price below the selling cost)
reports NaN.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from minecore.blockmodel import BlockModel
from minecore.pitopt.cashflow import Capital, CashFlow, FinancialTerms, cash_flow
from minecore.pitopt.economics import Economics, PitOptError, Route
from minecore.pitopt.optimise import PitResult, optimise_pit, pushbacks, revalue
from minecore.pitopt.precedence import SlopeProfile
from minecore.pitopt.scheduling import ConstrainedSchedule, Limits, schedule_constrained
from minecore.pitopt.width import apply_mining_width

Ints = npt.NDArray[np.int64]


class Input(StrEnum):
    PRICE = "price"
    MINING = "mining"
    PROCESSING = "processing"
    RECOVERY = "recovery"
    DISCOUNT = "discount"
    CAPITAL = "capital"


@dataclass(frozen=True)
class Plan:
    """The base case: everything needed to run the chain again."""

    model: BlockModel
    grade_attribute: str
    econ: Economics
    slope: SlopeProfile
    limits: Limits
    terms: FinancialTerms
    revenue_factors: tuple[float, ...] = (1.0,)  # the nested shells pushbacks are made of
    final_rf: float = 1.0  # the final pit, one of ``revenue_factors``
    phase_ore: float = 0.0  # ore t per pushback; 0 = a third of the final pit
    min_width: float = 0.0
    rock_attribute: str | None = None


@dataclass(frozen=True)
class Case:
    input: Input | None  # None for the base case
    change: float  # relative change, e.g. -0.1
    npv: float  # project NPV (cash flow with capital, tax, closure)
    irr: float
    ore_t: float  # ore fed over the life
    metal: float  # main metal recovered over the life
    years: int
    pit_t: float  # total tonnes inside the pit (changes only when re-optimised)


@dataclass(frozen=True)
class Sensitivity:
    base: Case
    cases: tuple[Case, ...]
    reoptimised: bool

    def npv(self, which: Input, change: float) -> float:
        for c in self.cases:
            if c.input == which and math.isclose(c.change, change):
                return c.npv
        raise KeyError((which, change))


def _scale_economics(econ: Economics, which: Input, f: float) -> Economics:
    if which is Input.MINING:
        return replace(
            econ,
            mining_cost=econ.mining_cost * f,
            mining_cost_per_bench=econ.mining_cost_per_bench * f,
        )
    if which is Input.PROCESSING:
        routes = tuple(Route(r.name, r.recovery, r.processing_cost * f) for r in econ.routes)
        return replace(econ, processing_cost=econ.processing_cost * f, routes=routes)
    if which is Input.RECOVERY:

        def cap(v: float) -> float:
            return min(1.0, v * f)

        routes = tuple(Route(r.name, cap(r.recovery), r.processing_cost) for r in econ.routes)
        products = tuple(
            replace(
                p,
                recovery=cap(p.recovery),
                route_recoveries=tuple(cap(v) for v in p.route_recoveries),
            )
            for p in econ.coproducts
        )
        return replace(econ, recovery=cap(econ.recovery), routes=routes, coproducts=products)
    return econ


def _scale_terms(
    limits: Limits, terms: FinancialTerms, which: Input, f: float
) -> tuple[Limits, FinancialTerms]:
    if which is Input.DISCOUNT:
        return (
            replace(limits, discount_rate=limits.discount_rate * f),
            replace(terms, discount_rate=terms.discount_rate * f),
        )
    if which is Input.CAPITAL:
        capital = tuple(Capital(c.period, c.amount * f, c.label) for c in terms.capital)
        return limits, replace(terms, capital=capital)
    return limits, terms


class _Runner:
    def __init__(self, plan: Plan, reoptimise: bool, base: tuple[PitResult, Ints] | None) -> None:
        if plan.final_rf not in plan.revenue_factors:
            raise PitOptError("the final revenue factor must be one of the revenue factors")
        self.plan = plan
        self.final = sorted(plan.revenue_factors).index(plan.final_rf) + 1
        self.reoptimise = reoptimise
        if base is None:
            pit = self._optimise(plan.econ, 1.0)
            base = (pit, self._phases(pit))
        self.base_pit, self.base_phase = base

    def _optimise(self, econ: Economics, price: float) -> PitResult:
        p = self.plan
        return optimise_pit(
            p.model,
            p.grade_attribute,
            econ,
            p.slope,
            list(p.revenue_factors),
            mining_rate=p.limits.mining_capacity,
            discount_rate=p.limits.discount_rate,
            rock_attribute=p.rock_attribute,
            price_factor=price,
        )

    def _phases(self, pit: PitResult) -> Ints:
        p = self.plan
        ore = p.phase_ore or max(pit.rows[self.final - 1].ore_t / 3, 1.0)
        phase = pushbacks(pit, self.final, ore)
        if p.min_width > 0:
            phase = apply_mining_width(p.model.grid, phase, p.slope, p.min_width).phase
        return phase

    def run(self, which: Input | None, change: float) -> Case:
        p, f = self.plan, 1.0 + change
        econ, limits, terms, price = p.econ, p.limits, p.terms, 1.0
        if which is not None:
            econ = _scale_economics(econ, which, f)
            limits, terms = _scale_terms(limits, terms, which, f)
            price = f if which is Input.PRICE else 1.0
        if which is None or not self.reoptimise:
            pit = revalue(
                p.model,
                p.grade_attribute,
                econ,
                self.base_pit,
                rock_attribute=p.rock_attribute,
                price_factor=price,
            )
            phase = self.base_phase
        else:
            pit = self._optimise(econ, price)
            phase = self._phases(pit)
        sched = schedule_constrained(pit, phase, limits)
        flow = cash_flow(sched.rows, terms)
        return _case(which, change, pit, sched, flow)


def _case(
    which: Input | None,
    change: float,
    pit: PitResult,
    sched: ConstrainedSchedule,
    flow: CashFlow,
) -> Case:
    return Case(
        which,
        change,
        flow.npv,
        flow.irr(),
        sum(r.ore_fed_t for r in sched.rows),
        sum(r.metal for r in sched.rows),
        len(sched.rows),
        float(pit.tonnes[pit.shell > 0].sum()),
    )


def sensitivity(  # noqa: PLR0913 - options are keyword-only
    plan: Plan,
    inputs: Sequence[Input] = tuple(Input),
    changes: Sequence[float] = (-0.2, -0.1, 0.1, 0.2),
    *,
    reoptimise: bool = False,
    base: tuple[PitResult, Ints] | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> Sensitivity:
    """Run the base case and every (input, change) case.

    ``base`` is the plan's pit and pushbacks when they are already computed
    (they must come from ``plan``); otherwise they are optimised here.
    """
    if not changes or any(not c > -1 for c in changes):
        raise PitOptError("changes must be > -100 %")
    inputs = [i for i in inputs if i is not Input.CAPITAL or plan.terms.capital]
    runner = _Runner(plan, reoptimise, base)
    base_case = runner.run(None, 0.0)
    cases = []
    total = len(inputs) * len(changes)
    for n, (which, change) in enumerate(((i, c) for i in inputs for c in changes), start=1):
        try:
            cases.append(runner.run(which, float(change)))
        except PitOptError:
            nan = math.nan
            cases.append(Case(which, float(change), nan, nan, nan, nan, 0, nan))
        if progress is not None:
            progress(n / total, f"{which.value} {change:+.0%}")
    return Sensitivity(base_case, tuple(cases), reoptimise)


def parse_changes(text: str) -> tuple[float, ...]:
    """``"-20; -10; 10; 20"`` (percent) -> (-0.2, -0.1, 0.1, 0.2)."""
    try:
        values = [float(v) / 100.0 for v in text.replace(",", ";").split(";") if v.strip()]
    except ValueError:
        raise PitOptError(f"changes {text!r} are not percentages") from None
    return tuple(sorted(set(values) - {0.0}))
