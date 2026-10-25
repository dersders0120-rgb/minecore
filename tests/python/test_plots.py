"""Section plots: plane cuts and a full figure on synthetic data."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.plots import PlotError, SectionSpec, cut_solid, section_plot
from minecore.project import Project
from minecore.solids import Solid, save_solid
from minecore.surfaces import Surface, save_surface


def _box(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> Solid:
    v = np.array(
        [[(hi if b & 1 else lo)[0], (hi if b & 2 else lo)[1], (hi if b & 4 else lo)[2]]
         for b in range(8)]
    )  # fmt: skip
    quads = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5))
    tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return Solid(v.astype(float), np.array(tris, np.int64))


def test_plane_cut_of_a_box_is_its_rectangle() -> None:
    box = _box((0.0, 0.0, 0.0), (10.0, 20.0, 5.0))
    segs = cut_solid(box, 0, 4.0)
    length = np.linalg.norm(segs[:, 1] - segs[:, 0], axis=1).sum()
    assert length == pytest.approx(2 * (20.0 + 5.0))
    assert np.allclose(segs[:, :, 0], 4.0)
    assert len(cut_solid(box, 2, 9.0)) == 0
    plan = cut_solid(box, 2, 2.5)
    assert np.linalg.norm(plan[:, 1] - plan[:, 0], axis=1).sum() == pytest.approx(60.0)


@pytest.mark.parametrize(("axis", "suffix"), [(0, ".png"), (1, ".pdf"), (2, ".svg")])
def test_section_figure(tmp_path: Path, axis: int, suffix: str) -> None:
    project = Project.create(tmp_path / "p.mcproj", "p")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (10, 8, 6))
    model = BlockModel.create(project, "BM", grid)
    model.set_attributes({"au": grid.centroids()[:, 2] / 10.0}, {"au": {"unit": "g/t"}}, "au")
    xx, yy = np.meshgrid(np.linspace(-10, 110, 13), np.linspace(-10, 90, 11))
    topo = np.column_stack([xx.ravel(), yy.ravel(), 55.0 + 0.05 * xx.ravel()])
    save_surface(project, "topo", Surface(topo))
    save_solid(project, "ore", _box((20.0, 20.0, 10.0), (60.0, 50.0, 40.0)))
    spec = SectionSpec(axis, 35.0, 10.0, model="BM", attribute="au", surfaces=("topo",),
                       solids=("ore",))  # fmt: skip
    out = tmp_path / f"s{suffix}"
    r = section_plot(project, spec, out)
    assert out.stat().st_size > 1000
    count = {0: (8, 6), 1: (10, 6), 2: (10, 8)}[axis]
    assert r.blocks == count[0] * count[1]
    assert r.solid_segments["ore"] > 0
    if axis != 2:
        assert r.surface_lines["topo"] > 100
    with pytest.raises(PlotError):
        section_plot(project, SectionSpec(axis, 1e6, 10.0, model="BM"), out)
    project.close()


def test_plan_of_a_tall_model(tmp_path: Path) -> None:
    # Regression: the plane index along z can exceed the x and y counts.
    project = Project.create(tmp_path / "t.mcproj", "t")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (2, 2, 10))
    model = BlockModel.create(project, "BM", grid)
    model.set_attributes({"au": np.ones(grid.blocks)}, {}, "au")
    r = section_plot(project, SectionSpec(2, 85.0, 10.0, model="BM", attribute="au"),
                     tmp_path / "p.png")  # fmt: skip
    assert r.blocks == 4
    project.close()
