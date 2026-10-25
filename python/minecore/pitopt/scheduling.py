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
* plant capacity (ore t/period): ore beyond it goes to one blended stockpile;
  periods with spare plant capacity reclaim from the stockpile. With process
  routes (``PitResult.route``) the plant capacity is the main route's and
  ``route_capacities`` give the extra routes theirs (unlimited when not
  given); each route has its own stockpile.

Cash per period = processing margin of the ore fed (revenue - processing cost)
- mining cost of everything mined. Stockpiled ore pays its mining cost when
mined and earns its margin when reclaimed. Cash is discounted at the end of
each period. This is a deterministic greedy sequence, not an optimised
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

    def __post_init__(self) -> None:
        if self.mining_capacity <= 0 or self.plant_capacity <= 0:
            raise PitOptError("mining and plant capacities must be > 0")
        if any(not c > 0 for c in self.route_capacities):
            raise PitOptError("route capacities must be > 0")
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
    by_route: Floats  # routes x (ore t, margin, metal, grade t)
    done: float = 0.0  # fraction mined


def _routes(result: PitResult) -> int:
    return max(int(result.route.max(initial=1)), 1)


def _increments(result: PitResult, phase: Ints) -> list[_Increment]:
    out: list[_Increment] = []
    inside = np.flatnonzero(phase > 0)
    keys = np.stack([phase[inside], result.bench[inside]], axis=1)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    inside, keys = inside[order], keys[order]
    if len(inside) == 0:
        return out
    cut = np.flatnonzero(np.any(np.diff(keys, axis=0) != 0, axis=1)) + 1
    n_routes = _routes(result)
    for blocks in np.split(inside, cut):
        ore = blocks[result.ore[blocks]]
        t = result.tonnes
        by_route = np.zeros((n_routes, 4))
        for r in range(n_routes):
            sel = ore[np.where(result.route[ore] > 0, result.route[ore], 1) == r + 1]
            by_route[r] = (
                result.feed[sel].sum(),
                (result.value[sel] + result.mining[sel]).sum(),
                result.metal[sel].sum(),
                (result.feed[sel] * result.feed_grade[sel]).sum(),
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
    incs = _increments(result, phase)
    phases = sorted({i.phase for i in incs})
    by_phase = {p: [i for i in incs if i.phase == p] for p in phases}
    finished_benches = dict.fromkeys(phases, -1)  # deepest fully mined bench index
    period_of = np.zeros(len(phase), np.int64)
    n_routes = _routes(result)
    caps = [limits.plant_capacity, *limits.route_capacities]
    caps += [math.inf] * (n_routes - len(caps))
    plant = np.asarray(caps[:n_routes], np.float64)
    stock = np.zeros((n_routes, 4))  # per route: ore t, margin, metal, grade t
    rows: list[PeriodRow] = []
    for period in range(1, limits.max_periods + 1):
        capacity = limits.mining_capacity
        mined = ore_mined = 0.0
        cost = 0.0
        fresh = np.zeros((n_routes, 4))
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
        # Each route: fresh ore first, surplus to its stockpile, spare capacity reclaims.
        fed = np.zeros((n_routes, 4))
        for r in range(n_routes):
            t = fresh[r, 0]
            share = min(1.0, plant[r] / t) if t > 0 else 0.0
            fed[r] = share * fresh[r]
            stock[r] += (1.0 - share) * fresh[r]
            spare = plant[r] - fed[r, 0]
            if spare > 0 and stock[r, 0] > 0:
                k = min(1.0, spare / stock[r, 0])
                fed[r] += k * stock[r]
                stock[r] *= 1.0 - k
        feed, margin, metal, grade_t = (float(v) for v in fed.sum(axis=0))
        if mined <= 1e-9 and feed <= 1e-9:
            break
        cash = margin - cost
        rows.append(
            PeriodRow(
                period,
                mined,
                ore_mined,
                feed,
                grade_t / feed if feed > 0 else math.nan,
                metal,
                float(stock[:, 0].sum()),
                cash,
                cash / (1.0 + limits.discount_rate) ** period,
                tuple(float(v) for v in fed[:, 0]),
            )
        )
    unmined = sum((1.0 - i.done) * i.tonnes for i in incs)
    return ConstrainedSchedule(tuple(rows), period_of, unmined)


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
