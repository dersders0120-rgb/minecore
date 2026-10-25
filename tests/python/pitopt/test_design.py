"""Pit design outputs: bench outlines, pit surface, pit solid, DXF export."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from minecore.blockmodel import BlockModel, Grid
from minecore.io.dxf import Polyline, write_dxf
from minecore.pitopt.design import _loops, bench_outlines, edge_blocks, pit_solid, pit_surface
from minecore.project import Project
from minecore.solids import read_dxf_3dfaces
from minecore.surfaces import Surface


def _area(loop: list[tuple[int, int]]) -> float:
    xy = np.array(loop, float)
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def test_square_with_hole() -> None:
    cells = np.ones((5, 5), bool)
    cells[2, 2] = False
    loops = _loops(cells)
    assert sorted(len(lp) for lp in loops) == [4, 4]
    assert sorted(_area(lp) for lp in loops) == [-1.0, 25.0]


@settings(max_examples=60, deadline=None)
@given(arrays(np.bool_, st.tuples(st.integers(1, 7), st.integers(1, 7))))
def test_signed_area_equals_cell_count(cells: np.ndarray) -> None:
    # Holds for any mask, including cells touching only at a corner.
    loops = _loops(cells)
    assert sum(_area(lp) for lp in loops) == cells.sum()


def _box_grid() -> tuple[Grid, np.ndarray]:
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 5.0), (8, 8, 6))
    nx, ny, nz = grid.count
    mined = np.zeros((nz, ny, nx), bool)
    mined[3:, 1:7, 1:7] = True  # top 3 benches, 6 x 6 blocks
    mined[2, 2:5, 2:5] = True  # one deeper bench
    return grid, mined.ravel()


def test_bench_outlines_and_surface() -> None:
    grid, mined = _box_grid()
    lines = bench_outlines(grid, mined)
    assert len(lines) == 8  # 4 benches x crest + toe
    crest = [ln for ln in lines if ln.layer == "PIT_CREST"]
    assert {float(ln.points[0, 2]) for ln in crest} == {30.0, 25.0, 20.0, 15.0}
    bottom = next(ln for ln in crest if ln.points[0, 2] == 15.0)
    assert bottom.points[:, :2].min(axis=0).tolist() == [20.0, 20.0]
    assert bottom.points[:, :2].max(axis=0).tolist() == [50.0, 50.0]
    corners = [[-50, -50, 28.0], [150, -50, 28.0], [150, 150, 28.0], [-50, 150, 28.0]]
    topo = Surface(np.array(corners))
    s = pit_surface(grid, mined, topo)
    z = s.points[:, 2].reshape(8, 8)
    assert z[0, 0] == 28.0  # unmined: topography
    assert z[1, 1] == 15.0  # floor of bench 3
    assert z[3, 3] == 10.0  # floor of the deeper bench


def test_pit_solid_volume_and_dxf_round_trip(tmp_path: Path) -> None:
    grid, mined = _box_grid()
    solid = pit_solid(grid, mined)
    assert solid.open_edges() == 0
    blocks = mined.sum() * grid.block_volume
    assert 0.8 * blocks < solid.volume() <= blocks
    path = tmp_path / "pit.dxf"
    tris = solid.vertices[solid.triangles]
    n = write_dxf(path, bench_outlines(grid, mined), [("PIT_SOLID", tris)])
    assert n == 8 + len(tris)
    back = read_dxf_3dfaces(path)
    assert abs(back.volume() - solid.volume()) < 1e-3 * solid.volume()
    assert back.open_edges() == 0
    text = path.read_text()
    assert text.count("POLYLINE") == 8
    assert text.rstrip().endswith("EOF")


def test_open_polyline_flag(tmp_path: Path) -> None:
    path = tmp_path / "l.dxf"
    write_dxf(path, [Polyline("A/B", np.zeros((3, 3)), closed=False)])
    lines = path.read_text().splitlines()
    i = lines.index("POLYLINE")
    assert lines[i + 2] == "A_B"
    assert lines[lines.index("70", i) + 1] == "8"


def test_edge_blocks() -> None:
    grid, mined = _box_grid()
    assert edge_blocks(grid, mined) == 0
    wide = mined.reshape(6, 8, 8).copy()
    wide[5, 0, 3] = True
    assert edge_blocks(grid, wide.ravel()) == 1


def test_period_surfaces_follow_the_schedule(tmp_path: Path) -> None:
    from minecore.pitopt.design import period_surfaces  # noqa: PLC0415
    from minecore.surfaces import load_surface  # noqa: PLC0415

    project = Project.create(tmp_path / "y.mcproj", "y")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (3, 1, 3))
    period = np.zeros((3, 1, 3))
    period[2, 0, 1] = 1  # top block of the middle column in year 1
    period[1, 0, 1] = 2  # the block below it in year 2
    period[2, 0, 0] = 2
    model = BlockModel.create(project, "BM", grid)
    model.set_attributes({"pit_period": period.ravel()}, {}, "schedule")
    rows = period_surfaces(project, model, dxf=tmp_path / "y.dxf")
    assert [(r.period, r.blocks, r.cumulative_blocks) for r in rows] == [(1, 1, 1), (2, 2, 3)]
    first = load_surface(project, "donem_1").points[:, 2].tolist()
    second = load_surface(project, "donem_2").points[:, 2].tolist()
    assert first == [30.0, 20.0, 30.0]
    assert second == [20.0, 10.0, 30.0]
    assert "Y2_TOE" in (tmp_path / "y.dxf").read_text()
    project.close()
