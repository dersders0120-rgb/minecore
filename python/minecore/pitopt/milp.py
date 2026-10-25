"""NPV-maximising production schedule as a mixed-integer linear programme.

Same mining units and rules as the greedy scheduler (``scheduling``):
increments (one pushback on one bench), mining capacity, plant capacity per
route, bench lag between pushbacks, sinking rate and stockpile capacity with
rehandling. Variables, for increment ``i``, ore source ``j`` (the ore of one
increment going to one route) and period ``t``:

    w[i,t]  in [0, 1]  fraction of i mined by the end of t (non-decreasing)
                       (a source is the ore of one increment in one route and
                       one grade bin of ``Limits.grade_bins``)
    c[i,t]  in {0, 1}  i is completely mined by the end of t (c <= w)
    d[j,t]  >= 0       ore of j fed to its route in t straight from the pit
    r[j,t]  >= 0       ore of j reclaimed from the stockpile in t
    s[j,t]  >= 0       ore of j on the stockpile at the end of t
    q[j,t]  >= 0       ore of j sent to waste in t (only useful when the
                       stockpile is full)

    max  sum_t (1 + rate)^-t [ sum_j m_j (d + r) - h sum_j r - sum_i k_i dw_i ]

subject to

    w[i,t] <= c[p,t]       p: the bench above i in its pushback (sequence)
    w[i,t] <= c[l,t]       l: the bench ``lag`` deeper in the previous pushback
                           (or all of it), the bench lag
    w[i,t] <= c[u,t-1]     u: the bench ``sinking_rate`` above i (sinking rate)
    sum_i T_i dw_i <= M                           mining capacity
    sum_{j in route} (d + r) <= H_route           plant capacity
    s_t = s_t-1 + o_j dw_i - d - r - q,  d <= o_j dw_i,  r <= s_t-1
    sum_{j in route} s <= S_route                 stockpile capacity

with ``m_j`` the processing margin per ore tonne of source j, ``h`` the
rehandling cost per tonne and ``k_i`` the mining cost of increment i. The
stockpile keeps each source apart (no blending within a bin is needed), so
it is at least as selective as the greedy scheduler's binned piles. Every
greedy schedule with the same limits satisfies these constraints, so the
optimum is never worse than the greedy NPV; if the solver stops (time limit)
with a worse solution, the greedy schedule is returned and says so.
Periods may have different lengths and capacities (``Limits.period_years``,
``mining_rates``, ``plant_rates``); cash is discounted at each period's end
time. Blending targets (``Limits.blends``) add, per period and route,

    sum_j (a_j - hi)(d + r) <= 0   and   sum_j (a_j - lo)(d + r) >= 0

with ``a_j`` the feed-tonne weighted mean of the attribute in source j. The
greedy schedule ignores blending, so with targets the greedy NPV is only a
reference and the MILP solution is kept even when it is lower.

Solved with HiGHS through ``scipy.optimize.milp``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult
from minecore.pitopt.scheduling import (
    Blend,
    ConstrainedSchedule,
    Limits,
    PeriodRow,
    _Increment,
    _increments,
    _per_route,
    schedule_constrained,
)

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]


@dataclass(frozen=True)
class OptimisedSchedule(ConstrainedSchedule):
    method: str = "milp"  # "milp", or "greedy" when the solver did not beat it
    status: str = ""  # solver message
    mip_gap: float = math.nan  # relative gap to the best bound reported by HiGHS
    greedy_npv: float = math.nan
    periods_allowed: int = 0


@dataclass(frozen=True)
class _Source:
    inc: int
    route: int  # 0-based
    ore: float
    margin: float  # total processing margin of the ore (revenue - processing)
    metal: float
    grade_t: float
    revenue: float


class _Index:
    """Column numbers of the variables, period-major blocks per kind."""

    def __init__(self, n_inc: int, n_src: int, periods: int) -> None:
        self.n_inc, self.n_src, self.periods = n_inc, n_src, periods
        self.size = 0
        self.w0 = self._block(n_inc)
        self.c0 = self._block(n_inc)
        self.d0 = self._block(n_src)
        self.r0 = self._block(n_src)
        self.s0 = self._block(n_src)
        self.q0 = self._block(n_src)

    def _block(self, n: int) -> int:
        start = self.size
        self.size += n * self.periods
        return start

    def w(self, i: int, t: int) -> int:
        return self.w0 + t * self.n_inc + i

    def c(self, i: int, t: int) -> int:
        return self.c0 + t * self.n_inc + i

    def d(self, j: int, t: int) -> int:
        return self.d0 + t * self.n_src + j

    def r(self, j: int, t: int) -> int:
        return self.r0 + t * self.n_src + j

    def s(self, j: int, t: int) -> int:
        return self.s0 + t * self.n_src + j

    def q(self, j: int, t: int) -> int:
        return self.q0 + t * self.n_src + j


class _Rows:
    """Sparse constraint rows ``lo <= A x <= hi``."""

    def __init__(self) -> None:
        self.rows: list[int] = []
        self.cols: list[int] = []
        self.vals: list[float] = []
        self.lo: list[float] = []
        self.hi: list[float] = []

    def add(self, terms: list[tuple[int, float]], lo: float, hi: float) -> None:
        n = len(self.lo)
        for col, val in terms:
            self.rows.append(n)
            self.cols.append(col)
            self.vals.append(val)
        self.lo.append(lo)
        self.hi.append(hi)

    def constraint(self, size: int) -> LinearConstraint:
        a = coo_array((self.vals, (self.rows, self.cols)), shape=(len(self.lo), size))
        return LinearConstraint(a.tocsr(), np.asarray(self.lo), np.asarray(self.hi))


def _sources(incs: list[_Increment]) -> list[_Source]:
    """One source per increment, route and grade bin that holds ore."""
    out = []
    for i, inc in enumerate(incs):
        for k, bins in enumerate(inc.by_route):
            for ore, margin, metal, grade_t, revenue in bins:
                if ore > 0:
                    out.append(_Source(i, k, ore, margin, metal, grade_t, revenue))
    return out


def _predecessors(incs: list[_Increment], limits: Limits) -> tuple[list[list[int]], list[int]]:
    """(same-period predecessors, previous-period sinking predecessor or -1) per increment."""
    phases = sorted({inc.phase for inc in incs})
    order = {p: [i for i, inc in enumerate(incs) if inc.phase == p] for p in phases}
    same: list[list[int]] = [[] for _ in incs]
    sink = [-1] * len(incs)
    for k, p in enumerate(phases):
        mine = order[p]  # top bench first
        for pos, i in enumerate(mine):
            if pos > 0:
                same[i].append(mine[pos - 1])
            if pos >= limits.sinking_rate:
                sink[i] = mine[pos - limits.sinking_rate]
            if k > 0:
                prev = order[phases[k - 1]]
                need = incs[i].bench + limits.bench_lag
                deeper = [j for j in prev if incs[j].bench >= need]
                same[i].append(deeper[0] if deeper else prev[-1])
    return same, sink


@dataclass(frozen=True)
class _Problem:
    incs: list[_Increment]
    srcs: list[_Source]
    limits: Limits
    plant: Floats  # periods x routes: ore t of each route in each period
    room: Floats  # stockpile t of each route
    ix: _Index
    blend_means: list[Floats]  # per blending target: mean of the attribute in each source


def _objective(pb: _Problem) -> Floats:
    """Negative discounted cash per unit of each variable (milp minimises)."""
    ix, rate = pb.ix, pb.limits.discount_rate
    cost = np.zeros(ix.size)
    for t in range(ix.periods):
        f = (1.0 + rate) ** -pb.limits.end_years(t + 1)
        later = (1.0 + rate) ** -pb.limits.end_years(t + 2) if t + 1 < ix.periods else 0.0
        for i, inc in enumerate(pb.incs):  # mining cost of w_t - w_t-1
            cost[ix.w(i, t)] += (f - later) * inc.mining
        for j, src in enumerate(pb.srcs):
            per_t = src.margin / src.ore
            cost[ix.d(j, t)] -= f * per_t
            cost[ix.r(j, t)] -= f * (per_t - pb.limits.rehandle_cost)
    return cost


def _sequence_rows(pb: _Problem, rows: _Rows) -> None:
    ix, inf = pb.ix, math.inf
    same, sink = _predecessors(pb.incs, pb.limits)
    for t in range(ix.periods):
        for i in range(len(pb.incs)):
            if t > 0:
                rows.add([(ix.w(i, t), 1.0), (ix.w(i, t - 1), -1.0)], 0.0, inf)  # w grows
                rows.add([(ix.c(i, t - 1), 1.0), (ix.c(i, t), -1.0)], -inf, 0.0)  # c grows
            rows.add([(ix.c(i, t), 1.0), (ix.w(i, t), -1.0)], -inf, 0.0)  # c <= w
            for p in same[i]:
                rows.add([(ix.w(i, t), 1.0), (ix.c(p, t), -1.0)], -inf, 0.0)
            if sink[i] >= 0:
                before = [(ix.c(sink[i], t - 1), -1.0)] if t > 0 else []
                rows.add([(ix.w(i, t), 1.0), *before], -inf, 0.0)


def _capacity_rows(pb: _Problem, rows: _Rows) -> None:
    ix, inf = pb.ix, math.inf
    for t in range(ix.periods):
        mined = [(ix.w(i, t), inc.tonnes) for i, inc in enumerate(pb.incs)]
        if t > 0:
            mined += [(ix.w(i, t - 1), -inc.tonnes) for i, inc in enumerate(pb.incs)]
        rows.add(mined, -inf, pb.limits.mining_cap(t + 1))
        for k in range(pb.plant.shape[1]):
            mine = [j for j, src in enumerate(pb.srcs) if src.route == k]
            if mine and math.isfinite(pb.plant[t, k]):
                rows.add([(ix.d(j, t), 1.0) for j in mine] + [(ix.r(j, t), 1.0) for j in mine],
                         -inf, pb.plant[t, k])  # fmt: skip
            if mine and math.isfinite(pb.room[k]):
                rows.add([(ix.s(j, t), 1.0) for j in mine], -inf, pb.room[k])


def _stock_rows(pb: _Problem, rows: _Rows) -> None:
    ix, inf = pb.ix, math.inf
    for t in range(ix.periods):
        for j, src in enumerate(pb.srcs):
            fresh = [(ix.w(src.inc, t), src.ore)]
            if t > 0:
                fresh.append((ix.w(src.inc, t - 1), -src.ore))
            # s_t - s_t-1 - fresh + d + r + q = 0
            balance = [(ix.s(j, t), 1.0), (ix.d(j, t), 1.0), (ix.r(j, t), 1.0), (ix.q(j, t), 1.0)]
            balance += [(col, -v) for col, v in fresh]
            if t > 0:
                balance.append((ix.s(j, t - 1), -1.0))
            rows.add(balance, 0.0, 0.0)
            rows.add([(ix.d(j, t), 1.0)] + [(col, -v) for col, v in fresh], -inf, 0.0)
            rows.add([(ix.r(j, t), 1.0)] + ([(ix.s(j, t - 1), -1.0)] if t else []), -inf, 0.0)


def _blend_rows(pb: _Problem, rows: _Rows) -> None:
    ix, inf = pb.ix, math.inf
    for blend, means in zip(pb.limits.blends, pb.blend_means, strict=True):
        mine = [j for j, src in enumerate(pb.srcs) if src.route == blend.route - 1]
        if not mine:
            continue
        for t in range(ix.periods):
            for bound, lo, hi in ((blend.hi, -inf, 0.0), (blend.lo, 0.0, inf)):
                if not math.isfinite(bound):
                    continue
                terms = []
                for j in mine:
                    terms += [(ix.d(j, t), means[j] - bound), (ix.r(j, t), means[j] - bound)]
                rows.add(terms, lo, hi)


def _blend_means(
    result: PitResult, incs: list[_Increment], srcs: list[_Source], limits: Limits
) -> list[Floats]:
    """Feed-weighted mean of each blending attribute in each source (``_sources`` order)."""
    out = []
    n_routes = max(int(result.route.max(initial=1)), 1)
    edges = np.asarray(limits.grade_bins, np.float64)
    bin_of = np.searchsorted(edges, result.feed_grade, side="right")
    for blend in limits.blends:
        if blend.route > n_routes:
            raise PitOptError(
                f"blend {blend.label} is for route {blend.route}; there are {n_routes}"
            )
        if not blend.attribute:
            out.append(np.array([s.grade_t / s.ore for s in srcs]))
            continue
        values = _blend_values(blend, len(result.tonnes))
        sums = []
        for inc in incs:
            ore = inc.blocks[result.ore[inc.blocks]]
            route_of = np.where(result.route[ore] > 0, result.route[ore], 1)
            for r in range(n_routes):
                for b in range(len(limits.grade_bins) + 1):
                    sel = ore[(route_of == r + 1) & (bin_of[ore] == b)]
                    feed = result.feed[sel].sum()
                    if feed > 0:
                        sums.append(float((result.feed[sel] * values[sel]).sum() / feed))
        out.append(np.asarray(sums, np.float64))
    return out


def _blend_values(blend: Blend, blocks: int) -> Floats:
    if blend.values is None or len(blend.values) != blocks:
        raise PitOptError(f"blend {blend.label}: block values are missing (resolve_blends)")
    values = np.asarray(blend.values, np.float64)
    if not np.all(np.isfinite(values)):
        values = np.nan_to_num(values, nan=0.0)  # unestimated blocks count as zero
    return values


def schedule_optimised(  # noqa: PLR0913 - options are keyword-only
    result: PitResult,
    phase: Ints,
    limits: Limits,
    *,
    time_limit: float = 60.0,
    mip_gap: float = 0.005,
    mine_all: bool = True,
    extra_periods: int = 2,
) -> OptimisedSchedule:
    """NPV-optimal schedule within ``limits`` (see the module text).

    The horizon is the greedy schedule's length plus ``extra_periods``;
    ``mine_all`` requires the whole pit to be mined within it. The ore of an
    increment is split by route and by the grade bins of ``limits``; the
    stockpile keeps each of these sources apart.
    """
    if time_limit <= 0 or not 0 <= mip_gap < 1:
        raise PitOptError("time limit must be > 0 and the MIP gap in [0, 1)")
    greedy = schedule_constrained(result, phase, limits)
    incs = _increments(result, phase, limits.grade_bins)
    if not incs:
        raise PitOptError("there is nothing to schedule")
    srcs = _sources(incs)
    n_routes = max(int(result.route.max(initial=1)), 1)
    periods = max(len(greedy.rows), 1) + max(extra_periods, 0)
    pb = _Problem(
        incs,
        srcs,
        limits,
        np.stack([limits.plant_caps(t + 1, n_routes) for t in range(periods)]),
        _per_route(list(limits.stockpile_capacities), n_routes),
        _Index(len(incs), len(srcs), periods),
        _blend_means(result, incs, srcs, limits),
    )
    rows = _Rows()
    _sequence_rows(pb, rows)
    _capacity_rows(pb, rows)
    _stock_rows(pb, rows)
    _blend_rows(pb, rows)
    ix = pb.ix
    lower = np.zeros(ix.size)
    upper = np.full(ix.size, math.inf)
    upper[ix.w0 : ix.d0] = 1.0  # w and c in [0, 1]
    if mine_all:
        for i in range(len(incs)):
            lower[ix.w(i, periods - 1)] = 1.0
    integrality = np.zeros(ix.size)
    integrality[ix.c0 : ix.d0] = 1
    solved = milp(
        _objective(pb),
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=[rows.constraint(ix.size)],
        options={"time_limit": float(time_limit), "mip_rel_gap": float(mip_gap)},
    )
    status = str(solved.message)
    if solved.x is None:
        if limits.blends:
            raise PitOptError(
                f"no schedule meets the blending targets within the time limit ({status}); "
                "widen the targets or allow more time"
            )
        return _greedy(greedy, f"no MILP solution ({status})", periods)
    gap = float(getattr(solved, "mip_gap", math.nan))
    sched_rows, unmined = _rows(solved.x, pb, n_routes)
    npv = sum(row.discounted for row in sched_rows)
    if not limits.blends and npv < greedy.npv - 1e-9 * max(abs(greedy.npv), 1.0):
        return _greedy(greedy, f"MILP solution below the greedy NPV ({status})", periods)
    period_of = _periods(solved.x, incs, ix, len(phase))
    return OptimisedSchedule(
        tuple(sched_rows), period_of, unmined, "milp", status, gap, greedy.npv, periods
    )


def _greedy(greedy: ConstrainedSchedule, why: str, periods: int) -> OptimisedSchedule:
    return OptimisedSchedule(
        greedy.rows, greedy.period, greedy.unmined_t, "greedy", why, math.nan, greedy.npv, periods
    )


def _periods(x: Floats, incs: list[_Increment], ix: _Index, blocks: int) -> Ints:
    period = np.zeros(blocks, np.int64)
    for i, inc in enumerate(incs):
        for t in range(ix.periods):
            if x[ix.w(i, t)] > 1e-9:
                period[inc.blocks] = t + 1
                break
    return period


def _rows(x: Floats, pb: _Problem, n_routes: int) -> tuple[list[PeriodRow], float]:
    incs, srcs, ix, limits = pb.incs, pb.srcs, pb.ix, pb.limits
    n_i = ix.n_inc
    done = np.zeros(n_i)
    out: list[PeriodRow] = []
    idle: list[int] = []
    stock_before: dict[int, float] = {}
    for t in range(ix.periods):
        stock_before[t] = float(sum(max(x[ix.s(j, t)], 0.0) for j in range(len(srcs))))
        w_t = np.array([min(max(x[ix.w(i, t)], 0.0), 1.0) for i in range(n_i)])
        dw = np.maximum(w_t - done, 0.0)
        done = w_t
        mined = float(sum(inc.tonnes * dw[i] for i, inc in enumerate(incs)))
        cost = float(sum(inc.mining * dw[i] for i, inc in enumerate(incs)))
        ore_mined = float(sum(inc.ore_t * dw[i] for i, inc in enumerate(incs)))
        fed_route = np.zeros(n_routes)
        feed = margin = metal = grade_t = revenue = reclaimed = stock = lost = 0.0
        blend_t = np.zeros((len(pb.blend_means), 2))  # attribute x feed t, feed t
        for j, src in enumerate(srcs):
            dj = max(x[ix.d(j, t)], 0.0)
            rj = max(x[ix.r(j, t)], 0.0)
            fed = dj + rj
            share = fed / src.ore
            fed_route[src.route] += fed
            feed += fed
            margin += src.margin * share
            metal += src.metal * share
            grade_t += src.grade_t * share
            revenue += src.revenue * share
            reclaimed += rj
            stock += max(x[ix.s(j, t)], 0.0)
            lost += max(x[ix.q(j, t)], 0.0)
            for b, (blend, means) in enumerate(zip(limits.blends, pb.blend_means, strict=True)):
                if src.route == blend.route - 1:
                    blend_t[b] += (means[j] * fed, fed)
        if mined <= 1e-6 and feed <= 1e-6:
            idle.append(t)
            continue
        for gap_t in idle:  # empty periods inside the schedule keep their place
            out.append(_idle_row(gap_t, limits, stock_before.get(gap_t, 0.0)))
        idle.clear()
        rehandle = limits.rehandle_cost * reclaimed
        cost += rehandle
        cash = margin - cost
        out.append(
            PeriodRow(
                t + 1,
                mined,
                ore_mined,
                feed,
                grade_t / feed if feed > 0 else math.nan,
                metal,
                stock,
                cash,
                cash / (1.0 + limits.discount_rate) ** limits.end_years(t + 1),
                tuple(float(v) for v in fed_route),
                revenue,
                revenue - margin,
                cost,
                reclaimed,
                rehandle,
                lost,
                (stock,),
                limits.end_years(t + 1),
                tuple(float(a / f) if f > 0 else math.nan for a, f in blend_t),
            )
        )
    unmined = float(sum(inc.tonnes * (1.0 - done[i]) for i, inc in enumerate(incs)))
    return out, unmined


def _idle_row(t: int, limits: Limits, stock: float) -> PeriodRow:
    nan = math.nan
    return PeriodRow(
        t + 1, 0.0, 0.0, 0.0, nan, 0.0, stock, 0.0, 0.0,
        years=limits.end_years(t + 1),
    )  # fmt: skip
