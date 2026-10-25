"""Re-blocking to larger blocks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.blockmodel.reblock import reblock
from minecore.project import Project


def test_reblock_conserves_metal(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "r.mcproj", "r")
    grid = Grid((0.0, 0.0, 0.0), (5.0, 5.0, 5.0), (4, 4, 3))
    model = BlockModel.create(project, "A", grid)
    rng = np.random.default_rng(2)
    au = rng.uniform(0, 4, grid.blocks)
    au[0] = np.nan
    rho = rng.uniform(2.4, 3.0, grid.blocks)
    model.set_attributes(
        {"au": au, "rho": rho}, {"au": {"unit": "g/t"}, "rho": {"unit": "t/m3"}}, "data"
    )
    big = reblock(project, model, "B", (2, 2, 2), density="rho")
    assert big.grid.count == (2, 2, 2)
    assert big.grid.size == (10.0, 10.0, 10.0)
    # Contained metal of the known blocks is conserved.
    ok = np.isfinite(au)
    metal = (au[ok] * rho[ok] * 125.0).sum()
    # Tonnage of the big blocks over the same known sub-blocks:
    idx = np.arange(grid.blocks)
    i, j, k = idx % 4, (idx // 4) % 4, idx // 16
    cell = i // 2 + 2 * (j // 2 + 2 * (k // 2))
    known_t = np.bincount(cell[ok], rho[ok] * 125.0, minlength=8)
    assert (big.values("au") * known_t).sum() == pytest.approx(metal)
    # Density is volume-weighted (top layer padded: 2 of 3 levels -> one partial big block).
    top = cell == 4
    assert big.values("rho")[4] == pytest.approx(rho[top].mean())
    project.close()
