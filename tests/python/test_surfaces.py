"""Surfaces: interpolation on a known plane, CSV reading, block flagging."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.pitopt import Economics, PriceUnit, SlopeProfile, optimise_pit
from minecore.project import Project
from minecore.surfaces import (
    Surface,
    SurfaceError,
    flag_below,
    load_surface,
    read_points_csv,
    save_surface,
    surface_names,
)


def _plane(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 100.0 + 0.1 * x - 0.05 * y


def test_linear_interpolation_is_exact_on_a_plane() -> None:
    rng = np.random.default_rng(1)
    xy = rng.uniform(0, 100, (40, 2))
    corners = np.array([[0, 0], [100, 0], [0, 100], [100, 100]], float)
    xy = np.vstack([xy, corners])
    s = Surface(np.column_stack([xy, _plane(xy[:, 0], xy[:, 1])]))
    q = rng.uniform(0, 100, (200, 2))
    z, outside = s.elevation(q)
    assert outside == 0
    np.testing.assert_allclose(z, _plane(q[:, 0], q[:, 1]), atol=1e-9)
    z_out, n_out = s.elevation(np.array([[150.0, 50.0]]))
    assert n_out == 1
    assert np.isfinite(z_out).all()


def test_degenerate_surface_rejected() -> None:
    with pytest.raises(SurfaceError):
        Surface(np.array([[0.0, 0, 0], [1, 1, 1]]))


def test_csv_round_trip_and_flagging(tmp_path: Path) -> None:
    csv_path = tmp_path / "topo.csv"
    rows = ["Dogu;Kuzey;Kot"]
    for x in range(0, 101, 25):
        for y in range(0, 101, 25):
            rows.append(f"{x};{y};{_plane(np.array(x), np.array(y)):.3f}".replace(".", ","))
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    s = read_points_csv(csv_path)
    assert len(s.points) == 25
    project = Project.create(tmp_path / "p.mcproj", "p")
    save_surface(project, "topo", s)
    loaded = load_surface(project, "topo")
    np.testing.assert_allclose(loaded.points, s.points)
    grid = Grid((0.0, 0.0, 80.0), (10.0, 10.0, 5.0), (10, 10, 8))
    model = BlockModel.create(project, "bm", grid)
    rock, outside = flag_below(model, loaded)
    assert outside == 0
    c = grid.centroids()
    expected = c[:, 2] <= _plane(c[:, 0], c[:, 1])
    np.testing.assert_array_equal(model.values("kaya") == 1.0, expected)
    assert rock == int(expected.sum())
    # Air blocks carry no tonnage or cost in the pit optimiser.
    model.set_attributes({"au": np.full(grid.blocks, 5.0)}, {"au": {"unit": "g/t"}}, "g")
    econ = Economics(60.0, PriceUnit.GRAM, 0.9, 2.0, 15.0)
    with_air = optimise_pit(model, "au", econ, SlopeProfile(45.0), [1.0], rock_attribute="kaya")
    total_t = with_air.rows[0].ore_t + with_air.rows[0].waste_t
    assert total_t == pytest.approx(rock * grid.block_volume * 2.7)
    project.close()


def test_estimation_mask_leaves_air_empty(tmp_path: Path) -> None:
    from minecore.blockmodel.workflow import SampleSet, estimate_blocks  # noqa: PLC0415
    from minecore.estimation import Ellipsoid, Method, Search  # noqa: PLC0415

    project = Project.create(tmp_path / "m.mcproj", "m")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (5, 5, 4))
    model = BlockModel.create(project, "bm", grid)
    rng = np.random.default_rng(0)
    xyz = rng.uniform(0, 50, (60, 3)) * np.array([1, 1, 0.8])
    samples = SampleSet(xyz, rng.uniform(0, 2, 60), np.arange(60, dtype=np.int64), "g/t")
    mask = grid.centroids()[:, 2] < 30
    missed = estimate_blocks(
        model,
        samples,
        "au",
        method=Method.INVERSE_DISTANCE,
        search=Search(Ellipsoid.isotropic(100.0)),
        mask=mask,
    )
    au = model.values("au")
    assert missed == 0
    assert np.isfinite(au[mask]).all()
    assert np.isnan(au[~mask]).all()
    assert (model.values("au_n")[~mask] == 0).all()
    # A second domain estimated into the same attribute keeps the first one.
    upper = SampleSet(xyz, np.full(60, 7.0), np.arange(60, dtype=np.int64), "g/t")
    estimate_blocks(
        model,
        upper,
        "au",
        method=Method.NEAREST,
        search=Search(Ellipsoid.isotropic(100.0)),
        mask=~mask,
    )
    both = model.values("au")
    assert np.array_equal(both[mask], au[mask])
    assert (both[~mask] == 7.0).all()
    assert len(model.attributes["au"]["domains"]) == 2
    assert (model.values("au_n")[mask] > 0).all()
    project.close()


def test_saving_under_an_existing_name_replaces(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "r.mcproj", "r")
    pts = np.array([[0.0, 0.0, 1.0], [10.0, 0.0, 1.0], [0.0, 10.0, 1.0]])
    save_surface(project, "s", Surface(pts))
    save_surface(project, "s", Surface(pts + np.array([0.0, 0.0, 5.0])))
    assert surface_names(project) == ["s"]
    assert load_surface(project, "s").points[:, 2].tolist() == [6.0, 6.0, 6.0]
    project.close()
