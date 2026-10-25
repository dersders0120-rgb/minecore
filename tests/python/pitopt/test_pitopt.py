"""Pit optimisation checked against hand calculations and exhaustive search."""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.pitopt import (
    Economics,
    PriceUnit,
    SlopeProfile,
    block_values,
    optimise_pit,
    precedence_arcs,
    pushbacks,
    schedule,
)
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import store
from minecore.project import Project


def test_gold_block_value_by_hand() -> None:
    econ = Economics(1800.0, PriceUnit.TROY_OUNCE, 0.9, 2.0, 15.0, selling_cost=0.0)
    out = block_values(
        np.array([1.0, 0.1, np.nan]),
        "g/t",
        np.array([1000.0, 1000.0, 1000.0]),
        np.array([0, 0, 2]),
        econ,
    )
    oz = 1000 * 1000 * 1e-6 * 0.9 / 0.0311034768  # 28.935 ozt recovered
    assert out.metal[0] == pytest.approx(oz)
    assert out.value[0] == pytest.approx(oz * 1800 - 15_000 - 2_000)
    # 0.1 g/t pays 5208 < 15000 processing: waste.
    assert not out.ore[1]
    assert out.value[1] == pytest.approx(-2_000)
    assert out.value[2] == pytest.approx(-2_000)


def test_copper_percent_and_bench_cost() -> None:
    econ = Economics(
        9000.0, PriceUnit.TONNE, 0.85, 2.0, 10.0, selling_cost=500.0, mining_cost_per_bench=0.1
    )
    out = block_values(np.array([0.5]), "%", np.array([100.0]), np.array([10]), econ)
    metal_t = 100 * 0.005 * 0.85
    assert out.value[0] == pytest.approx(metal_t * 8500 - 1000 - 100 * 3.0)


def test_invalid_economics() -> None:
    with pytest.raises(PitOptError):
        Economics(0.0, PriceUnit.GRAM, 0.9, 1, 1)
    with pytest.raises(PitOptError):
        Economics(10.0, PriceUnit.GRAM, 1.5, 1, 1)


def _closure(arcs: np.ndarray, start: int, n: int) -> set[int]:
    out: dict[int, list[int]] = {}
    for a, b in arcs:
        out.setdefault(int(a), []).append(int(b))
    seen, stack = {start}, [start]
    while stack:
        for v in out.get(stack.pop(), []):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen - {start}


@pytest.mark.parametrize(
    ("size", "slope"),
    [
        ((10.0, 10.0, 10.0), SlopeProfile(45.0)),
        ((10.0, 10.0, 5.0), SlopeProfile(40.0)),
        (
            (10.0, 12.0, 6.0),
            SlopeProfile(sectors=((0.0, 50.0), (90.0, 35.0), (180.0, 50.0), (270.0, 35.0))),
        ),
    ],
)
def test_precedence_chains_reproduce_the_cone_exactly(
    size: tuple[float, float, float], slope: SlopeProfile
) -> None:
    grid = Grid((0.0, 0.0, 0.0), size, (15, 15, 5))
    arcs = precedence_arcs(grid, slope, max_levels=4)
    nx, ny, nz = grid.count
    i0, j0 = 7, 7
    start = i0 + nx * j0  # bottom bench, centre
    got = _closure(arcs, start, grid.blocks)
    expected = set()
    for i, j, k in itertools.product(range(nx), range(ny), range(1, nz)):
        dx, dy, dz = (i - i0) * size[0], (j - j0) * size[1], k * size[2]
        az = math.degrees(math.atan2(dx, dy))
        angle = float(slope.at(np.array([az]))[0])
        if math.hypot(dx, dy) <= dz / math.tan(math.radians(angle)) + 1e-9:
            expected.add(i + nx * (j + ny * k))
    assert got == expected


def _brute_force(values: np.ndarray, arcs: np.ndarray) -> float:
    n = len(values)
    best = 0.0
    for mask in range(1 << n):
        inside = [(mask >> b) & 1 for b in range(n)]
        if all(not inside[a] or inside[b] for a, b in arcs):
            best = max(best, sum(v for v, s in zip(values, inside, strict=True) if s))
    return best


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "p.mcproj", "p")


def test_ultimate_pit_matches_exhaustive_search(project: Project) -> None:
    rng = np.random.default_rng(3)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (4, 1, 4))
    model = BlockModel.create(project, "bm", grid)
    grade = np.where(rng.random(grid.blocks) < 0.35, rng.uniform(0.5, 6.0, grid.blocks), 0.0)
    model.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "grades")
    econ = Economics(60.0, PriceUnit.GRAM, 0.9, 3.0, 20.0, density=2.5)
    slope = SlopeProfile(45.0)
    result = optimise_pit(model, "au", econ, slope, [1.0], max_levels=3)
    arcs = precedence_arcs(grid, slope, 3)
    pit_value = float(result.value[result.shell == 1].sum())
    assert pit_value == pytest.approx(_brute_force(result.value, arcs), abs=0.01 * grid.blocks)


def _ore_body(project: Project) -> tuple[BlockModel, Economics]:
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (30, 20, 12))
    model = BlockModel.create(project, "bm", grid)
    c = grid.centroids()
    # A dipping lens of gold, richest at depth.
    d = np.hypot(c[:, 0] - 150, (c[:, 2] - 60) * 2.0) + 0.3 * np.abs(c[:, 1] - 100)
    grade = np.clip(4.0 - d / 25.0, 0, None) * (1 + (60 - c[:, 2]) / 200)
    model.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "grades")
    return model, Economics(55.0, PriceUnit.GRAM, 0.9, 2.5, 18.0, density=2.6)


def test_shells_nested_report_pushbacks_and_schedule(project: Project) -> None:
    model, econ = _ore_body(project)
    factors = [0.4, 0.6, 0.8, 1.0, 1.2]
    result = optimise_pit(model, "au", econ, SlopeProfile(45.0), factors, mining_rate=2e6)
    rows = result.rows
    tonnes = [r.ore_t + r.waste_t for r in rows]
    assert all(a <= b for a, b in itertools.pairwise(tonnes))
    assert tonnes[-1] > 0
    final = 4  # revenue factor 1.0
    for r in rows:
        assert r.best_npv >= r.worst_npv - 1e-6
    # Undiscounted cash of the RF=1 pit is the maximum over all shells' cash at RF=1.
    assert rows[final - 1].cash >= max(r.cash for r in rows) - 1e-6

    phase = pushbacks(result, final, min_ore_t=0.25 * rows[final - 1].ore_t)
    inside = (result.shell > 0) & (result.shell <= final)
    assert ((phase > 0) == inside).all()
    assert phase.max() >= 2

    sched = schedule(result, phase, 2e6, 0.08)
    total = sum(r.ore_t + r.waste_t for r in sched.rows)
    assert total == pytest.approx(float(result.tonnes[inside].sum()))
    assert sum(r.cash for r in sched.rows) == pytest.approx(rows[final - 1].cash)
    block_t = float(result.tonnes.max())
    for period in sched.rows:
        assert period.ore_t + period.waste_t <= 2e6 + block_t
    store(model, result, econ, SlopeProfile(45.0), phase, sched)
    assert {"pit_value", "pit_shell", "pit_phase", "pit_period"} <= set(model.attributes)
