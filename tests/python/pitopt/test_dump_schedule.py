"""Waste goes to the nearest dump until it is full; haul is tonnage-weighted."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.pitopt.fleet import DumpSite, dump_schedule
from minecore.pitopt.scheduling import ConstrainedSchedule, PeriodRow


def _row(period: int, mined: float, ore: float) -> PeriodRow:
    return PeriodRow(period, mined, ore, ore, 1.0, 0.0, 0.0, 0.0, 0.0)


def test_nearest_dump_first_by_hand() -> None:
    sched = ConstrainedSchedule(
        (_row(1, 100.0, 20.0), _row(2, 100.0, 20.0)), np.zeros(0, np.int64), 0.0
    )
    plan = dump_schedule(
        sched, (DumpSite("far", 1000.0, 3000.0), DumpSite("near", 100.0, 1000.0)), 2000.0
    )
    assert plan[0].by_dump == (0.0, 80.0)
    assert plan[1].by_dump == (60.0, 20.0)
    # period 2: 20 t ore x 2000 + 20 t x 1000 + 60 t x 3000 over 100 t
    assert plan[1].surface_m == pytest.approx((20 * 2000 + 20 * 1000 + 60 * 3000) / 100)
