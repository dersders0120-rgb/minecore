"""Development schedule against a hand-built layout."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.underground.development import DeclineDesign, Development, LevelAccess
from minecore.underground.devschedule import DevRates, schedule_development
from minecore.underground.economics import StopeValue, schedule_stopes
from minecore.underground.stopes import Stope


def _dev() -> Development:
    # Straight 1200 m decline; level at 600 m along (z -60) and at the end (z -120).
    line = np.array([[0.0, 0, 0], [600.0, 0, -60], [1200.0, 0, -120]])
    a1 = LevelAccess(-60.0, line[1], line[1] + [0, 300, 0], 300.0)
    a2 = LevelAccess(-120.0, line[2], line[2] + [0, 500, 0], 500.0)
    return Development(line, (a1, a2), np.zeros(2), DeclineDesign())


def test_levels_wait_for_the_decline_and_the_crew() -> None:
    length = float(np.linalg.norm(np.diff(_dev().decline, axis=0), axis=1).sum())
    ds = schedule_development(_dev(), DevRates(length, 400.0, 1, 0.5, 1000.0))
    upper, lower = ds.levels
    assert upper.reached == pytest.approx(0.5)
    assert upper.finish == pytest.approx(1.25)  # 300 m at 400 m/y
    assert lower.reached == pytest.approx(1.0)
    assert lower.start == pytest.approx(1.25)  # one crew: waits for the upper level
    assert lower.ready == pytest.approx(1.25 + 1.25 + 0.5)
    assert sum(y.decline_m + y.level_m for y in ds.years) == pytest.approx(length + 800.0)
    assert sum(y.cost for y in ds.years) == pytest.approx((length + 800.0) * 1000.0)


def test_stopes_start_when_their_level_is_ready() -> None:
    s = StopeValue(Stope(0, 0, 0, 1000.0, 2.0), 1000.0, 2.0, 2000.0, 100.0, 50.0, 50.0)
    sched, starts = schedule_stopes(
        [s], 2000.0, 0.0, ready_years=[2.4], extra_costs={1: 10.0, 2: 10.0}
    )
    assert starts[0][1] == 3
    assert [r.period for r in sched.rows] == [1, 2, 3]
    assert sched.rows[0].mining_cost == pytest.approx(10.0)
    assert sched.rows[2].mined_t == pytest.approx(1000.0)
