"""Period-by-period production schedule with mining and plant limits, bench
lag between pushbacks and a stockpile.

Mining units are *increments*: the blocks of one pushback on one bench.
Each period the scheduler mines increments in pushback order, top bench
first, subject to:

* total mining capacity (t/period); an increment may be split across periods;
* sinking rate: a pushback works on at most ``sinking_rate`` benches per
  period, which leaves capacity for the next pushbacks (concurrent mining);
* bench lag: pushback ``p`` may not start bench ``b`` until pushback ``p-1``
  has finished bench ``b - lag`` (or is finished);
* plant capacity (ore t/period). With process routes (``PitResult.route``)
  the plant capacity is the main route's and ``route_capacities`` give the
  extra routes theirs (unlimited when not given).

Stockpiles: ore is split into grade bins by its feed grade (``grade_bins``
are the bin edges, ascending; no edges = one bin). Each route has one
stockpile per bin. Every period each route fills its plant capacity from the
highest bin down, taking within a bin the fresh ore before the stockpiled
ore, so high-grade stock is reclaimed before low-grade fresh ore is fed and
low-grade ore waits on the stockpile. Fresh ore that is not fed goes to its
bin's stockpile, highest bin first, while the route's stockpile has room
(``stockpile_capacities``, t, per route; unlimited when not given); ore that
finds no room is sent to the waste dump and its margin is lost
(``PeriodRow.discarded_t``). Reclaimed ore pays ``rehandle_cost`` per tonne.
Stockpiles hold the blended grade of what entered each bin; there are no
stockpile losses or grade degradation.

Cash per period = processing margin of the ore fed (revenue - processing cost)
- mining cost of everything mined - rehandling. Stockpiled ore pays its
mining cost when mined and earns its margin when reclaimed. Cash is
discounted at the end of each period. This is a deterministic greedy sequence, not an optimised
schedule (no MILP); it respects the constraints and is reproducible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult

Ints = npt.NDArray[np.int64]
Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Limits:
    mining_capacity: float  # t per period
    plant_capacity: float  # ore t per period
    bench_lag: int = 2
    sinking_rate: int = 6  # max benches a pushback may advance per period
    discount_rate: float = 0.08
    max_periods: int = 200
    route_capacities: tuple[float, ...] = ()  # ore t per period of routes 2, 3, ...
    grade_bins: tuple[float, ...] = ()  # ascending feed-grade edges of the stockpile bins
    stockpile_capacities: tuple[float, ...] = ()  # t per route (main first); unlimited if absent
    rehandle_cost: float = 0.0  # per tonne reclaimed from a stockpile

    def __post_init__(self) -> None:
        if self.mining_capacity <= 0 or self.plant_capacity <= 0:
            raise PitOptError("mining and plant capacities must be > 0")
        if any(not c > 0 for c in self.route_capacities):
            raise PitOptError("route capacities must be > 0")
        if any(not c >= 0 for c in self.stockpile_capacities):
            raise PitOptError("stockpile capacities must be >= 0")
        if any(b >= a for b, a in zip(self.grade_bins, self.grade_bins[1:], strict=False)) or not (
            all(math.isfinite(b) for b in self.grade_bins)
        ):
            raise PitOptError("grade bin edges must be finite and strictly ascending")
        if not (math.isfinite(self.rehandle_cost) and self.rehandle_cost >= 0):
            raise PitOptError("rehandling cost must be >= 0")
        if self.bench_lag < 0:
            raise PitOptError("bench lag must be >= 0")
        if self.sinking_rate < 1:
            raise PitOptError("sinking rate must be >= 1 bench per period")


@dataclass(frozen=True)
class PeriodRow:
    period: int
    mined_t: float
    ore_mined_t: float
    ore_fed_t: float
    feed_grade: float
    metal: float
    stockpile_t: float
    cash: float
    discounted: float
    fed_by_route: tuple[float, ...] = ()  # ore fed per process route (main first)
    revenue: float = 0.0  # sales revenue of the ore fed, after payables and royalty
    processing: float = 0.0  # processing cost of the ore fed
    mining_cost: float = 0.0  # mining cost of everything mined, plus rehandling
    reclaimed_t: float = 0.0  # ore fed from the stockpiles
    rehandle: float = 0.0  # rehandling cost (included in mining_cost)
    discarded_t: float = 0.0  # ore sent to waste because the stockpile was full
    stock_by_bin: tuple[float, ...] = ()  # stockpile t per grade bin (lowest first), all routes


@dataclass(frozen=True)
class ConstrainedSchedule:
    rows: tuple[PeriodRow, ...]
    period: Ints  # period in which each block's increment starts (0 = not mined)
    unmined_t: float

    @property
    def npv(self) -> float:
        return sum(r.discounted for r in self.rows)


@dataclass
class _Increment:
    phase: int
    bench: int  # 0 = top bench of the model
    blocks: Ints
    tonnes: float
    ore_t: float
    margin: float  # processing margin of its ore
    mining: float
    metal: float
    grade_t: float  # sum(tonnes * grade) of its ore
    by_route: Floats  # routes x bins x (ore t, margin, metal, grade t, revenue)
    done: float = 0.0  # fraction mined


def _routes(result: PitResult) -> int:
    return max(int(result.route.max(initial=1)), 1)


def _increments(
    result: PitResult, phase: Ints, grade_bins: tuple[float, ...] = ()
) -> list[_Increment]:
    out: list[_Increment] = []
    inside = np.flatnonzero(phase > 0)
    keys = np.stack([phase[inside], result.bench[inside]], axis=1)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    inside, keys = inside[order], keys[order]
    if len(inside) == 0:
        return out
    cut = np.flatnonzero(np.any(np.diff(keys, axis=0) != 0, axis=1)) + 1
    n_routes = _routes(result)
    n_bins = len(grade_bins) + 1
    bin_of = np.searchsorted(np.asarray(grade_bins, np.float64), result.feed_grade, side="right")
    for blocks in np.split(inside, cut):
        ore = blocks[result.ore[blocks]]
        t = result.tonnes
        by_route = np.zeros((n_routes, n_bins, 5))
        route_of = np.where(result.route[ore] > 0, result.route[ore], 1)
        for r in range(n_routes):
            for b in range(n_bins):
                sel = ore[(route_of == r + 1) & (bin_of[ore] == b)]
                by_route[r, b] = (
                    result.feed[sel].sum(),
                    (result.value[sel] + result.mining[sel]).sum(),
                    result.metal[sel].sum(),
                    (result.feed[sel] * result.feed_grade[sel]).sum(),
                    result.revenue[sel].sum(),
                )
        out.append(
            _Increment(
                int(phase[blocks[0]]),
                int(result.bench[blocks[0]]),
                blocks,
                float(t[blocks].sum()),
                float(result.feed[ore].sum()),
                float((result.value[ore] + result.mining[ore]).sum()),
                float(result.mining[blocks].sum()),
                float(result.metal[ore].sum()),
                float((result.feed[ore] * result.feed_grade[ore]).sum()),
                by_route,
            )
        )
    return out


def schedule_constrained(  # noqa: PLR0915 - one simulation loop
    result: PitResult, phase: Ints, limits: Limits
) -> ConstrainedSchedule:
    incs = _increments(result, phase, limits.grade_bins)
    phases = sorted({i.phase for i in incs})
    by_phase = {p: [i for i in incs if i.phase == p] for p in phases}
    finished_benches = dict.fromkeys(phases, -1)  # deepest fully mined bench index
    period_of = np.zeros(len(phase), np.int64)
    n_routes, n_bins = _routes(result), len(limits.grade_bins) + 1
    plant = _per_route([limits.plant_capacity, *limits.route_capacities], n_routes)
    room = _per_route(list(limits.stockpile_capacities), n_routes)
    stock = np.zeros((n_routes, n_bins, 5))  # ore t, margin, metal, grade t, revenue
    rows: list[PeriodRow] = []
    for period in range(1, limits.max_periods + 1):
        capacity = limits.mining_capacity
        mined = ore_mined = 0.0
        cost = 0.0
        fresh = np.zeros((n_routes, n_bins, 5))
        touched: dict[int, set[int]] = {p: set() for p in phases}
        progress = True
        while capacity > 1e-9 and progress:
            progress = False
            for k, p in enumerate(phases):
                queue = [i for i in by_phase[p] if i.done < 1.0]
                if not queue:
                    continue
                inc = queue[0]
                if inc.bench not in touched[p] and len(touched[p]) >= limits.sinking_rate:
                    continue
                if k > 0:
                    prev = phases[k - 1]
                    prev_done = all(i.done >= 1.0 for i in by_phase[prev])
                    if not prev_done and inc.bench > finished_benches[prev] - limits.bench_lag:
                        continue
                touched[p].add(inc.bench)
                remaining = (1.0 - inc.done) * inc.tonnes
                take = min(remaining, capacity)
                frac = take / inc.tonnes if inc.tonnes > 0 else 1.0 - inc.done
                if inc.done == 0.0:
                    period_of[inc.blocks] = period
                inc.done = min(1.0, inc.done + frac)
                if inc.done >= 1.0 - 1e-12:
                    inc.done = 1.0
                    finished_benches[p] = inc.bench
                capacity -= take
                mined += take
                cost += frac * inc.mining
                ore_mined += frac * inc.ore_t
                fresh += frac * inc.by_route
                progress = True
                break  # re-evaluate from the first pushback
        fed, reclaimed, discarded = _feed(fresh, stock, plant, room)
        feed, margin, metal, grade_t, revenue = (float(v) for v in fed.sum(axis=0))
        if mined <= 1e-9 and feed <= 1e-9:
            break
        rehandle = limits.rehandle_cost * reclaimed
        cost += rehandle
        cash = margin - cost
        rows.append(
            PeriodRow(
                period,
                mined,
                ore_mined,
                feed,
                grade_t / feed if feed > 0 else math.nan,
                metal,
                float(stock[:, :, 0].sum()),
                cash,
                cash / (1.0 + limits.discount_rate) ** period,
                tuple(float(v) for v in fed[:, 0]),
                revenue,
                revenue - margin,
                cost,
                reclaimed,
                rehandle,
                discarded,
                tuple(float(v) for v in stock[:, :, 0].sum(axis=0)),
            )
        )
    unmined = sum((1.0 - i.done) * i.tonnes for i in incs)
    return ConstrainedSchedule(tuple(rows), period_of, unmined)


def _per_route(values: list[float], n_routes: int) -> Floats:
    """One value per route; routes without one are unlimited."""
    values = values + [math.inf] * (n_routes - len(values))
    return np.asarray(values[:n_routes], np.float64)


def _feed(fresh: Floats, stock: Floats, plant: Floats, room: Floats) -> tuple[Floats, float, float]:
    """Feed each route from the highest grade bin down, fresh ore before stock
    within a bin; store the rest while there is room. ``stock`` is updated in
    place. Returns (fed per route, t reclaimed, t sent to waste)."""
    n_routes, n_bins = fresh.shape[0], fresh.shape[1]
    fed = np.zeros((n_routes, 5))
    reclaimed = discarded = 0.0
    for r in range(n_routes):
        spare = plant[r]
        left = fresh[r].copy()
        for b in range(n_bins - 1, -1, -1):
            for from_stock, source in ((False, left), (True, stock[r])):
                t = source[b, 0]
                if spare <= 0 or t <= 0:
                    continue
                k = min(1.0, spare / t)
                fed[r] += k * source[b]
                spare -= k * t
                if from_stock:
                    reclaimed += k * t
                source[b] *= 1.0 - k  # a view: updates the stockpile in place
        space = room[r] - stock[r, :, 0].sum()
        for b in range(n_bins - 1, -1, -1):
            t = left[b, 0]
            if t <= 0:
                continue
            k = min(1.0, max(space, 0.0) / t)
            stock[r, b] += k * left[b]
            space -= k * t
            discarded += (1.0 - k) * t
    return fed, reclaimed, discarded


@dataclass(frozen=True)
class InventoryRow:
    phase: int
    bench_toe: float  # elevation of the bench floor, m
    ore_t: float  # plant feed tonnes (after mining recovery and dilution)
    grade: float  # feed grade
    metal: float
    waste_t: float
    value: float


def bench_inventory(
    result: PitResult, phase: Ints, origin_z: float, bench_height: float, benches: int
) -> list[InventoryRow]:
    """Mineable inventory per pushback and bench (top bench first within a pushback)."""
    rows = []
    for inc in _increments(result, phase):
        ore = inc.ore_t
        rows.append(
            InventoryRow(
                inc.phase,
                origin_z + (benches - 1 - inc.bench) * bench_height,
                ore,
                inc.grade_t / ore if ore > 0 else math.nan,
                inc.metal,
                inc.tonnes - ore,
                float(result.value[inc.blocks].sum()),
            )
        )
    return rows
