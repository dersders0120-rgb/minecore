"""Decline and level access layout against its geometric rules."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.underground.development import DeclineDesign, design_development
from minecore.underground.stopes import StopeError


def _ore() -> np.ndarray:
    # Steep tabular orebody: x 0..40, y 0..10, z -200..-100 on 5 m blocks.
    g = np.mgrid[2.5:40:5, 2.5:10:5, -197.5:-100:5].reshape(3, -1).T
    return g.astype(float)


@pytest.mark.parametrize("clockwise", [True, False])
def test_decline_rules(clockwise: bool) -> None:
    design = DeclineDesign(gradient=1 / 7, radius=25.0, standoff=40.0, clockwise=clockwise)
    levels = [-117.5, -142.5, -167.5, -192.5]
    dev = design_development(_ore(), (300.0, 5.0, 0.0), levels, design)
    line = dev.decline
    seg = np.diff(line, axis=0)
    run = np.linalg.norm(seg[:, :2], axis=1)
    # Constant gradient everywhere, from the portal down to the deepest level.
    assert np.allclose(-seg[:, 2] / run, 1 / 7, rtol=1e-3)  # chords of the arc
    assert line[0].tolist() == [300.0, 5.0, 0.0]
    assert line[-1, 2] == pytest.approx(-192.5)
    # Smooth: no kink above 10 degrees between consecutive segments (incl. leg -> spiral).
    heading = np.arctan2(seg[:, 1], seg[:, 0])
    turn = np.abs(np.angle(np.exp(1j * np.diff(heading))))
    assert np.degrees(turn).max() < 10.0
    # Turning direction: clockwise = negative turn in plan.
    assert (np.sign(np.angle(np.exp(1j * np.diff(heading)))).sum() < 0) == clockwise
    # Spiral part at the design radius and clear of the orebody by the standoff.
    spiral = np.linalg.norm(line[:, :2] - dev.centre, axis=1)
    assert spiral[-50:] == pytest.approx(25.0)
    ore = _ore()
    gap = np.linalg.norm(line[:, None, :2] - ore[None, :, :2], axis=2).min()
    assert gap >= 40.0 - 1e-6
    # One horizontal crosscut per level, ending on an ore block of that level.
    assert [a.elevation for a in dev.accesses] == sorted(levels, reverse=True)
    for a in dev.accesses:
        assert a.start[2] == a.end[2] == a.elevation
        assert np.isclose(ore, a.end).all(axis=1).any()
        assert a.length == pytest.approx(np.linalg.norm(a.end - a.start))
        assert a.length >= 40.0 - 1e-6
    assert dev.volume == pytest.approx(dev.total_length * 5.0 * 5.5)


def test_validation() -> None:
    with pytest.raises(StopeError):
        DeclineDesign(gradient=0.5)
    with pytest.raises(StopeError):
        design_development(_ore(), (0.0, 0.0, -500.0), [-150.0], DeclineDesign())
