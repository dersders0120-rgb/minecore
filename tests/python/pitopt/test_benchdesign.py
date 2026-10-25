"""Bench design around a block pit."""

from __future__ import annotations

import math

import numpy as np
import pytest

from minecore.blockmodel import Grid
from minecore.pitopt.benchdesign import BenchGeometry, Ramp, Sector, design_pit
from minecore.pitopt.economics import PitOptError


def test_stair_profile_by_hand() -> None:
    g = BenchGeometry(face_angle=45.0, bench_height=10.0, berm=5.0)
    # Face run 10 m, cycle 15 m.
    d = np.array([0.0, 5.0, 10.0, 12.0, 15.0, 20.0, 30.0])
    assert g.rise(d).tolist() == pytest.approx([0.0, 5.0, 10.0, 10.0, 10.0, 15.0, 20.0])
    assert g.overall_angle == pytest.approx(math.degrees(math.atan2(10, 15)))
    with pytest.raises(PitOptError):
        BenchGeometry(95.0, 10.0, 5.0)


def test_single_column_cone() -> None:
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (21, 21, 5))
    mined = np.zeros((5, 21, 21), bool)
    mined[2:, 10, 10] = True  # column floor at z = 20, model top at 50
    g = BenchGeometry(60.0, 10.0, 4.0)
    pit = design_pit(grid, mined.ravel(), g, resolution=1.0, margin=0.0)
    assert not pit.reaches_edge
    j = int(np.argmin(np.abs(pit.y - 105.0)))
    for dx in (0.0, 3.0, 8.0, 20.0, 40.0):
        i = int(np.argmin(np.abs(pit.x - (105.0 + 5.0 + dx))))  # east of the column edge
        d = max(pit.x[i] - 110.0, 0.0)
        expect = min(20.0 + float(g.rise(np.array([d]))[0]), 50.0)
        assert pit.z[j, i] == pytest.approx(expect, abs=1.0)
    # The design contains the column and is never deeper than its floor.
    assert pit.z.min() == pytest.approx(20.0)
    centres = np.array([[105.0, 105.0, 25.0], [105.0, 105.0, 45.0], [105.0, 105.0, 15.0]])
    assert pit.inside(centres).tolist() == [True, True, False]
    assert len(pit.strings()) > 0
    assert pit.volume(outside_model_only=True) == 0.0


def test_design_contains_every_mined_block() -> None:
    rng = np.random.default_rng(3)
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (16, 14, 6))
    nx, ny, nz = grid.count
    depth = rng.integers(0, 4, size=(ny, nx))
    mined = np.zeros((nz, ny, nx), bool)
    for j in range(ny):
        for i in range(nx):
            if depth[j, i]:
                mined[nz - depth[j, i] :, j, i] = True
    pit = design_pit(grid, mined.ravel(), BenchGeometry(70.0, 10.0, 3.0))
    c = grid.centroids()[mined.ravel()]
    assert pit.inside(c).all()
    assert pit.volume() >= mined.sum() * grid.block_volume * 0.99


def _cone_pit() -> tuple[Grid, np.ndarray]:
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (40, 40, 12))
    c = grid.centroids()
    r = np.hypot(c[:, 0] - 200.0, c[:, 1] - 200.0)
    return grid, (r < 60.0 + c[:, 2]) & (c[:, 2] > 0.0)


