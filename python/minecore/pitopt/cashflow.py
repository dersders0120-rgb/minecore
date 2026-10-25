"""Project cash flow from a production schedule: capital, fixed costs, closure, tax.

The schedule (``scheduling.schedule_constrained``) gives, per period, the
sales revenue of the ore fed (after payables and royalty), its processing
cost and the mining cost of everything mined. This module adds what is not a
block cost and computes the project's net cash flow and its measures:

    EBITDA_t   = revenue_t - processing_t - mining_t - fixed_t
    D_t        = straight-line depreciation of each capital item over
                 ``depreciation_years`` periods, starting the period after it
                 is spent (period 0 capital from period 1)
    taxable_t  = EBITDA_t - D_t - closure_t, less losses carried forward
    tax_t      = tax_rate * max(taxable_t, 0)
    CF_t       = EBITDA_t - tax_t - capital_t - closure_t

Period 0 is before production (construction); it holds capital only and is
not discounted. Production periods are 1..n; closure is paid in period n+1,
where the book value not yet depreciated is also written off.
Discounting is at the end of each period, ``CF_t / (1 + r)^t``, or at its
middle, ``CF_t / (1 + r)^(t - 0.5)`` (``mid_period``). The rate is per period
(per year for the schedules MineCore builds). Losses are carried forward
without limit and never refunded. No working capital, inflation, financing or
salvage value is modelled.

The royalty is part of the block value (``Economics.royalty``), so the
schedule's revenue is already net of it; ``royalty`` here only reports the
amount paid: ``gross = revenue / (1 - royalty)``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.scheduling import PeriodRow


@dataclass(frozen=True)
class Capital:
    period: int  # 0 = construction, before production
    amount: float
    label: str = ""

    def __post_init__(self) -> None:
        if self.period < 0:
            raise PitOptError("capital period must be >= 0")
        if not (math.isfinite(self.amount) and self.amount >= 0):
            raise PitOptError("capital amount must be >= 0")


@dataclass(frozen=True)
class FinancialTerms:
    capital: tuple[Capital, ...] = ()
    fixed_cost: float = 0.0  # per production period (site overheads, G&A)
    closure_cost: float = 0.0  # paid in the period after the last production period
    tax_rate: float = 0.0  # on taxable income
    depreciation_years: int = 5
    discount_rate: float = 0.08  # per period
    mid_period: bool = False
    royalty: float = 0.0  # only for reporting the royalty paid; see the module text

    def __post_init__(self) -> None:
        for name in ("fixed_cost", "closure_cost"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value >= 0):
                raise PitOptError(f"{name} must be >= 0")
        if not 0 <= self.tax_rate < 1:
            raise PitOptError("tax rate must be in [0, 1)")
        if self.depreciation_years < 1:
            raise PitOptError("depreciation needs at least 1 period")
        if not 0 <= self.discount_rate < 10:
            raise PitOptError("discount rate must be in [0, 10)")
        if not 0 <= self.royalty < 1:
            raise PitOptError("royalty must be in [0, 1)")


def parse_capital(text: str) -> tuple[Capital, ...]:
    """Capital items from ``"period:amount"`` pairs separated by ``;`` (``"0:250e6; 4:40e6"``)."""
    out = []
    for item in text.replace(",", ";").split(";"):
        if not item.strip():
            continue
        parts = item.split(":")
        try:
            period, amount = int(parts[0]), float(parts[1])
        except (IndexError, ValueError):
            raise PitOptError(f"capital {item.strip()!r} is not period:amount") from None
        if len(parts) != 2:
            raise PitOptError(f"capital {item.strip()!r} is not period:amount")
        out.append(Capital(period, amount))
    return tuple(out)


@dataclass(frozen=True)
class CashFlowRow:
    period: int
    gross_revenue: float
    royalty: float
    revenue: float
    processing: float
    mining: float
    fixed: float
    ebitda: float
    depreciation: float
    taxable: float  # after losses carried forward
    tax: float
    capital: float
    closure: float
    cash: float
    discount_factor: float
    discounted: float
    cumulative: float  # undiscounted, for payback


@dataclass(frozen=True)
class CashFlow:
    rows: tuple[CashFlowRow, ...]
    terms: FinancialTerms

    @property
    def npv(self) -> float:
        return sum(r.discounted for r in self.rows)

    @property
    def undiscounted(self) -> float:
        return sum(r.cash for r in self.rows)

    def irr(self) -> float:
        """Rate at which the NPV is zero (end-of-period), NaN when there is none.

        Searched by bisection on (-0.99, 10); defined only when the cash flow
        starts negative and turns positive (one sign change in the NPV).
        """
        cash = [r.cash for r in self.rows]
        periods = [r.period for r in self.rows]

        def npv(rate: float) -> float:
            return sum(c / (1.0 + rate) ** t for c, t in zip(cash, periods, strict=True))

        lo, hi = -0.99, 10.0
        f_lo, f_hi = npv(lo), npv(hi)
        if not (math.isfinite(f_lo) and math.isfinite(f_hi)) or f_lo * f_hi > 0:
            return math.nan
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            f_mid = npv(mid)
            if (f_mid > 0) == (f_lo > 0):
                lo, f_lo = mid, f_mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def payback(self) -> float:
        """Periods from the start of production until the cumulative cash turns
        non-negative, interpolated within the period; NaN if it never does."""
        previous = self.rows[0].cumulative  # period 0: construction
        for r in self.rows[1:]:
            if r.cumulative >= 0:
                if previous >= 0:
                    return float(r.period - 1)
                return r.period - 1 + (-previous / r.cash)
            previous = r.cumulative
        return math.nan


def cash_flow(schedule_rows: Sequence[PeriodRow], terms: FinancialTerms) -> CashFlow:
    """Period-by-period project cash flow (period 0 = construction)."""
    production = max((r.period for r in schedule_rows), default=0)
    if production == 0:
        raise PitOptError("the schedule has no production periods")
    if any(c.period > production for c in terms.capital):
        raise PitOptError(f"capital is spent after the last production period ({production})")
    by_period = {r.period: r for r in schedule_rows}
    spend = [0.0] * (production + 2)
    for c in terms.capital:
        spend[c.period] += c.amount
    rate, life = terms.discount_rate, terms.depreciation_years
    rows: list[CashFlowRow] = []
    losses = cumulative = booked = 0.0
    for t in range(production + 2):
        sched = by_period.get(t)
        revenue = sched.revenue if sched else 0.0
        processing = sched.processing if sched else 0.0
        mining = sched.mining_cost if sched else 0.0
        fixed = terms.fixed_cost if 1 <= t <= production else 0.0
        closure = terms.closure_cost if t == production + 1 else 0.0
        ebitda = revenue - processing - mining - fixed
        if t <= production:
            depreciation = sum(spend[s] / life for s in range(t) if 0 < t - s <= life)
            booked += depreciation
        else:  # closure period: the book value left is written off
            depreciation = sum(spend) - booked
        income = ebitda - depreciation - closure
        if income < 0:
            losses += -income
            taxable = 0.0
        else:
            used = min(losses, income)
            losses -= used
            taxable = income - used
        tax = terms.tax_rate * taxable
        cash = ebitda - tax - spend[t] - closure
        exponent = 0.0 if t == 0 else (t - 0.5 if terms.mid_period else float(t))
        factor = (1.0 + rate) ** -exponent
        cumulative += cash
        gross = revenue / (1.0 - terms.royalty)
        rows.append(
            CashFlowRow(
                t,
                gross,
                gross - revenue,
                revenue,
                processing,
                mining,
                fixed,
                ebitda,
                depreciation,
                taxable,
                tax,
                spend[t],
                closure,
                cash,
                factor,
                cash * factor,
                cumulative,
            )
        )
    last = rows[-1]
    if last.cash == 0.0 and last.closure == 0.0 and last.depreciation == 0.0:
        rows.pop()  # no closure period without a closure cost or a write-off
    return CashFlow(tuple(rows), terms)
