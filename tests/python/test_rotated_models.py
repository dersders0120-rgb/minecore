"""Rotated block models (ADR-0009): the rotated result must equal the unrotated one.

Every check builds the same problem twice: once in an unrotated frame and once
turned by an azimuth about a pivot. Results that do not depend on orientation
(estimates, volumes, fractions, tonnages) must agree; geometry must be the
unrotated geometry turned by the same rotation.
"""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from minecore.blockmodel import BlockModel, BlockModelError, Grid
from minecore.blockmodel.importer import import_block_csv
from minecore.blockmodel.model import FORMAT, FORMAT_ROTATED
from minecore.estimation import (
    Ellipsoid,
    Method,
    ModelType,
    Search,
    Structure,
    VariogramModel,
    estimate,
)
from minecore.pitopt.benchdesign import BenchGeometry, design_pit
from minecore.pitopt.design import bench_outlines
from minecore.plots import block_section_polygons
from minecore.project import Project
from minecore.solids import Solid, block_fractions, iso_solid
from minecore.underground.stopes import stope_envelope, stope_triangles

PIVOT = np.array([1000.0, 2000.0, 300.0])


def turn(points: np.ndarray, azimuth: float, pivot: np.ndarray = PIVOT) -> np.ndarray:
    """Rotate world points clockwise (seen from above) by ``azimuth`` about ``pivot``."""
    a = math.radians(azimuth)
    c, s = math.cos(a), math.sin(a)
    p = np.asarray(points, float) - pivot
    out = np.column_stack([c * p[:, 0] + s * p[:, 1], -s * p[:, 0] + c * p[:, 1], p[:, 2]])
    turned: np.ndarray = out + pivot
    return turned


def pair(azimuth: float, count: tuple[int, int, int] = (6, 5, 4)) -> tuple[Grid, Grid]:
    size = (10.0, 8.0, 5.0)
    plain = Grid(tuple(PIVOT), size, count)
    return plain, Grid(tuple(PIVOT), size, count, azimuth)


def _box(lo: np.ndarray, hi: np.ndarray) -> Solid:
    v = np.array(
        [[(hi if b & 1 else lo)[0], (hi if b & 2 else lo)[1], (hi if b & 4 else lo)[2]]
         for b in range(8)]
    )  # fmt: skip
    quads = ((0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5))
    tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    return Solid(v.astype(float), np.array(tris, np.int64))


def test_azimuth_convention_is_the_bearing_of_the_rows() -> None:
    g = Grid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1, 1, 1), 90.0)
    # Bearing 90: +j runs east, +i runs south.
    assert np.allclose(g.axes[:, 1], [1.0, 0.0, 0.0])
    assert np.allclose(g.axes[:, 0], [0.0, -1.0, 0.0])
    g30 = Grid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1, 1, 1), 30.0)
    assert np.allclose(g30.axes[:, 1], [0.5, math.sqrt(3) / 2, 0.0])


@settings(max_examples=50, deadline=None)
@given(
    st.floats(-359.0, 359.0),
    st.lists(st.floats(-1e4, 1e4), min_size=3, max_size=3),
)
def test_local_world_round_trip(azimuth: float, point: list[float]) -> None:
    g = Grid((500.0, -200.0, 10.0), (5.0, 5.0, 5.0), (3, 3, 3), azimuth)
    p = np.array([point])
    assert np.allclose(g.to_world(g.to_local(p)), p, atol=1e-6)
    assert np.allclose(g.centroids(), turn(g.local().centroids(), azimuth, np.zeros(3)) + g.origin)


