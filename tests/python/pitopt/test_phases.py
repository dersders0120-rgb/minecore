"""Pushbacks chosen by NPV: exhaustive choice against an independent brute force, the
local search never below its start, ore targets respected."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.pitopt import Economics, PriceUnit, SlopeProfile, optimise_pit, pushbacks
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult
from minecore.pitopt.phases import best_pushbacks, phase_from_boundaries
from minecore.pitopt.scheduling import Limits, schedule_constrained
from minecore.project import Project

FACTORS = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
LIMITS = Limits(60_000.0, 15_000.0, bench_lag=1, sinking_rate=3, discount_rate=0.12)


@pytest.fixture
def pit(tmp_path: Path) -> Iterator[PitResult]:
    project = Project.create(tmp_path / "p.mcproj", "p")
    rng = np.random.default_rng(4)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (16, 12, 8))
    model = BlockModel.create(project, "m", grid)
    c = grid.centroids()
    r = np.hypot(c[:, 0] - 80, c[:, 1] - 60)
    grade = np.where(r < 50, rng.uniform(0.2, 1.5, grid.blocks) * (1.6 - r / 50), 0.0)
    model.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "au")
    econ = Economics(60.0, PriceUnit.GRAM, 0.9, 2.0, 12.0)
    yield optimise_pit(model, "au", econ, SlopeProfile(45.0), FACTORS)
    project.close()


def _feed(pit: PitResult, lo: int, hi: int) -> float:
    sel = (pit.shell > lo) & (pit.shell <= hi) & pit.ore
    return float(pit.feed[sel].sum())


def test_exhaustive_choice_matches_brute_force(pit: PitResult) -> None:
    final, min_ore = 7, 10_000.0
    choice = best_pushbacks(pit, final, LIMITS, min_ore_t=min_ore, max_pushbacks=4)
    assert choice.exhaustive
    best = -np.inf
    best_b: tuple[int, ...] = ()
    for k in range(4):  # independent enumeration: up to 4 pushbacks
        for b in itertools.combinations(range(1, final), k):
            edges = (0, *b, final)
            if any(_feed(pit, lo, hi) < min_ore for lo, hi in itertools.pairwise(edges)):
                continue
            phase = phase_from_boundaries(pit, final, b)
            v = schedule_constrained(pit, phase, LIMITS).npv
            if v > best + 1e-6:
                best, best_b = v, b
    assert choice.npv == pytest.approx(best)
    assert choice.boundaries == best_b
    assert choice.npv >= choice.start_npv - 1e-6
    assert int(choice.phase.max()) == len(choice.boundaries) + 1


def test_local_search_never_below_its_start(pit: PitResult) -> None:
    choice = best_pushbacks(pit, 7, LIMITS, min_ore_t=8_000.0, max_evaluations=12)
    assert not choice.exhaustive
    assert choice.evaluations <= 12
    assert choice.npv >= choice.start_npv - 1e-6
    edges = (0, *choice.boundaries, 7)
    assert all(_feed(pit, lo, hi) >= 8_000.0 for lo, hi in itertools.pairwise(edges))


def test_boundaries_reproduce_the_ore_target_grouping(pit: PitResult) -> None:
    phase = pushbacks(pit, 7, 20_000.0)
    tops = tuple(int(pit.shell[phase == p].max()) for p in range(1, int(phase.max())))
    assert (phase_from_boundaries(pit, 7, tops) == phase).all()
    choice = best_pushbacks(pit, 7, LIMITS, min_ore_t=20_000.0)
    assert choice.start_boundaries == tops


def test_validation(pit: PitResult) -> None:
    with pytest.raises(PitOptError):
        best_pushbacks(pit, 9, LIMITS, min_ore_t=1.0)
    with pytest.raises(PitOptError):
        best_pushbacks(pit, 7, LIMITS, min_ore_t=1.0, max_pushbacks=0)
    one = best_pushbacks(pit, 7, LIMITS, min_ore_t=1.0, max_pushbacks=1)
    assert one.boundaries == ()
    assert set(np.unique(one.phase)) <= {0, 1}