@pytest.mark.parametrize("clockwise", [True, False])
def test_spiral_ramp_reaches_the_floor_at_its_gradient(clockwise: bool) -> None:
    grid, mined = _cone_pit()
    g = BenchGeometry(70.0, 10.0, 6.0)
    plain = design_pit(grid, mined, g)
    ramp = Ramp(width=25.0, gradient=0.10, exit_azimuth=90.0, clockwise=clockwise)
    pit = design_pit(grid, mined, g, ramp=ramp)
    road = pit.ramp
    assert road is not None
    run = np.linalg.norm(np.diff(road[:, :2], axis=0), axis=1)
    # Constant gradient from the crest (model top 120 m) down to the floor (0 m).
    assert road[0, 2] == pytest.approx(120.0, abs=0.6)  # within one 5 m road step
    assert road[-1, 2] == pytest.approx(0.0, abs=0.5)
    assert -np.diff(road[:, 2]).sum() / run.sum() == pytest.approx(0.10, rel=1e-3)
    assert pit.ramp_length == pytest.approx(run.sum() * math.sqrt(1 + 0.01), rel=1e-3)
    # The road leaves the pit on the east side and winds in the requested direction.
    assert road[0, 0] > 330.0
    assert abs(road[0, 1] - 200.0) < 20.0
    first_turn = road[min(20, len(road) - 1)]
    assert (first_turn[1] < 200.0) == clockwise  # clockwise from east heads south
    # The road is cut into the wall: the design surface follows the road elevation.
    i = np.clip(np.round((road[:, 0] - pit.x[0]) / (pit.x[1] - pit.x[0])).astype(int), 0, None)
    j = np.clip(np.round((road[:, 1] - pit.y[0]) / (pit.y[1] - pit.y[0])).astype(int), 0, None)
    gap = pit.z[j, i] - road[:, 2]
    assert gap.max() <= 0.5
    assert np.median(np.abs(gap)) <= 0.5
    # A ramp only adds excavation, and pushes the walls above it back.
    assert (pit.z <= plain.z + 1e-9).all()
    assert pit.volume() > plain.volume() * 1.05
    assert any(line.layer == "DESIGN_RAMP" for line in pit.strings())


@pytest.mark.parametrize("span", [120.0, 60.0])
def test_switchback_ramp_stays_on_one_wall(span: float) -> None:
    from scipy.spatial import cKDTree  # noqa: PLC0415

    grid, mined = _cone_pit()
    g = BenchGeometry(70.0, 10.0, 6.0)
    plain = design_pit(grid, mined, g)
    spiral = design_pit(grid, mined, g, ramp=Ramp(25.0, 0.10, 90.0))
    ramp = Ramp(25.0, 0.10, 90.0, switchback_span=span)
    pit = design_pit(grid, mined, g, ramp=ramp)
    road = pit.ramp
    assert road is not None
    assert road[0, 2] == pytest.approx(120.0, abs=0.6)
    assert road[-1, 2] == pytest.approx(0.0, abs=0.5)
    # Every road point lies inside the arc centred on the exit azimuth (east).
    az = np.degrees(np.arctan2(road[:, 0] - 200.0, road[:, 1] - 200.0)) % 360.0
    tol = 8.0  # one road step at the floor radius
    assert az.min() >= 90.0 - span / 2 - tol
    assert az.max() <= 90.0 + span / 2 + tol
    # The road reverses direction at least twice and is flat on each pad.
    sweep = np.diff(np.unwrap(np.radians(az)))
    turns = int(np.count_nonzero(np.diff(np.sign(sweep[np.abs(sweep) > 1e-6])) != 0))
    assert turns >= 2
    run = np.linalg.norm(np.diff(road[:, :2], axis=0), axis=1)
    drop = -np.diff(road[:, 2])
    assert drop.min() >= -1e-9  # never climbs going down
    assert run[drop < 1e-9].sum() >= turns * ramp.pad * 0.8
    # Off the pads the gradient is the design gradient.
    sloped = drop > 1e-9
    assert (drop[sloped] / run[sloped]).max() <= 0.10 + 1e-9
    # Legs never overlap: points more than a bench apart in height are at least a
    # road width apart in plan.
    dist, idx = cKDTree(road[:, :2]).query(road[:, :2], k=60)
    apart = np.abs(road[idx, 2] - road[:, 2:3]) > 5.0
    assert dist[apart].min() >= ramp.width
    # The road is cut into the wall, and only adds excavation.
    i = np.clip(np.round((road[:, 0] - pit.x[0]) / (pit.x[1] - pit.x[0])).astype(int), 0, None)
    j = np.clip(np.round((road[:, 1] - pit.y[0]) / (pit.y[1] - pit.y[0])).astype(int), 0, None)
    assert (pit.z[j, i] - road[:, 2]).max() <= 0.5
    assert (pit.z <= plain.z + 1e-9).all()
    assert pit.volume() > spiral.volume()  # back-and-forth legs cost more waste than a spiral