def test_centroids_and_corners_turn_with_the_model() -> None:
    plain, rot = pair(37.0)
    assert np.allclose(rot.centroids(), turn(plain.centroids(), 37.0))
    assert np.allclose(rot.corners(), turn(plain.corners(), 37.0))
    lo, hi = rot.bounds()
    assert (rot.centroids() >= lo).all()
    assert (rot.centroids() <= hi).all()
    idx = rot.cell_index(rot.centroids())
    n = np.arange(rot.blocks)
    nx, ny, _ = rot.count
    assert np.array_equal(idx, np.column_stack([n % nx, (n // nx) % ny, n // (nx * ny)]))


def test_covering_a_rotated_cloud_uses_the_model_axes() -> None:
    rng = np.random.default_rng(4)
    local = rng.uniform([0, 0, 0], [200, 60, 50], size=(400, 3))
    pts = turn(local + PIVOT, 50.0)
    grid = Grid.covering(pts, (10.0, 10.0, 10.0), azimuth=50.0)
    assert grid.azimuth == 50.0
    idx = grid.cell_index(pts)
    assert (idx >= 0).all()
    assert (idx < np.asarray(grid.count)).all()
    # Aligned with the points' own frame, the model is far smaller than the
    # world-aligned box around the same points.
    unrotated = Grid.covering(pts, (10.0, 10.0, 10.0))
    assert grid.blocks < 0.6 * unrotated.blocks


def test_rotated_grid_is_stored_with_format_2_and_round_trips(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "p.mcproj", "p")
    _, rot = pair(25.0)
    plain = BlockModel.create(project, "plain", pair(0.0)[0])
    model = BlockModel.create(project, "rot", rot)
    assert plain.metadata["format"] == FORMAT
    assert "azimuth" not in plain.metadata["grid"]  # old readers see an unchanged record
    assert model.metadata["format"] == FORMAT_ROTATED
    project.close()
    with Project.open(tmp_path / "p.mcproj") as again:
        assert BlockModel.open(again, "rot").grid == rot
        assert BlockModel.open(again, "plain").grid.azimuth == 0.0
        record = next(r for r in again.objects("block_model") if r.name == "rot")
        meta = dict(record.metadata)
        meta["format"] = FORMAT_ROTATED + 1
        again.update_object(record.object_id, metadata=meta, description="future format")
        with pytest.raises(BlockModelError, match="newer MineCore"):
            BlockModel.open(again, "rot")


def test_invalid_azimuth_is_refused() -> None:
    with pytest.raises(BlockModelError):
        Grid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1, 1, 1), math.nan)
    with pytest.raises(BlockModelError):
        Grid((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (1, 1, 1), 400.0)


@pytest.mark.parametrize("method", [Method.INVERSE_DISTANCE, Method.ORDINARY_KRIGING])
def test_estimates_do_not_depend_on_model_rotation(method: Method) -> None:
    rng = np.random.default_rng(7)
    plain, rot = pair(33.0)
    lo, hi = plain.bounds()
    samples = rng.uniform(lo, hi, size=(150, 3))
    values = rng.lognormal(0.0, 0.6, 150)
    vg = VariogramModel(0.1, (Structure(ModelType.SPHERICAL, 0.9, Ellipsoid(45.0, 45.0, 45.0)),))
    search = Search(Ellipsoid(60.0, 60.0, 60.0), 4, 16)
    disc = (3, 3, 2)
    a = estimate(
        samples,
        values,
        plain.centroids(),
        search,
        method,
        variogram=vg,
        block_size=plain.size,
        discretisation=disc,
    )
    b = estimate(
        turn(samples, 33.0),
        values,
        rot.centroids(),
        search,
        method,
        block_size=rot.size,
        variogram=vg,
        discretisation=disc,
        block_axes=rot.axes,
    )
    assert np.isfinite(a.value).sum() > 0.8 * plain.blocks
    np.testing.assert_allclose(b.value, a.value, rtol=1e-9, equal_nan=True)
    np.testing.assert_allclose(b.variance, a.variance, rtol=1e-9, equal_nan=True)


def test_partial_block_fractions_of_a_turned_solid() -> None:
    plain, rot = pair(61.0)
    box = _box(PIVOT + np.array([13.0, 7.0, 3.0]), PIVOT + np.array([41.0, 29.0, 12.5]))
    a = block_fractions(plain, box, subdivisions=4)
    turned = Solid(turn(box.vertices, 61.0), box.triangles)
    b = block_fractions(rot, turned, subdivisions=4)
    np.testing.assert_allclose(b, a, atol=1e-12)
    # Known answer: total inside volume equals the box volume (sub-cell exact here
    # up to the sampling of boundary cells).
    vol = (a * plain.block_volume).sum()
    assert vol == pytest.approx(28.0 * 22.0 * 9.5, rel=0.06)


def test_iso_solid_and_bench_outlines_turn_with_the_model() -> None:
    plain, rot = pair(-20.0, (8, 8, 6))
    nx, ny, nz = plain.count
    mined = np.zeros((nz, ny, nx), bool)
    mined[3:, 1:7, 1:7] = True
    mined[2, 2:5, 2:5] = True
    s0, s1 = (
        iso_solid(plain, mined.ravel().astype(float), 0.5),
        iso_solid(rot, mined.ravel().astype(float), 0.5),
    )
    assert s1.volume() == pytest.approx(s0.volume(), rel=1e-9)
    assert np.allclose(np.sort(s1.vertices, axis=0), np.sort(turn(s0.vertices, -20.0), axis=0))
    l0, l1 = bench_outlines(plain, mined.ravel()), bench_outlines(rot, mined.ravel())
    assert len(l0) == len(l1)
    for a, b in zip(l0, l1, strict=True):
        assert a.layer == b.layer
        assert np.allclose(b.points, turn(a.points, -20.0))


def test_bench_design_of_a_turned_pit_has_the_same_volume() -> None:
    size = (10.0, 10.0, 10.0)
    plain = Grid(tuple(PIVOT), size, (20, 20, 8))
    c = plain.local_centroids()
    r = np.hypot(c[:, 0] - 100.0, c[:, 1] - 100.0)
    mined = (r < 30.0 + 0.8 * c[:, 2]) & (c[:, 2] > 20.0)
    geom = BenchGeometry(45.0, 10.0, 3.0)
    base = design_pit(plain, mined, geom, resolution=2.0)
    for az in (90.0, 30.0):
        rot = Grid(tuple(PIVOT), size, (20, 20, 8), az)
        pit = design_pit(rot, mined, geom, resolution=2.0)
        # 90 degrees maps the raster onto itself; 30 degrees resamples it.
        assert pit.volume() == pytest.approx(base.volume(), rel=1e-6 if az == 90.0 else 0.03)
        assert pit.inside(rot.centroids()[mined]).all()


def test_stopes_of_a_rotated_model_are_turned_boxes() -> None:
    plain, rot = pair(45.0, (6, 2, 4))
    grade = np.full(plain.blocks, 3.0)
    result = stope_envelope(plain, grade, np.ones(plain.blocks), (2, 1, 2), 1.0)
    t0, t1 = stope_triangles(plain, result), stope_triangles(rot, result)
    assert np.allclose(t1.reshape(-1, 3), turn(t0.reshape(-1, 3), 45.0))
    assert np.allclose(t0.reshape(-1, 3).min(axis=0), PIVOT)  # unrotated: world boxes


def test_section_polygons_of_a_turned_block() -> None:
    g = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 5.0), (1, 1, 2), 45.0)
    centre = g.centroids()[0]
    flat, polys = block_section_polygons(g, 0, float(centre[0]))  # x constant through the centre
    assert flat.tolist() == [0, 1]
    width = polys[:, 1, 0] - polys[:, 0, 0]
    assert np.allclose(width, 10.0 * math.sqrt(2.0))  # the square's diagonal
    assert np.allclose(polys[:, 2, 1] - polys[:, 1, 1], 5.0)
    flat, plan = block_section_polygons(g, 2, 7.0)
    assert flat.tolist() == [1]
    x, y = plan[0, :, 0], plan[0, :, 1]
    assert 0.5 * abs(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)) == pytest.approx(100.0)
    assert block_section_polygons(g, 0, 50.0)[0].size == 0


def test_csv_import_of_a_rotated_model(tmp_path: Path) -> None:
    _, rot = pair(32.5, (5, 4, 3))
    c = rot.centroids()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["XC", "YC", "ZC", "XINC", "YINC", "ZINC", "AU"])
    for n, (x, y, z) in enumerate(c):
        w.writerow([f"{x:.4f}", f"{y:.4f}", f"{z:.4f}", 10, 8, 5, n])
    path = tmp_path / "bm.csv"
    path.write_text(buf.getvalue())
    project = Project.create(tmp_path / "p.mcproj", "p")
    imp = import_block_csv(project, path, "imp", azimuth=32.5)
    g = imp.model.grid
    assert g.count == rot.count
    assert g.azimuth == 32.5
    assert np.allclose(g.origin, rot.origin, atol=1e-3)
    assert np.array_equal(imp.model.values("AU"), np.arange(rot.blocks, dtype=float))
    project.close()
