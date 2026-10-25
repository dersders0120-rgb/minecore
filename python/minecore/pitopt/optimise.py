"""Nested pit shells, shell report, pushbacks and a simple schedule.

Shells: the ultimate pit is solved for each revenue factor (price multiplier)
in increasing order; because the solver returns the smallest maximum closure,
the pits are nested. ``shell[b]`` is the 1-based index of the first revenue
factor whose pit contains block ``b`` (0 = never mined).

Discounted cash flow uses a constant total mining rate (t/year). A block's
time is the cumulative tonnage mined before its midpoint divided by the rate;
its cash is discounted by ``(1 + rate)^-t``. *Best case* mines shell by
shell (each shell top-down); *worst case* mines the chosen pit bench by bench
over its whole extent. These bracket the value of practical schedules
(Whittle 1988).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from minecore._core import max_closure
from minecore.blockmodel import BlockModel
from minecore.jobs import Progress
from minecore.pitopt.economics import Economics, PitOptError, block_values
from minecore.pitopt.precedence import SlopeProfile, precedence_arcs

if TYPE_CHECKING:
    from minecore.pitopt.scheduling import ConstrainedSchedule

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]


@dataclass(frozen=True)
class ShellRow:
    shell: int
    revenue_factor: float
    ore_t: float
    waste_t: float
    strip_ratio: float
    grade: float
    metal: float
    cash: float
    best_npv: float
    worst_npv: float
    years: float


@dataclass(frozen=True)
class PitResult:
    shell: Ints
    value: Floats
    ore: npt.NDArray[np.bool_]
    tonnes: Floats
    metal: Floats
    grade: Floats
    bench: Ints
    revenue_factors: tuple[float, ...]
    rows: tuple[ShellRow, ...]
    arcs: int
    mining: Floats  # mining cost per block at revenue factor 1
    feed: Floats  # plant feed tonnes of ore blocks (after mining recovery, dilution)
    feed_grade: Floats  # diluted feed grade


def _discounted(order: Ints, tonnes: Floats, value: Floats, rate: float, discount: float) -> float:
    t = tonnes[order]
    mid = np.cumsum(t) - t / 2
    years = mid / rate
    return float((value[order] * (1.0 + discount) ** -years).sum())


def optimise_pit(  # noqa: PLR0913 - options are keyword-only
    model: BlockModel,
    grade_attribute: str,
    econ: Economics,
    slope: SlopeProfile,
    revenue_factors: Sequence[float],
    *,
    mining_rate: float = 10e6,
    discount_rate: float = 0.08,
    max_levels: int | None = None,
    rock_attribute: str | None = None,
    progress: Progress | None = None,
) -> PitResult:
    """Nested shells for ``revenue_factors`` and their report.

    ``rock_attribute`` (1 = rock, 0 = air above topography) zeroes the tonnage,
    cost and value of air blocks.
    """
    factors = tuple(sorted(float(f) for f in revenue_factors))
    if not factors or factors[0] <= 0:
        raise PitOptError("revenue factors must be > 0")
    if mining_rate <= 0 or discount_rate < 0:
        raise PitOptError("mining rate must be > 0 and discount rate >= 0")
    grid = model.grid
    nx, ny, nz = grid.count
    grade = model.values(grade_attribute)
    unit = model.attributes[grade_attribute].get("unit")
    if isinstance(econ.density, str):
        rho = model.values(econ.density)
        rho = np.where(np.isfinite(rho) & (rho > 0), rho, np.nan)
        if np.isnan(rho).any():
            raise PitOptError(f"density attribute {econ.density!r} has missing values")
    else:
        rho = np.full(grid.blocks, float(econ.density))
    tonnes = rho * grid.block_volume
    if rock_attribute is not None:
        rock = np.nan_to_num(model.values(rock_attribute), nan=1.0)
        tonnes = tonnes * np.clip(rock, 0.0, 1.0)
    k = np.arange(grid.blocks) // (nx * ny)
    bench = (nz - 1 - k).astype(np.int64)
    base = block_values(grade, unit, tonnes, bench, econ)
    if progress is not None:
        progress.update(0.02, "eğim kısıtları")
    arcs = precedence_arcs(grid, slope, max_levels)
    shell = np.zeros(grid.blocks, np.int64)
    for n, rf in enumerate(factors, start=1):
        econ_rf = block_values(grade, unit, tonnes, bench, econ, rf)
        cents = np.round(econ_rf.value * 100.0).astype(np.int64)
        pit = np.asarray(max_closure(cents, arcs), bool)
        shell[pit & (shell == 0)] = n
        if progress is not None:
            progress.update(0.05 + 0.85 * n / len(factors), f"kabuk {n}/{len(factors)}")
    clean_grade = np.where(np.isfinite(grade), grade, 0.0)
    best_order = np.lexsort((-bench, np.where(shell > 0, shell, len(factors) + 1)))
    rows = []
    for n, rf in enumerate(factors, start=1):
        inside = (shell > 0) & (shell <= n)
        ore = inside & base.ore
        ore_t = float(base.feed[ore].sum())
        total = float(tonnes[inside].sum())
        waste_t = total - ore_t
        best = _discounted(
            best_order[: int(inside.sum())], tonnes, base.value, mining_rate, discount_rate
        )
        idx = np.flatnonzero(inside)
        worst_order = idx[np.argsort(bench[idx], kind="stable")]
        worst = _discounted(worst_order, tonnes, base.value, mining_rate, discount_rate)
        rows.append(
            ShellRow(
                n,
                rf,
                ore_t,
                waste_t,
                waste_t / ore_t if ore_t > 0 else math.inf,
                float((base.feed[ore] * base.feed_grade[ore]).sum() / ore_t)
                if ore_t > 0
                else math.nan,
                float(base.metal[ore].sum()),
                float(base.value[inside].sum()),
                best,
                worst,
                total / mining_rate,
            )
        )
    if progress is not None:
        progress.update(1.0, "tamam")
    return PitResult(
        shell,
        base.value,
        base.ore,
        tonnes,
        base.metal,
        clean_grade,
        bench,
        factors,
        tuple(rows),
        len(arcs),
        base.mining,
        base.feed,
        base.feed_grade,
    )


def pushbacks(result: PitResult, final_shell: int, min_ore_t: float) -> Ints:
    """Group shells ``1..final_shell`` into phases of at least ``min_ore_t`` ore.

    Returns the phase of each block (0 = outside the final pit). A small
    remainder (less than half the target) joins the last phase.
    """
    if not 1 <= final_shell <= len(result.revenue_factors):
        raise PitOptError("final shell out of range")
    phase_of_shell = np.zeros(final_shell + 1, np.int64)
    phase, acc = 1, 0.0
    for n in range(1, final_shell + 1):
        sel = (result.shell == n) & result.ore
        acc += float(result.tonnes[sel].sum())
        phase_of_shell[n] = phase
        if acc >= min_ore_t and n < final_shell:
            phase, acc = phase + 1, 0.0
    last = phase_of_shell[final_shell]
    if last > 1 and acc < 0.5 * min_ore_t:
        phase_of_shell[phase_of_shell == last] = last - 1
    inside = (result.shell > 0) & (result.shell <= final_shell)
    out = np.zeros(len(result.shell), np.int64)
    out[inside] = phase_of_shell[result.shell[inside]]
    return out


@dataclass(frozen=True)
class SchedulePeriod:
    period: int
    ore_t: float
    waste_t: float
    grade: float
    metal: float
    cash: float
    discounted: float


@dataclass(frozen=True)
class Schedule:
    period: Ints
    rows: tuple[SchedulePeriod, ...]

    @property
    def npv(self) -> float:
        return sum(r.discounted for r in self.rows)


def schedule(result: PitResult, phase: Ints, mining_rate: float, discount_rate: float) -> Schedule:
    """Mine phases in order, each bench by bench top-down, at a fixed total rate.

    A block belongs to the year in which its mining starts. Cash flows are
    discounted at the end of each year. No bench lag between phases, no
    processing limit and no stockpiles: a first-pass schedule, not a plan.
    """
    if mining_rate <= 0:
        raise PitOptError("mining rate must be > 0")
    idx = np.flatnonzero(phase > 0)
    order = idx[np.lexsort((result.bench[idx], phase[idx]))]
    t = result.tonnes[order]
    start = np.cumsum(t) - t
    per = (np.floor(start / mining_rate + 1e-9) + 1).astype(np.int64)
    period = np.zeros(len(phase), np.int64)
    period[order] = per
    rows = []
    for p in range(1, int(per.max(initial=0)) + 1):
        sel = period == p
        ore = sel & result.ore
        ore_t = float(result.feed[ore].sum())
        cash = float(result.value[sel].sum())
        rows.append(
            SchedulePeriod(
                p,
                ore_t,
                float(result.tonnes[sel].sum()) - ore_t,
                float((result.feed[ore] * result.feed_grade[ore]).sum() / ore_t)
                if ore_t > 0
                else math.nan,
                float(result.metal[ore].sum()),
                cash,
                cash / (1.0 + discount_rate) ** p,
            )
        )
    return Schedule(period, tuple(rows))


def store(  # noqa: PLR0913, PLR0917 - optional result parts
    model: BlockModel,
    result: PitResult,
    econ: Economics,
    slope: SlopeProfile,
    phase: Ints | None = None,
    sched: Schedule | ConstrainedSchedule | None = None,
) -> None:
    """Write pit attributes to the block model in one undoable operation."""
    params: dict[str, Any] = {
        "economics": econ.to_json(),
        "slope": {"angle": slope.angle, "sectors": [list(s) for s in slope.sectors]},
        "revenue_factors": list(result.revenue_factors),
    }
    values = {
        "pit_value": result.value,
        "pit_shell": result.shell.astype(np.float64),
    }
    info: dict[str, dict[str, Any]] = {
        "pit_value": {"unit": None, **params},
        "pit_shell": {"unit": None},
    }
    if phase is not None:
        values["pit_phase"] = phase.astype(np.float64)
        info["pit_phase"] = {"unit": None}
    if sched is not None:
        values["pit_period"] = sched.period.astype(np.float64)
        info["pit_period"] = {"unit": None}
    model.set_attributes(values, info, "Pit optimisation")
