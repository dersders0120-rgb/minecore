"""Stockpile grade bins, capacity and rehandling in the constrained schedule, by hand."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult
from minecore.pitopt.scheduling import Limits, schedule_constrained

PRICE, PROCESS, MINE = 100.0, 20.0, 2.0  # revenue per grade unit and tonne; costs per tonne


def _pit(grades: list[float], tonnes: float = 1000.0) -> PitResult:
    """One pushback, one block per bench (top first), every block ore."""
    n = len(grades)
    g = np.asarray(grades, np.float64)
    t = np.full(n, tonnes)
    revenue = g * PRICE * t
    mining = np.full(n, MINE * tonnes)
    value = revenue - PROCESS * t - mining
    return PitResult(
        shell=np.ones(n, np.int64),
        value=value,
        ore=np.ones(n, bool),
        tonnes=t,
        metal=g * t,
        grade=g,
        bench=np.arange(n, dtype=np.int64),
        revenue_factors=(1.0,),
        rows=(),
        arcs=0,
        mining=mining,
        feed=t.copy(),
        feed_grade=g,
        route=np.ones(n, np.int64),
        revenue=revenue,
        coproduct_metal=np.zeros((n, 0)),
    )


def _limits(**kw: object) -> Limits:
    base: dict[str, object] = {
        "mining_capacity": 2000.0,
        "plant_capacity": 1000.0,
        "bench_lag": 0,
        "sinking_rate": 50,
        "discount_rate": 0.0,
    }
    return Limits(**{**base, **kw})  # type: ignore[arg-type]


PHASE1 = np.ones(4, np.int64)


def test_bins_feed_high_grade_and_stockpile_low_grade() -> None:
    pit = _pit([3.0, 1.0, 1.2, 2.5])
    blended = schedule_constrained(pit, PHASE1, _limits())
    binned = schedule_constrained(pit, PHASE1, _limits(grade_bins=(2.0,)))
    # One blended pile: period 2 feeds half of each fresh block, (1.2 + 2.5) / 2.
    assert blended.rows[1].feed_grade == pytest.approx(1.85)
    # Bins: period 1 feeds the 3.0 block, period 2 the fresh 2.5 block; the 1.0 and 1.2
    # blocks wait on the low-grade pile and are reclaimed in periods 3 and 4.
    assert [r.feed_grade for r in binned.rows] == pytest.approx([3.0, 2.5, 1.1, 1.1])
    assert binned.rows[1].stock_by_bin == pytest.approx((2000.0, 0.0))
    assert [r.reclaimed_t for r in binned.rows] == pytest.approx([0.0, 0.0, 1000.0, 1000.0])
    assert sum(r.metal for r in binned.rows) == pytest.approx(pit.metal.sum())


def test_high_grade_stock_is_reclaimed_before_low_grade_fresh_ore() -> None:
    pit = _pit([3.0, 3.0, 1.0])
    phase = np.ones(3, np.int64)
    s = schedule_constrained(
        pit, phase, _limits(mining_capacity=2000.0, grade_bins=(2.0,), rehandle_cost=1.5)
    )
    # Period 1 mines both 3.0 blocks: one fed, one stockpiled (high bin).
    # Period 2 mines the 1.0 block: the plant takes the 3.0 stock, the 1.0 ore is stored.
    assert s.rows[1].feed_grade == pytest.approx(3.0)
    assert s.rows[1].reclaimed_t == pytest.approx(1000.0)
    assert s.rows[1].rehandle == pytest.approx(1500.0)
    assert s.rows[1].stock_by_bin == pytest.approx((1000.0, 0.0))
    assert s.rows[2].feed_grade == pytest.approx(1.0)
    for r in s.rows:
        assert r.revenue - r.processing - r.mining_cost == pytest.approx(r.cash)
    # Rehandling: 2000 t reclaimed at 1.5 per tonne.
    total_margin = float((pit.value + pit.mining).sum() - pit.mining.sum())
    assert sum(r.cash for r in s.rows) == pytest.approx(total_margin - 2000 * 1.5)


def test_a_full_stockpile_sends_ore_to_waste() -> None:
    pit = _pit([3.0, 3.0, 3.0, 3.0])
    s = schedule_constrained(pit, PHASE1, _limits(stockpile_capacities=(500.0,)))
    # Period 1: 2000 t ore, 1000 fed, 500 stored, 500 to waste; period 2 the same, with
    # the 500 t pile full: 1000 fresh fed, 1000 left, no room -> to waste, then nothing
    # is reclaimed because the plant is full of fresh ore. Period 3 reclaims the pile.
    assert [r.discarded_t for r in s.rows] == pytest.approx([500.0, 1000.0, 0.0])
    assert [r.ore_fed_t for r in s.rows] == pytest.approx([1000.0, 1000.0, 500.0])
    fed = sum(r.ore_fed_t for r in s.rows)
    lost = sum(r.discarded_t for r in s.rows)
    assert fed + lost + s.rows[-1].stockpile_t == pytest.approx(pit.feed.sum())
    unlimited = schedule_constrained(pit, PHASE1, _limits())
    assert s.npv < unlimited.npv
    assert unlimited.rows[-1].discarded_t == 0.0


def test_bins_above_every_grade_change_nothing() -> None:
    rng = np.random.default_rng(3)
    pit = _pit(list(rng.uniform(0.5, 4.0, 30)))
    phase = np.where(np.arange(30) < 15, 1, 2).astype(np.int64)
    plain = schedule_constrained(pit, phase, _limits(mining_capacity=3000.0))
    edges = schedule_constrained(pit, phase, _limits(mining_capacity=3000.0, grade_bins=(1e9,)))
    assert [r.cash for r in plain.rows] == pytest.approx([r.cash for r in edges.rows])
    binned = schedule_constrained(
        pit, phase, _limits(mining_capacity=3000.0, grade_bins=(1.5, 2.5, 3.2))
    )
    assert sum(r.ore_fed_t for r in binned.rows) == pytest.approx(pit.feed.sum())
    assert sum(r.metal for r in binned.rows) == pytest.approx(pit.metal.sum())
    # Feeding high grade first can only bring metal forward.
    early = np.cumsum([r.metal for r in binned.rows])
    late = np.cumsum([r.metal for r in plain.rows])
    n = min(len(early), len(late))
    assert (early[:n] >= late[:n] - 1e-6).all()


def test_validation() -> None:
    with pytest.raises(PitOptError):
        _limits(grade_bins=(2.0, 1.0))
    with pytest.raises(PitOptError):
        _limits(stockpile_capacities=(-1.0,))
    with pytest.raises(PitOptError):
        _limits(rehandle_cost=-1.0)
