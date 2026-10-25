"""Shortest haul route on a small hand-built network."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.haulroads import HaulRoadError, RoadNetwork
from minecore.strings import String


def _s(*pts: tuple[float, float, float]) -> String:
    return String(np.asarray(pts, np.float64), False)


def test_takes_the_shorter_branch_and_splits_ramp_and_flat() -> None:
    # Ramp 0 -> (100, 0, 10) at 10 %, then two roads to (300, 0, 10): direct 200 m
    # or a detour through (200, 200, 10).
    ramp = _s((0, 0, 0), (100, 0, 10))
    direct = _s((100, 0, 10), (300, 0, 10))
    detour = _s((100, 0, 10), (200, 200, 10), (300, 0, 10))
    r = RoadNetwork([ramp, direct, detour]).route((0, 0, 0), (300, 0, 10))
    assert r.length == pytest.approx(200.0 + np.hypot(100, 10))
    assert r.ramp_m == pytest.approx(np.hypot(100, 10))
    assert r.flat_m == pytest.approx(200.0)
    assert r.climb == pytest.approx(10.0)
    assert r.travel_minutes(10.0, 30.0) == pytest.approx(
        60 * (np.hypot(100, 10) / 10 + 200 / 30) / 1000
    )


def test_unconnected_roads_are_reported() -> None:
    net = RoadNetwork([_s((0, 0, 0), (10, 0, 0)), _s((50, 0, 0), (60, 0, 0))])
    with pytest.raises(HaulRoadError):
        net.route((0, 0, 0), (60, 0, 0))