def test_ramp_validation() -> None:
    with pytest.raises(PitOptError):
        Ramp(width=0.0, gradient=0.1)
    with pytest.raises(PitOptError):
        Ramp(width=20.0, gradient=0.5)
    with pytest.raises(PitOptError):
        Ramp(width=20.0, gradient=0.1, switchback_span=10.0)
    with pytest.raises(PitOptError):
        Ramp(width=20.0, gradient=0.1, switchback_span=90.0, turn_length=-1.0)
    assert Ramp(width=20.0, gradient=0.1, switchback_span=90.0).pad == 30.0


def test_geotechnical_sectors_set_the_slope_by_wall_position() -> None:
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (21, 21, 5))
    mined = np.zeros((5, 21, 21), bool)
    mined[2:, 10, 10] = True  # floor at z = 20 in the column x, y = 100..110
    base = BenchGeometry(45.0, 10.0, 0.0)
    steep, flat = BenchGeometry(70.0, 10.0, 0.0), BenchGeometry(30.0, 10.0, 0.0)
    # East half (0..180 deg) steep, west half flat. Berm 0: rise = d * tan(face).
    sectors = (Sector(180.0, flat), Sector(0.0, steep))
    pit = design_pit(grid, mined.ravel(), base, resolution=1.0, margin=60.0, sectors=sectors)
    j = int(np.argmin(np.abs(pit.y - 105.0)))
    for d in (5.0, 15.0):
        east = int(np.argmin(np.abs(pit.x - (110.0 + d))))
        west = int(np.argmin(np.abs(pit.x - (100.0 - d))))
        de, dw = pit.x[east] - 110.0, 100.0 - pit.x[west]
        z_east = min(20.0 + de * math.tan(math.radians(70)), 50.0)
        z_west = min(20.0 + dw * math.tan(math.radians(30)), 50.0)
        assert pit.z[j, east] == pytest.approx(z_east, abs=1.5)
        assert pit.z[j, west] == pytest.approx(z_west, abs=1.0)
    # The flat west wall daylights much further out than the steep east wall.
    out = pit.z[j] >= 50.0 - 1e-6
    west_crest = 100.0 - pit.x[~out & (pit.x < 100)].min()
    east_crest = pit.x[~out & (pit.x > 110)].max() - 110.0
    assert west_crest == pytest.approx(30.0 / math.tan(math.radians(30)), abs=1.5)
    assert east_crest == pytest.approx(30.0 / math.tan(math.radians(70)), abs=1.5)
    with pytest.raises(PitOptError):
        design_pit(grid, mined.ravel(), base, sectors=(Sector(0.0, BenchGeometry(60, 15, 0)),))
    with pytest.raises(PitOptError):
        design_pit(grid, mined.ravel(), base, sectors=(Sector(0.0, steep), Sector(360.0, flat)))


def test_sectors_with_a_ramp() -> None:
    grid, mined = _cone_pit()
    g = BenchGeometry(65.0, 10.0, 6.0)
    sectors = (Sector(90.0, BenchGeometry(50.0, 10.0, 8.0)), Sector(270.0, g))
    pit = design_pit(grid, mined, g, ramp=Ramp(15.0, 0.10), sectors=sectors)
    assert pit.ramp is not None
    assert pit.inside(grid.centroids()[mined]).all()
