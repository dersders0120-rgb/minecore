"""NPV-maximising MILP schedule: hand-solved cases, never worse than the greedy one,
and the sequencing rules hold."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.milp import schedule_optimised
from minecore.pitopt.optimise import PitResult
from minecore.pitopt.scheduling import Limits, parse_blends, schedule_constrained

PRICE, PROCESS, MINE = 100.0, 20.0, 2.0


def _pit(
    grades: list[float], phases: list[int] | None = None, benches: list[int] | None = None
) -> tuple[PitResult, np.ndarray]:
    """Blocks of 1000 t, one per bench and pushback; ore when the margin is positive."""
    n = len(grades)
    g = np.asarray(grades, np.float64)
    t = np.full(n, 1000.0)
    revenue = g * PRICE * t
    mining = np.full(n, MINE * 1000.0)
    margin = revenue - PROCESS * t
    ore = margin > 0
    pit = PitResult(
        shell=np.ones(n, np.int64),
        value=np.where(ore, margin, 0.0) - mining,
        ore=ore,
        tonnes=t,
        metal=np.where(ore, g * t, 0.0),
        grade=g,
        bench=np.asarray(benches if benches is not None else range(n), np.int64),
        revenue_factors=(1.0,),
        rows=(),
        arcs=0,
        mining=mining,
        feed=np.where(ore, t, 0.0),
        feed_grade=g,
        route=np.where(ore, 1, 0).astype(np.int64),
        revenue=np.where(ore, revenue, 0.0),
        coproduct_metal=np.zeros((n, 0)),
    )
    phase = np.asarray(phases if phases is not None else [1] * n, np.int64)
    return pit, phase


def _limits(**kw: object) -> Limits:
    base: dict[str, object] = {
        "mining_capacity": 2000.0,
        "plant_capacity": 1000.0,
        "bench_lag": 0,
        "sinking_rate": 50,
        "discount_rate": 0.1,
    }
    return Limits(**{**base, **kw})  # type: ignore[arg-type]


def test_two_benches_solved_by_hand() -> None:
    # Bench 0: 3.0 (margin 280 000), bench 1: 1.0 (margin 80 000); mining 2 000 each;
    # plant 1 000 t/period. Options (discount 10 %):
    #   both mined in period 1, the 1.0 ore stockpiled: 276 000/1.1 + 80 000/1.21 = 317 025
    #   one bench per period:                         278 000/1.1 + 78 000/1.21 = 317 190
    # The optimum defers the second bench's mining cost.
    pit, phase = _pit([3.0, 1.0])
    s = schedule_optimised(pit, phase, _limits())
    assert s.method == "milp"
    assert s.npv == pytest.approx(278_000 / 1.1 + 78_000 / 1.21, rel=1e-9)
    assert [r.mined_t for r in s.rows] == pytest.approx([1000.0, 1000.0])
    # The greedy schedule mines at capacity and blends the pile: 176 000/1.1 + 180 000/1.21.
    greedy = schedule_constrained(pit, phase, _limits())
    assert greedy.npv == pytest.approx(176_000 / 1.1 + 180_000 / 1.21)
    assert s.greedy_npv == pytest.approx(greedy.npv)


def test_high_grade_is_reclaimed_first_by_value() -> None:
    # Bench 0 waste-free low grade, bench 1 high grade, both in period 1 (the high grade
    # sits below): the plant takes the high grade first and the low grade waits.
    pit, phase = _pit([1.0, 3.0])
    s = schedule_optimised(pit, phase, _limits(rehandle_cost=0.5))
    assert s.rows[0].feed_grade == pytest.approx(3.0)
    assert s.rows[1].reclaimed_t == pytest.approx(1000.0)
    assert s.rows[1].rehandle == pytest.approx(500.0)
    expected = (280_000 - 4000) / 1.1 + (80_000 - 500) / 1.21
    assert s.npv == pytest.approx(expected, rel=1e-9)


def test_mine_all_or_leave_an_uneconomic_bottom() -> None:
    pit, phase = _pit([3.0, 0.0])
    forced = schedule_optimised(pit, phase, _limits())
    assert forced.unmined_t == pytest.approx(0.0)
    free = schedule_optimised(pit, phase, _limits(), mine_all=False)
    assert free.unmined_t == pytest.approx(1000.0)
    assert free.npv == pytest.approx(278_000 / 1.1)
    assert free.npv > forced.npv
    assert free.period[1] == 0  # the waste bench is never started


def test_never_worse_than_greedy_and_rules_hold() -> None:
    rng = np.random.default_rng(11)
    benches = [b for _ in range(2) for b in range(6)]
    phases = [1] * 6 + [2] * 6
    grades = list(rng.uniform(0.0, 4.0, 12))
    pit, phase = _pit(grades, phases, benches)
    limits = _limits(mining_capacity=2500.0, plant_capacity=900.0, bench_lag=1, sinking_rate=2)
    s = schedule_optimised(pit, phase, limits)
    greedy = schedule_constrained(pit, phase, limits)
    assert s.method == "milp"
    assert s.npv >= greedy.npv - 1e-6
    assert s.unmined_t == pytest.approx(0.0)
    for r in s.rows:
        assert r.mined_t <= 2500.0 * (1 + 1e-6)
        assert r.ore_fed_t <= 900.0 * (1 + 1e-6)
        assert r.revenue - r.processing - r.mining_cost == pytest.approx(r.cash, abs=1e-3)
    start = s.period
    # Sinking rate 2: bench b of a pushback starts no earlier than period floor(b / 2) + 1.
    for k in range(12):
        assert start[k] >= benches[k] // 2 + 1
    # Bench lag 1: pushback 2 starts bench b only after pushback 1 finished bench b + 1,
    # which itself started no later than that.
    for b in range(5):
        assert start[6 + b] >= start[b + 1]
    total_ore = float(pit.feed.sum())
    fed = sum(r.ore_fed_t for r in s.rows)
    assert fed + s.rows[-1].stockpile_t + sum(r.discarded_t for r in s.rows) == pytest.approx(
        total_ore
    )


def test_validation() -> None:
    pit, phase = _pit([3.0])
    with pytest.raises(PitOptError):
        schedule_optimised(pit, phase, _limits(), time_limit=0.0)
    with pytest.raises(PitOptError):
        schedule_optimised(pit, np.zeros(1, np.int64), _limits())


def test_blending_bounds_hold_every_period() -> None:
    pit, phase = _pit([3.0, 1.0, 1.0, 3.0])
    s = schedule_optimised(
        pit, phase, _limits(plant_capacity=2000.0, blends=parse_blends("grade:1.5:2.0"))
    )
    assert s.method == "milp"
    fed = [r for r in s.rows if r.ore_fed_t > 1e-6]
    assert fed
    assert all(1.5 - 1e-6 <= r.blend_values[0] <= 2.0 + 1e-6 for r in fed)
    assert all(r.feed_grade == pytest.approx(r.blend_values[0]) for r in fed)


def test_half_year_periods_scale_capacity_and_discounting() -> None:
    pit, phase = _pit([3.0, 1.0])
    s = schedule_constrained(pit, phase, _limits(period_years=(0.5,)))
    # 2000 t/year x 0.5; the plant (1000 t/year) feeds 500 t per period, the rest waits
    assert [r.mined_t for r in s.rows][:2] == pytest.approx([1000.0, 1000.0])
    assert [r.ore_fed_t for r in s.rows] == pytest.approx([500.0] * 4)
    assert [r.years for r in s.rows] == pytest.approx([0.5, 1.0, 1.5, 2.0])
    first = s.rows[0]
    assert first.discounted == pytest.approx(first.cash / 1.1**0.5)
    m = schedule_optimised(pit, phase, _limits(period_years=(0.5,)))
    assert m.rows[0].years == pytest.approx(0.5)
