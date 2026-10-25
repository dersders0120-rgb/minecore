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
  periods with spare plant capacity reclaim from the stockpile.

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


@dataclass(frozen=True)
class Limits:
    mining_capacity: float  # t per period
    plant_capacity: float  # ore t per period
    bench_lag: int = 2
    sinking_rate: int = 6  # max benches a pushback may advance per period
    discount_rate: float = 0.08
    max_periods: int = 200

    def __post_init__(self) -> None:
        if self.mining_capacity <= 0 or self.plant_capacity <= 0:
            raise PitOptError("mining and plant capacities must be > 0")
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
    done: float = 0.0  # fraction mined


def _increments(result: PitResult, phase: Ints) -> list[_Increment]:
    out: list[_Increment] = []
    inside = np.flatnonzero(phase > 0)
    keys = np.stack([phase[inside], result.bench[inside]], axis=1)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    inside, keys = inside[order], keys[order]
    if len(inside) == 0:
        return out
    cut = np.flatnonzero(np.any(np.diff(keys, axis=0) != 0, axis=1)) + 1
    for blocks in np.split(inside, cut):
        ore = blocks[result.ore[blocks]]
        t = result.tonnes
        out.append(
            _Increment(
                int(phase[blocks[0]]),
                int(result.bench[blocks[0]]),
                blocks,
                float(t[blocks].sum()),
                float(t[ore].sum()),
                float((result.value[ore] + result.mining[ore]).sum()),
                float(result.mining[blocks].sum()),
                float(result.metal[ore].sum()),
                float((t[ore] * result.grade[ore]).sum()),
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
    stock_t = stock_margin = stock_metal = stock_grade_t = 0.0
    rows: list[PeriodRow] = []
    for period in range(1, limits.max_periods + 1):
        capacity = limits.mining_capacity
        mined = ore_mined = 0.0
        cost = 0.0
        fresh_t = fresh_margin = fresh_metal = fresh_grade_t = 0.0
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
                fresh_t += frac * inc.ore_t
                fresh_margin += frac * inc.margin
                fresh_metal += frac * inc.metal
                fresh_grade_t += frac * inc.grade_t
                progress = True
                break  # re-evaluate from the first pushback
        # Plant: fresh ore first, surplus to stockpile, spare capacity reclaims.
        plant = limits.plant_capacity
        feed = min(fresh_t, plant)
        share = feed / fresh_t if fresh_t > 0 else 0.0
        margin = share * fresh_margin
        metal = share * fresh_metal
        grade_t = share * fresh_grade_t
        surplus = 1.0 - share
        stock_t += surplus * fresh_t
        stock_margin += surplus * fresh_margin
        stock_metal += surplus * fresh_metal
        stock_grade_t += surplus * fresh_grade_t
        spare = plant - feed
        if spare > 0 and stock_t > 0:
            r = min(1.0, spare / stock_t)
            feed += r * stock_t
            margin += r * stock_margin
            metal += r * stock_metal
            grade_t += r * stock_grade_t
            stock_t, stock_margin = stock_t * (1 - r), stock_margin * (1 - r)
            stock_metal, stock_grade_t = stock_metal * (1 - r), stock_grade_t * (1 - r)
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
                stock_t,
                cash,
                cash / (1.0 + limits.discount_rate) ** period,
            )
        )
    unmined = sum((1.0 - i.done) * i.tonnes for i in incs)
    return ConstrainedSchedule(tuple(rows), period_of, unmined)
