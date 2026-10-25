"""Known-answer checks A-H of the technical audit (docs/audit/AUDIT_2026-10.md).

Each test computes a small case whose answer is known in advance by hand and
compares MineCore's own functions with it. The letters follow the owner's
audit brief: A block tonnage, B contained metal, C recovered metal,
D discounting, E block value, F pit optimisation, G production and stockpile
balance, H error conditions.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel, BlockModelError, Grid
from minecore.blockmodel.importer import import_block_csv
from minecore.pitopt import (
    Economics,
    PriceUnit,
    SlopeProfile,
    block_values,
    optimise_pit,
    precedence_arcs,
    pushbacks,
)
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.scheduling import Limits, schedule_constrained
from minecore.project import Project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "audit.mcproj", "audit")


def _one_block(project: Project, grade: float, unit: str) -> BlockModel:
    model = BlockModel.create(project, "one", Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (1, 1, 1)))
    model.set_attributes({"g": np.array([grade])}, {"g": {"unit": unit}}, "grade")
    return model


def test_a_block_tonnage(project: Project) -> None:
    model = _one_block(project, 1.0, "%")
    assert model.grid.block_volume == 1000.0
    row = model.grade_tonnage("g", [0.0], density=2.7).rows[0]
    assert row.volume == pytest.approx(1000.0)
    assert row.tonnes == pytest.approx(2700.0)


def test_b_contained_metal_percent_and_gpt(project: Project) -> None:
    cu = _one_block(project, 1.0, "%").grade_tonnage("g", [0.0], density=2.7)
    assert cu.metal_unit == "t"
    assert cu.rows[0].metal == pytest.approx(27.0)  # 2700 t x 1 %
    au = BlockModel.create(project, "au", Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (1, 1, 1)))
    au.set_attributes({"g": np.array([2.0])}, {"g": {"unit": "g/t"}}, "grade")
    gt = au.grade_tonnage("g", [0.0], density=2.7)
    assert gt.metal_unit == "g"
    assert gt.rows[0].metal == pytest.approx(5400.0)  # 2700 t x 2 g/t = 5.4 kg


def test_c_recovered_metal() -> None:
    econ = Economics(10_000.0, PriceUnit.TONNE, 0.85, 0.0, 0.0)
    out = block_values(np.array([1.0]), "%", np.array([2700.0]), np.zeros(1, np.int64), econ)
    assert out.metal[0] == pytest.approx(22.95)  # 27 t x 85 %


def test_d_discounting_at_period_end(project: Project) -> None:
    # One ore block worth 110 after costs, mined and fed in period 1 at 10 %.
    model = _one_block(project, 1.0, "%")
    econ = Economics(10_000.0, PriceUnit.TONNE, 1.0, 0.0, 0.0, density=1.0)
    # 1000 t x 1 % x 10 000 = 100 000 revenue; mining cost makes cash exactly 110.
    econ = Economics(10_000.0, PriceUnit.TONNE, 1.0, (100_000.0 - 110.0) / 1000.0, 0.0, density=1.0)
    result = optimise_pit(model, "g", econ, SlopeProfile(45.0), [1.0])
    phase = pushbacks(result, 1, 0.0)
    sched = schedule_constrained(result, phase, Limits(1e9, 1e9, discount_rate=0.10))
    assert len(sched.rows) == 1
    assert sched.rows[0].cash == pytest.approx(110.0)
    assert sched.rows[0].discounted == pytest.approx(100.0)
    assert sched.npv == pytest.approx(100.0)


def test_e_block_economic_value() -> None:
    # 1000 t at 1 %, 80 % recovery, 10 000 per t metal; costs 20 + 30 per t = 50 000.
    econ = Economics(10_000.0, PriceUnit.TONNE, 0.80, 20.0, 30.0)
    out = block_values(np.array([1.0]), "%", np.array([1000.0]), np.zeros(1, np.int64), econ)
    assert out.metal[0] * 10_000.0 == pytest.approx(80_000.0)
    assert out.value[0] == pytest.approx(30_000.0)
    assert bool(out.ore[0])
    # A waste block is kept with its negative value (it may have to be mined).
    waste = block_values(np.array([0.0]), "%", np.array([1000.0]), np.zeros(1, np.int64), econ)
    assert waste.value[0] == pytest.approx(-20_000.0)


def test_f_pit_optimisation_matches_a_hand_solved_section(project: Project) -> None:
    """2-D section, 45 degree slopes, square blocks: each block needs the 3 above it.

    Values (top row first)        Optimum by hand:
        -1 -1 -1 -1 -1             mine the 3-block cone above the +10
        -1 -1 +10 -1 -1             ore (-1 -1 -1 / +10) = +7, and not the
        -1 -1 -1 -1 +2              +2, whose cone costs 3 more blocks
                                    (-1 -1 / -1 and shares one) = net -1.
    """
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (5, 1, 3))
    model = BlockModel.create(project, "sec", grid)
    values = np.array(
        [[-1, -1, -1, -1, 2], [-1, -1, 10, -1, -1], [-1, -1, -1, -1, -1]], np.float64
    )  # bottom row first (k = 0)
    # Value = grade revenue - 1 per block: grade in % of a 1 t block at price 100/t.
    grade = values.ravel() + 1.0
    model.set_attributes({"g": grade}, {"g": {"unit": "%"}}, "grades")
    econ = Economics(100.0, PriceUnit.TONNE, 1.0, 1.0, 0.0, density=1.0 / 1000.0)
    result = optimise_pit(model, "g", econ, SlopeProfile(45.0), [1.0])
    np.testing.assert_allclose(result.value, values.ravel(), atol=1e-9)
    mined = set(np.flatnonzero(result.shell == 1).tolist())
    nx = 5
    expected = {2 + nx * 1, 1 + nx * 2, 2 + nx * 2, 3 + nx * 2}
    assert mined == expected
    assert result.value[result.shell == 1].sum() == pytest.approx(7.0)
    # Independent check: exhaustive search over all closed sets of the same arcs.
    arcs = precedence_arcs(grid, SlopeProfile(45.0), None)
    best = max(
        sum(values.ravel()[i] for i in range(15) if (m >> i) & 1)
        for m in range(1 << 15)
        if all(not (m >> int(a)) & 1 or (m >> int(b)) & 1 for a, b in arcs)
    )
    assert best == pytest.approx(7.0)


def test_g_production_and_stockpile_balance(project: Project) -> None:
    rng = np.random.default_rng(11)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (12, 8, 6))
    model = BlockModel.create(project, "g", grid)
    c = grid.centroids()
    grade = np.where(
        np.hypot(c[:, 0] - 60, c[:, 1] - 40) < 30, rng.uniform(0.5, 4.0, grid.blocks), 0
    )
    model.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "grades")
    econ = Economics(60.0, PriceUnit.GRAM, 0.9, 2.0, 15.0, density=2.7)
    result = optimise_pit(model, "au", econ, SlopeProfile(45.0), [0.6, 0.8, 1.0])
    phase = pushbacks(result, 3, 20_000.0)
    limits = Limits(mining_capacity=60_000.0, plant_capacity=15_000.0, discount_rate=0.1)
    sched = schedule_constrained(result, phase, limits)
    assert len(sched.rows) >= 2
    stock = 0.0
    for row in sched.rows:
        assert row.mined_t <= limits.mining_capacity * (1 + 1e-9)
        assert row.ore_fed_t <= limits.plant_capacity * (1 + 1e-9)
        stock = stock + row.ore_mined_t - row.ore_fed_t  # S_t = S_t-1 + I_t - O_t
        assert row.stockpile_t == pytest.approx(stock, abs=1e-6)
        assert row.stockpile_t >= -1e-9
        assert row.discounted == pytest.approx(row.cash / 1.1**row.period)
    # Mass balance: everything in the pit is mined; all ore ends at the plant.
    inside = result.shell > 0
    assert sum(r.mined_t for r in sched.rows) == pytest.approx(result.tonnes[inside].sum())
    assert sum(r.ore_fed_t for r in sched.rows) == pytest.approx(
        result.feed[inside & result.ore].sum()
    )
    assert sched.rows[-1].stockpile_t == pytest.approx(0.0, abs=1e-6)
    assert sched.unmined_t == pytest.approx(0.0, abs=1e-6)


def test_h_error_conditions(project: Project, tmp_path: Path) -> None:
    model = _one_block(project, 1.0, "%")
    with pytest.raises(BlockModelError):
        model.grade_tonnage("g", [0.0], density=0.0)  # zero density
    with pytest.raises(BlockModelError):
        model.grade_tonnage("g", [0.0], density=-2.7)  # negative density
    with pytest.raises(PitOptError):
        Economics(10.0, PriceUnit.TONNE, 1.5, 1.0, 1.0)  # recovery above 100 %
    with pytest.raises(PitOptError):  # grade without a unit cannot be valued
        block_values(np.ones(1), None, np.ones(1), np.zeros(1, np.int64), Economics(
            10.0, PriceUnit.TONNE, 0.9, 1.0, 1.0))  # fmt: skip
    with pytest.raises(BlockModelError):
        Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (0, 1, 1))  # empty model
    with pytest.raises(BlockModelError):
        Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (1000, 1000, 100))  # above the 50 M limit
    with pytest.raises(PitOptError):
        Limits(mining_capacity=0.0, plant_capacity=1.0)  # impossible capacity
    with pytest.raises(BlockModelError):
        model.values("missing")
    # No profitable block: the optimiser returns an empty pit, not an error or a guess.
    poor = _one_block_named(project, "poor", 0.0)
    econ = Economics(10.0, PriceUnit.TONNE, 0.9, 1.0, 1.0, density=2.7)
    empty = optimise_pit(poor, "g", econ, SlopeProfile(45.0), [1.0])
    assert not (empty.shell > 0).any()
    assert empty.rows[0].ore_t == 0.0
    # Not enough periods: the schedule reports what it could not mine.
    rich = _one_block_named(project, "rich", 5.0)
    full = optimise_pit(rich, "g", Economics(10_000.0, PriceUnit.TONNE, 0.9, 1.0, 1.0, density=2.7),
                        SlopeProfile(45.0), [1.0])  # fmt: skip
    assert (full.shell == 1).all()
    short = schedule_constrained(full, pushbacks(full, 1, 0.0), Limits(100.0, 100.0, max_periods=2))
    assert short.unmined_t == pytest.approx(2700.0 - 200.0)
    # Broken import files fail with a message, and nothing is created.
    bad = tmp_path / "bad.csv"
    bad.write_text("x;y;z;au\n1;2;abc;1\n")
    with pytest.raises(BlockModelError, match="centroid"):
        import_block_csv(project, bad, "bad")
    assert "bad" not in [r.name for r in project.objects("block_model")]
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("x;y;z;au\n")
    with pytest.raises(MineCoreError, match="fewer than two non-empty lines"):
        import_block_csv(project, empty_csv, "empty")


def _one_block_named(project: Project, name: str, grade: float) -> BlockModel:
    model = BlockModel.create(project, name, Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (1, 1, 1)))
    model.set_attributes({"g": np.array([grade])}, {"g": {"unit": "%"}}, "grade")
    return model


def test_revenue_factor_scales_price_not_costs() -> None:
    """Nested shells use P' = f P with unchanged costs (documented in the audit)."""
    econ = Economics(10_000.0, PriceUnit.TONNE, 0.8, 20.0, 30.0)
    args = (np.array([1.0]), "%", np.array([1000.0]), np.zeros(1, np.int64), econ)
    for f in (0.5, 0.75, 1.0, 1.2):
        assert block_values(*args, f).value[0] == pytest.approx(80_000.0 * f - 50_000.0)


