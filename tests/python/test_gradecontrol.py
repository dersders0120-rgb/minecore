"""Grade control dig lines on a synthetic blast with a known ore/waste contact."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.gradecontrol import grade_control


def test_contact_at_known_position() -> None:
    outline = np.array([[0, 0, 100], [60, 0, 100], [60, 40, 100], [0, 40, 100]], float)
    gx, gy = np.meshgrid(np.arange(2.5, 60, 5.0), np.arange(2.5, 40, 5.0))
    grade = np.where(gx > 30, 2.0, 0.2)
    holes = np.column_stack([gx.ravel(), gy.ravel(), grade.ravel()])
    gc = grade_control(outline, holes, (0.5,), bench_height=10, density=2.5, cell=1.0, smu=5)
    waste, ore = gc.rows
    assert ore.area == pytest.approx(30 * 40, rel=0.05)
    assert ore.tonnes == pytest.approx(ore.area * 10 * 2.5)
    assert ore.grade > 1.5
    assert waste.grade < 0.5
    assert {c for c, _ in gc.lines} == {0, 1}
    assert all(np.allclose(line[:, 2], 100.0) for _, line in gc.lines)


def test_smoothing_removes_a_single_rich_hole() -> None:
    outline = np.array([[0, 0, 0], [40, 0, 0], [40, 40, 0], [0, 40, 0]], float)
    gx, gy = np.meshgrid(np.arange(2.5, 40, 5.0), np.arange(2.5, 40, 5.0))
    grade = np.full(gx.shape, 0.1)
    grade[3, 3] = 5.0  # one hole: about 2-3 m of ore around it with IDW
    holes = np.column_stack([gx.ravel(), gy.ravel(), grade.ravel()])
    raw = grade_control(outline, holes, (1.0,), cell=0.5, smu=0.5, radius=6, max_samples=4)
    smooth = grade_control(outline, holes, (1.0,), cell=0.5, smu=10.0, radius=6, max_samples=4)
    assert raw.rows[1].area > 0
    assert smooth.rows[1].area == 0
    assert smooth.ore_loss_t == pytest.approx(raw.rows[1].tonnes)
