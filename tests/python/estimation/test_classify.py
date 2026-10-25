"""Confidence categories from estimation statistics."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, BlockModelError, Grid
from minecore.blockmodel.classify import Criteria, categorise, classify
from minecore.project import Project

CRIT = [Criteria(25.0, 8, 0.4), Criteria(50.0, 4, 0.7), Criteria(100.0, 1)]


def test_categorise_by_hand() -> None:
    dist = np.array([10.0, 10.0, 40.0, 90.0, 150.0, np.nan])
    n = np.array([10.0, 10.0, 6.0, 2.0, 2.0, 0.0])
    kv = np.array([0.2, 0.5, 0.6, 0.9, 0.9, np.nan])
    out = categorise(dist, n, kv, CRIT)
    # Block 2 is close but its variance only meets category 2.
    assert out[:5].tolist() == [1.0, 2.0, 2.0, 3.0, 0.0]
    assert np.isnan(out[5])
    with pytest.raises(BlockModelError):
        categorise(dist, n, None, CRIT)


def test_classify_and_masked_report(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "c.mcproj", "c")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (3, 2, 1))
    model = BlockModel.create(project, "BM", grid)
    vg = {"nugget": 0.2, "structures": [{"type": "spherical", "sill": 0.8}]}
    model.set_attributes(
        {
            "au": np.array([1.0, 2.0, 3.0, 4.0, 5.0, np.nan]),
            "au_n": np.array([10, 10, 6, 2, 2, 0], float),
            "au_dist": np.array([10, 10, 40, 90, 150, np.nan], float),
            "au_kv": np.array([0.2, 0.5, 0.6, 0.9, 0.9, np.nan]),  # sill 1: ratio = kv
        },
        {"au": {"unit": "g/t", "variogram": vg}},
        "estimate",
    )
    counts = classify(model, "au", CRIT)
    assert counts == {0: 1, 1: 1, 2: 2, 3: 1}
    cat = model.values("kat_au")
    gt = model.grade_tonnage("au", [0.0], density=2.0, mask=cat == 2)
    assert gt.rows[0].blocks == 2
    assert gt.rows[0].grade == pytest.approx(2.5)
    assert gt.unestimated_blocks == 0
    project.close()
