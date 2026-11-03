"""OMF export -> import round trip keeps geometry and block values."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.io.omf_io import export_omf, import_omf
from minecore.project import Project
from minecore.solids import Solid, load_solid, save_solid
from minecore.strings import String, load_strings, save_strings
from minecore.surfaces import Surface, load_surface, save_surface


def test_round_trip(tmp_path: Path) -> None:
    src = Project.create(tmp_path / "a.mcproj", "a")
    grid = Grid((100.0, 200.0, 50.0), (5.0, 10.0, 2.0), (3, 2, 2), 30.0)
    model = BlockModel.create(src, "bm", grid)
    au = np.arange(12, dtype=np.float64)
    au[3] = np.nan
    model.set_attributes({"au": au}, {}, "au")
    save_surface(src, "topo", Surface(np.array([[0.0, 0, 1], [10, 0, 2], [0, 10, 3], [10, 10, 4]])))
    v = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    save_solid(src, "tet", Solid(v, np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])))
    ring = String(np.array([[0.0, 0, 5], [5, 0, 5], [5, 5, 5]]), True)
    line = String(np.array([[9.0, 9, 9], [8, 8, 8]]), False)
    save_strings(src, "lines", [ring, line])
    out = tmp_path / "x.omf"
    export_omf(src, out)

    dst = Project.create(tmp_path / "b.mcproj", "b")
    report = import_omf(dst, out)
    assert not report.skipped
    back = BlockModel.open(dst, "bm")
    g = back.grid
    assert g.count == grid.count
    assert g.size == pytest.approx(grid.size)
    assert g.azimuth == pytest.approx(30.0)
    assert g.origin == pytest.approx(grid.origin)
    np.testing.assert_allclose(back.values("au"), au)
    assert load_surface(dst, "topo").points.shape == (4, 3)
    assert load_solid(dst, "tet").volume() == pytest.approx(1 / 6)
    got = sorted(load_strings(dst, "lines"), key=lambda s: len(s.points))
    assert [len(s.points) for s in got] == [2, 3]
    assert got[1].closed
    assert not got[0].closed
