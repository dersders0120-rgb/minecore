"""Period-by-period production schedule with mining and plant limits, bench
lag between pushbacks and a stockpile.

Mining units are *increments*: the blocks of one pushback on one bench.
Each period the scheduler mines increments in pushback order, top bench
first, subject to:

* total mining capacity (t/year times the period length); an increment may
  be split across periods;
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

Periods: by default every period is one year. ``period_years`` gives each
period its own length (e.g. four quarters, then years; the last length
repeats), and ``mining_rates`` / ``plant_rates`` give the capacity per year
of each period (the last rate repeats; the scalar capacity when empty).
A period's capacity is its rate times its length, and its cash is
discounted at the end of the period's time in years. The sinking rate stays
benches per period.

Blending targets (``blends``) bound the tonne-weighted mean of the main feed
grade or of any block attribute (e.g. a deleterious element) in the feed of
one route each period. Only the MILP schedule (``milp``) enforces them; the
greedy rules ignore them.

Cash per period = processing margin of the ore fed (revenue - processing cost)
- mining cost of everything mined - rehandling. Stockpiled ore pays its
mining cost when mined and earns its margin when reclaimed. Cash is
discounted at the end of each period. This is a deterministic greedy sequence, not an optimised
schedule (no MILP); it respects the constraints and is reproducible.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult

Ints = npt.NDArray[np.int64]
Floats = npt.NDArray[np.float64]


@dataclass(frozen=True, eq=False)
class Blend:
    """Feed blending target: ``lo <= mean <= hi`` per period for one route.

    ``attribute`` empty means the main feed grade (after dilution); otherwise
    ``values`` holds that attribute per block (in situ, weighted by feed
    tonnes; see ``resolve_blends``).
    """

    attribute: str = ""
    lo: float = -math.inf
    hi: float = math.inf
    route: int = 1
    values: Floats | None = None

    def __post_init__(self) -> None:
        if math.isnan(self.lo) or math.isnan(self.hi) or self.lo > self.hi:
            raise PitOptError(f"blend {self.label}: the minimum is above the maximum")
        if not (math.isfinite(self.lo) or math.isfinite(self.hi)):
            raise PitOptError(f"blend {self.label} needs a minimum or a maximum")
        if self.route < 1:
            raise PitOptError("blend route numbers start at 1")

    @property
    def label(self) -> str:
        return self.attribute or "grade"


GRADE_WORDS = {"", "grade", "tenör", "tenor", "feed"}


def parse_blends(text: str) -> tuple[Blend, ...]:
    """``"grade:0.8:1.5; as:-:500:1"`` -> blending targets (``-`` or empty = no bound).

    Each item is ``ATTRIBUTE:MIN:MAX[:ROUTE]``; ``grade`` (or ``tenör``) is the
    main feed grade. Attribute values are attached later by ``resolve_blends``.
    """
    out = []
    for item in text.split(";"):
        if not item.strip():
            continue
        parts = [v.strip() for v in item.split(":")]
        if len(parts) not in (3, 4):
            raise PitOptError(f"blend {item.strip()!r} is not ATTRIBUTE:MIN:MAX[:ROUTE]")
        try:
            lo = -math.inf if parts[1] in ("", "-") else float(parts[1])
            hi = math.inf if parts[2] in ("", "-") else float(parts[2])
            route = int(parts[3]) if len(parts) == 4 and parts[3] else 1
        except ValueError:
            raise PitOptError(f"blend {item.strip()!r} has a value that is not a number") from None
        name = "" if parts[0].lower() in GRADE_WORDS else parts[0]
        out.append(Blend(name, lo, hi, route))
    return tuple(out)


def resolve_blends(
    blends: tuple[Blend, ...], values_of: Callable[[str], Floats]
) -> tuple[Blend, ...]:
    """Attach the block values of each attribute target (``values_of`` reads an attribute)."""
    return tuple(
        b if not b.attribute else replace(b, values=np.asarray(values_of(b.attribute), np.float64))
        for b in blends
    )


def parse_periods(text: str) -> tuple[float, ...]:
    """``"0.25*4; 0.5*2; 1"`` -> (0.25, 0.25, 0.25, 0.25, 0.5, 0.5, 1.0)."""
    out: list[float] = []
    for raw in text.replace(",", ";").split(";"):
        item = raw.strip()
        if not item:
            continue
        value, _, count = item.partition("*")
        try:
            out += [float(value)] * (int(count) if count.strip() else 1)
        except ValueError:
            raise PitOptError(f"period list item {item!r} is not VALUE or VALUE*COUNT") from None
    return tuple(out)


def _pick(values: tuple[float, ...], period: int, default: float) -> float:
    if not values:
        return default
    return values[min(period, len(values)) - 1]


@dataclass(frozen=True)
class Limits:
    mining_capacity: float  # t per year
    plant_capacity: float  # ore t per year
    bench_lag: int = 2
    sinking_rate: int = 6  # max benches a pushback may advance per period
    discount_rate: float = 0.08
    max_periods: int = 200
    route_capacities: tuple[float, ...] = ()  # ore t per period of routes 2, 3, ...
    grade_bins: tuple[float, ...] = ()  # ascending feed-grade edges of the stockpile bins
    stockpile_capacities: tuple[float, ...] = ()  # t per route (main first); unlimited if absent
    rehandle_cost: float = 0.0  # per tonne reclaimed from a stockpile
    period_years: tuple[float, ...] = ()  # length of each period; the last repeats; empty = 1
    mining_rates: tuple[float, ...] = ()  # t/year by period; the last repeats
    plant_rates: tuple[float, ...] = ()  # main-route ore t/year by period; the last repeats
    blends: tuple[Blend, ...] = ()  # feed blending targets (MILP schedule only)

    def length(self, period: int) -> float:
        """Length of a period (1-based) in years."""
        return _pick(self.period_years, period, 1.0)

    def end_years(self, period: int) -> float:
        """Time at the end of a period (1-based), years from the start of mining."""
        if not self.period_years:
            return float(period)
        return float(sum(self.length(t) for t in range(1, period + 1)))

    def mining_cap(self, period: int) -> float:
        return _pick(self.mining_rates, period, self.mining_capacity) * self.length(period)

    def plant_caps(self, period: int, n_routes: int) -> Floats:
        main = _pick(self.plant_rates, period, self.plant_capacity)
        return _per_route([main, *self.route_capacities], n_routes) * self.length(period)

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
        if any(not (math.isfinite(v) and v > 0) for v in self.period_years):
            raise PitOptError("period lengths must be > 0 years")
        if any(not (math.isfinite(v) and v > 0) for v in (*self.mining_rates, *self.plant_rates)):
            raise PitOptError("capacities by period must be > 0")


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
    years: float = math.nan  # time at the end of the period, years (NaN: = period)
    blend_values: tuple[float, ...] = ()  # feed mean of each blending target (MILP)


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
    room = _per_route(list(limits.stockpile_capacities), n_routes)
    stock = np.zeros((n_routes, n_bins, 5))  # ore t, margin, metal, grade t, revenue
    rows: list[PeriodRow] = []
    for period in range(1, limits.max_periods + 1):
        capacity = limits.mining_cap(period)
        plant = limits.plant_caps(period, n_routes)
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
                cash / (1.0 + limits.discount_rate) ** limits.end_years(period),
                tuple(float(v) for v in fed[:, 0]),
                revenue,
                revenue - margin,
                cost,
                reclaimed,
                rehandle,
                discarded,
                tuple(float(v) for v in stock[:, :, 0].sum(axis=0)),
                limits.end_years(period),
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