def test_precedence_never_allows_a_block_without_its_overburden(project: Project) -> None:
    rng = np.random.default_rng(5)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (9, 9, 5))
    model = BlockModel.create(project, "p", grid)
    model.set_attributes(
        {"g": np.where(rng.random(grid.blocks) < 0.2, rng.uniform(1, 8, grid.blocks), 0.0)},
        {"g": {"unit": "g/t"}},
        "g",
    )
    econ = Economics(60.0, PriceUnit.GRAM, 0.9, 2.0, 15.0)
    result = optimise_pit(model, "g", econ, SlopeProfile(45.0), [0.5, 1.0, 1.5])
    nx, ny, nz = grid.count
    for n in (1, 2, 3):
        pit = ((result.shell > 0) & (result.shell <= n)).reshape(nz, ny, nx)
        # 45 deg with 10 m blocks: one level up the cone reaches 10 m, so a mined block
        # needs the block above it and the four edge neighbours of that one; two levels
        # up (20 m) it also needs the diagonals (14.1 m).
        for k, j, i in itertools.product(range(nz - 1), range(ny), range(nx)):
            if not pit[k, j, i]:
                continue
            for dk, di, dj in [(1, 0, 0), (1, 1, 0), (1, -1, 0), (1, 0, 1), (1, 0, -1),
                               (2, 1, 1), (2, -1, 1), (2, 1, -1), (2, -1, -1)]:  # fmt: skip
                kk, ii, jj = k + dk, i + di, j + dj
                if kk < nz and 0 <= ii < nx and 0 <= jj < ny:
                    assert pit[kk, jj, ii]


def test_pushback_ore_target_counts_plant_feed(project: Project) -> None:
    """Audit finding: the pushback target used in-situ ore tonnes, the report plant feed."""
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (3, 1, 1))
    model = BlockModel.create(project, "pb", grid)
    model.set_attributes({"g": np.array([6.0, 3.0, 1.5])}, {"g": {"unit": "%"}}, "g")
    # 1000 t blocks, 50 % mining recovery: 500 t of feed each.
    econ = Economics(100.0, PriceUnit.TONNE, 0.9, 1.0, 1.0, density=1.0, mining_recovery=0.5)
    result = optimise_pit(model, "g", econ, SlopeProfile(45.0), [0.6, 1.2, 2.5])
    assert result.shell.tolist() == [1, 2, 3]
    assert result.rows[2].ore_t == pytest.approx(1500.0)
    # A 1000 t feed target groups shells 1 + 2; counting in-situ tonnes would give 3 phases.
    assert pushbacks(result, 3, 1000.0).tolist() == [1, 1, 2]
