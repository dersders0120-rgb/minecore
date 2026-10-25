"""Bench design (face angle, bench height, berm) around an optimised pit.

The designed pit is the shallowest surface with the given bench geometry
that still contains the floor of every mined block column:

    z(p) = min over floor points q of  floor(q) + stair(|p - q|)

where ``stair(d)`` climbs ``bench_height`` over a horizontal run of
``bench_height / tan(face_angle)`` and then stays flat for ``berm`` metres,
repeatedly. It is evaluated on a raster: for every distinct floor elevation
``L`` the Euclidean distance transform (scipy) gives the distance to the
columns whose floor is at or below ``L``, and the surface is the minimum of
``L + stair(distance)`` over all levels. Above the topography the surface
is cut by the topography (or the top of rock).

Corners are rounded (Euclidean distance), which corresponds to a design whose
bench strings are offset with round joins. Crest and toe strings are contours
of the design surface (contourpy).

Ramp (optional): a spiral haul road is traced bottom-up from the pit floor.
At each angular step around the pit centre the road centre sits half a road
width outside the wall contour at the road's current elevation (the wall as
already cut back by the road below), and the elevation rises by ``gradient``
times the travelled length until the road meets the original surface. The
start angle is adjusted so the road exits at the requested azimuth. The road
cells then act as extra floor points at the road elevation, so the same
envelope cuts the road into the wall and sets the benches above it back (the
road is excavated, not placed on the wall). The road surface is flat across
its width; no road-specific batters are designed.

Switchback ramp (``switchback_span`` > 0): instead of circling the pit, the
road climbs back and forth across one wall, inside an arc of the given span
centred on the exit azimuth. At each end of the arc the road turns on a flat
pad (``turn_length``, default 1.5 road widths); the leg just climbed is cut
into the wall before the turn, so the next leg runs above it and the benches
above both legs are set back. The road exits wherever its last leg meets the
original surface, inside the arc.

Geotechnical sectors (optional): the wall geometry may change with the wall
position. A sector starts at an azimuth (degrees from north, clockwise, seen
from the pit: 0 is the north wall) and runs to the next sector's start. Each
raster cell takes the sector of the direction from its nearest floor cell at
that level (the Euclidean distance transform returns it), i.e. of the wall's
outward normal. All sectors share the bench height so bench levels line up;
face angle and berm may differ. Sector changes are abrupt (no blending).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from scipy.ndimage import distance_transform_edt

from minecore import surfaces
from minecore.blockmodel import BlockModel, Grid
from minecore.io.dxf import Polyline, write_dxf
from minecore.pitopt.economics import PitOptError
from minecore.project import Project
from minecore.surfaces import Surface

Floats = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class BenchGeometry:
    face_angle: float  # degrees
    bench_height: float  # m
    berm: float  # m

    def __post_init__(self) -> None:
        if not 10.0 <= self.face_angle <= 89.0:
            raise PitOptError("bench face angle must be between 10 and 89 degrees")
        if self.bench_height <= 0 or self.berm < 0:
            raise PitOptError("bench height must be > 0 and berm width >= 0")

    @property
    def overall_angle(self) -> float:
        run = self.bench_height / math.tan(math.radians(self.face_angle)) + self.berm
        return math.degrees(math.atan2(self.bench_height, run))

    def rise(self, d: Floats) -> Floats:
        """Height of the stair profile at horizontal distance ``d`` (>= 0)."""
        face = self.bench_height / math.tan(math.radians(self.face_angle))
        cycle = face + self.berm
        n = np.floor(d / cycle)
        r = d - n * cycle
        return np.asarray(n * self.bench_height + np.minimum(r / face, 1.0) * self.bench_height)


@dataclass(frozen=True)
class Sector:
    azimuth: float  # degrees from north, clockwise; where this sector starts
    geometry: BenchGeometry


def _check_sectors(sectors: tuple[Sector, ...], geometry: BenchGeometry) -> tuple[Sector, ...]:
    if not sectors:
        return ()
    if any(abs(s.geometry.bench_height - geometry.bench_height) > 1e-9 for s in sectors):
        raise PitOptError("all geotechnical sectors must use the design bench height")
    ordered = tuple(sorted(sectors, key=lambda s: s.azimuth % 360.0))
    starts = [s.azimuth % 360.0 for s in ordered]
    if len(set(starts)) != len(starts):
        raise PitOptError("two geotechnical sectors start at the same azimuth")
    return ordered


def parse_sectors(text: str, bench_height: float) -> tuple[Sector, ...]:
    """Sectors from ``"azimuth:face:berm"`` items separated by ``;`` or ``,``."""
    out = []
    for item in text.replace(",", ";").split(";"):
        if not item.strip():
            continue
        parts = item.split(":")
        try:
            az, face, berm = (float(v) for v in parts)
        except ValueError:
            raise PitOptError(f"sector {item.strip()!r} is not azimuth:face:berm") from None
        out.append(Sector(az, BenchGeometry(face, bench_height, berm)))
    return tuple(out)


@dataclass(frozen=True)
class Ramp:
    width: float  # m
    gradient: float  # rise over run, e.g. 0.10 for 10 %
    exit_azimuth: float = 0.0  # degrees from north, clockwise; where the road leaves the pit
    clockwise: bool = True  # direction of travel going down
    switchback_span: float = 0.0  # arc of wall (degrees) a switchback road uses; 0 = spiral
    turn_length: float | None = None  # flat road at each switchback (m); default 1.5 x width

    def __post_init__(self) -> None:
        if self.width <= 0 or not 0.01 <= self.gradient <= 0.25:
            raise PitOptError("ramp width must be > 0 and gradient between 1 % and 25 %")
        if self.switchback_span != 0.0 and not 20.0 <= self.switchback_span <= 300.0:
            raise PitOptError("switchback span must be 0 (spiral) or 20 to 300 degrees")
        if self.turn_length is not None and self.turn_length < 0:
            raise PitOptError("switchback turn length must be >= 0")

    @property
    def switchback(self) -> bool:
        return self.switchback_span > 0.0

    @property
    def pad(self) -> float:
        """Flat road length at each switchback."""
        return 1.5 * self.width if self.turn_length is None else self.turn_length


@dataclass(frozen=True)
class DesignedPit:
    x: Floats  # raster column coordinates
    y: Floats  # raster row coordinates
    z: Floats  # ny x nx design surface (cut by topography)
    top: Floats  # ny x nx original surface
    geometry: BenchGeometry
    reaches_edge: bool  # design meets the raster border below the original surface
    on_model: Mask  # ny x nx raster cells over the block model area
    ramp: Floats | None = None  # road centreline, n x 3, from the exit down to the floor
    sectors: tuple[Sector, ...] = ()  # geotechnical sectors, sorted by start azimuth

    @property
    def ramp_length(self) -> float:
        if self.ramp is None or len(self.ramp) < 2:
            return 0.0
        return float(np.linalg.norm(np.diff(self.ramp, axis=0), axis=1).sum())

    def surface(self) -> Surface:
        xx, yy = np.meshgrid(self.x, self.y)
        return Surface(np.column_stack([xx.ravel(), yy.ravel(), self.z.ravel()]))

    def volume(self, outside_model_only: bool = False) -> float:
        """Excavated volume between the original surface and the design (m3).

        ``outside_model_only``: only the part beyond the block model area, which
        the block model cannot value (the optimiser treats the model edge as
        unconstrained, so a designed pit can need ground outside the model).
        """
        cell = float((self.x[1] - self.x[0]) * (self.y[1] - self.y[0]))
        depth = np.clip(self.top - self.z, 0.0, None)
        if outside_model_only:
            depth = np.where(self.on_model, 0.0, depth)
        return float(depth.sum() * cell)

    def inside(self, points: Floats) -> Mask:
        """Points between the design surface and the original surface."""
        pts = np.asarray(points, np.float64)
        dx, dy = self.x[1] - self.x[0], self.y[1] - self.y[0]
        i = np.clip(np.round((pts[:, 0] - self.x[0]) / dx).astype(int), 0, len(self.x) - 1)
        j = np.clip(np.round((pts[:, 1] - self.y[0]) / dy).astype(int), 0, len(self.y) - 1)
        return np.asarray((pts[:, 2] > self.z[j, i]) & (pts[:, 2] < self.top[j, i]))

    def strings(self, name: str = "DESIGN") -> list[Polyline]:
        """Toe and crest contours at every bench level inside the pit."""
        from contourpy import contour_generator  # noqa: PLC0415

        cut = np.where(self.z < self.top - 1e-6, self.z, np.nan)
        if not np.isfinite(cut).any():
            return []
        gen = contour_generator(self.x, self.y, cut)
        h = self.geometry.bench_height
        lo, hi = float(np.nanmin(cut)), float(np.nanmax(cut))
        lines: list[Polyline] = []
        level = lo
        while level <= hi + 1e-9:
            for layer, z in ((f"{name}_TOE", level + 1e-3), (f"{name}_CREST", level + h - 1e-3)):
                if z > hi:
                    continue
                for seg in gen.lines(z):
                    pts = np.asarray(seg, np.float64)
                    if len(pts) >= 2:
                        closed = bool(np.allclose(pts[0], pts[-1]))
                        lines.append(
                            Polyline(layer, np.column_stack([pts, np.full(len(pts), z)]), closed)
                        )
            level += h
        if self.ramp is not None and len(self.ramp) >= 2:
            lines.append(Polyline(f"{name}_RAMP", self.ramp, closed=False))
        return lines


def design_pit(  # noqa: PLR0913 - options are keyword-only
    grid: Grid,
    mined: Mask,
    geometry: BenchGeometry,
    *,
    topography: Surface | None = None,
    rock: Mask | None = None,
    resolution: float | None = None,
    margin: float | None = None,
    ramp: Ramp | None = None,
    sectors: tuple[Sector, ...] = (),
) -> DesignedPit:
    nx, ny, nz = grid.count
    sectors = _check_sectors(tuple(sectors), geometry)
    flattest = min([geometry, *(s.geometry for s in sectors)], key=lambda g: g.overall_angle)
    cube = np.asarray(mined, bool).reshape(nz, ny, nx)
    if not cube.any():
        raise PitOptError("no mined blocks")
    oz = grid.origin[2]
    sx, sy, sz = grid.size
    res = resolution or min(sx, sy) / 2.0
    depth = nz * sz
    pad = margin if margin is not None else depth / math.tan(math.radians(flattest.overall_angle))
    # The raster is world-aligned (sectors and ramps use world azimuths); a rotated
    # model is sampled through its own cell index.
    lo, hi = grid.bounds()
    x = np.arange(lo[0] - pad + res / 2, hi[0] + pad, res)
    y = np.arange(lo[1] - pad + res / 2, hi[1] + pad, res)
    xx, yy = np.meshgrid(x, y)
    # Column floor (bottom of the deepest mined block) on the raster, +inf if unmined.
    lowest = np.argmax(cube, axis=0)
    floor_col = np.where(cube.any(axis=0), oz + lowest * sz, np.inf)
    ci, cj = _raster_cells(grid, xx, yy)
    on_grid = (ci >= 0) & (ci < nx) & (cj >= 0) & (cj < ny)
    floor = np.full(xx.shape, np.inf)
    floor[on_grid] = floor_col[cj[on_grid], ci[on_grid]]
    design = _envelope(floor, geometry, res, sectors)
    top = _original_surface(grid, xx, yy, topography, rock, cube)
    road = None
    if ramp is not None:
        road, road_floor = _trace_ramp(x, y, design, top, floor, ramp, geometry, res, sectors)
        design = _envelope(np.minimum(floor, road_floor), geometry, res, sectors)
    z = np.minimum(design, top)
    below = design < top - 1e-6
    edge = bool(below[0, :].any() or below[-1, :].any() or below[:, 0].any() or below[:, -1].any())
    return DesignedPit(x, y, z, top, geometry, edge, on_grid, road, sectors)


def _envelope(
    floor: Floats, geometry: BenchGeometry, res: float, sectors: tuple[Sector, ...] = ()
) -> Floats:
    design = np.full(floor.shape, np.inf)
    if sectors:
        starts = np.array([s.azimuth % 360.0 for s in sectors])
        jj, ii = np.indices(floor.shape)
    for level in np.unique(floor[np.isfinite(floor)]):
        if not sectors:
            dist = distance_transform_edt(floor > level, sampling=res)
            design = np.minimum(design, level + geometry.rise(np.asarray(dist, np.float64)))
            continue
        dist, (nj, ni) = distance_transform_edt(floor > level, sampling=res, return_indices=True)
        d = np.asarray(dist, np.float64)
        az = np.degrees(np.arctan2(ii - ni, jj - nj)) % 360.0
        # Sector k covers [start_k, start_k+1); azimuths before the first start
        # belong to the last sector (wrap-around).
        which = (np.searchsorted(starts, az, side="right") - 1) % len(sectors)
        rise = np.empty_like(d)
        for k, sector in enumerate(sectors):
            sel = which == k
            rise[sel] = sector.geometry.rise(d[sel])
        design = np.minimum(design, level + rise)
    return design


def _trace_ramp(  # noqa: PLR0913, PLR0917, PLR0915 - raster inputs, one tracing loop
    x: Floats,
    y: Floats,
    design: Floats,
    top: Floats,
    floor: Floats,
    ramp: Ramp,
    geometry: BenchGeometry,
    res: float,
    sectors: tuple[Sector, ...] = (),
) -> tuple[Floats, Floats]:
    """Road centreline (n x 3, exit first) and a raster of road elevations (+inf off road).

    Traced bottom-up so every lap sits on the wall as already pushed back by
    the laps below it; the start angle is adjusted until the road leaves the
    pit at the requested exit azimuth.
    """
    ny, nx = design.shape
    pit = np.isfinite(floor)
    jj, ii = np.nonzero(pit)
    cx, cy = float(x[ii].mean()), float(y[jj].mean())
    bottom = float(floor[pit].min())
    high = float(np.nanmax(top))
    reach = float(np.hypot(x[-1] - x[0], y[-1] - y[0]))
    radii = np.arange(0.0, reach, res / 2)
    xx, yy = np.meshgrid(x, y)

    # Laps are placed on the wall at the overall slope (no stairs), so the wall
    # contour moves smoothly; the stair wall is never outside it, which keeps the
    # whole road width in rock. The cut behind a section uses the flattest sector.
    def smooth_of(g: BenchGeometry) -> BenchGeometry:
        return BenchGeometry(g.overall_angle, g.bench_height, 0.0)

    smooth_sectors = tuple(Sector(s.azimuth, smooth_of(s.geometry)) for s in sectors)
    flattest = min([geometry, *(s.geometry for s in sectors)], key=lambda g: g.overall_angle)
    smooth = smooth_of(flattest)
    slope = math.tan(math.radians(flattest.overall_angle))
    design = _envelope(floor, smooth_of(geometry), res, smooth_sectors)
    up = -1.0 if ramp.clockwise else 1.0  # clockwise going down = anticlockwise going up

    def lookup(grid: Floats, px: Floats, py: Floats) -> Floats:
        i = np.clip(np.round((px - x[0]) / res).astype(int), 0, nx - 1)
        j = np.clip(np.round((py - y[0]) / res).astype(int), 0, ny - 1)
        return np.asarray(grid[j, i], np.float64)

    def cut(current: Floats, p: tuple[float, float], h: float) -> None:
        """Lower the wall behind a road section (the road is excavated into it)."""
        span = (high - h) / slope + ramp.width
        i0, i1 = np.searchsorted(x, [p[0] - span, p[0] + span])
        j0, j1 = np.searchsorted(y, [p[1] - span, p[1] + span])
        d = np.hypot(xx[j0:j1, i0:i1] - p[0], yy[j0:j1, i0:i1] - p[1])
        lowered = h + smooth.rise(np.maximum(d - ramp.width / 2, 0.0))
        current[j0:j1, i0:i1] = np.minimum(current[j0:j1, i0:i1], lowered)

    target = math.radians(ramp.exit_azimuth)
    half = math.radians(ramp.switchback_span) / 2

    def walk(start: float, gradient: float) -> tuple[Floats, float]:
        current = design.copy()
        theta, h, travelled = start, bottom, 0.0
        heading, flat_left = up, 0.0
        pts: list[tuple[float, float, float]] = []
        pending: list[tuple[float, tuple[float, float], float]] = []
        for _ in range(200_000):
            ray = lookup(current, cx + radii * math.sin(theta), cy + radii * math.cos(theta))
            hit = np.flatnonzero(ray > h + 1e-3)
            if hit.size == 0:
                break
            r_road = float(radii[hit[0]]) + ramp.width / 2
            p = (cx + r_road * math.sin(theta), cy + r_road * math.cos(theta))
            if pts:
                step = math.hypot(p[0] - pts[-1][0], p[1] - pts[-1][1])
                travelled += step
                h += gradient * max(step - flat_left, 0.0)  # switchback pads are flat
                flat_left = max(flat_left - step, 0.0)
            pts.append((p[0], p[1], h))
            if lookup(top, np.array([p[0]]), np.array([p[1]]))[0] <= h:
                break  # the road has reached the original surface: exit
            # A section's cut is applied half a lap later: it shapes the wall for the
            # lap above, not for the road's own next steps.
            pending.append((travelled, p, h))
            delay = max(math.pi * r_road, 4.0 * ramp.width)
            while pending and travelled - pending[0][0] > delay:
                _, q, hq = pending.pop(0)
                cut(current, q, hq)
            if ramp.switchback and heading * math.remainder(theta - target, 2 * math.pi) >= half:
                # Switchback: the leg below is cut now, so the next leg runs above it,
                # and the road turns on a flat pad.
                for _, q, hq in pending:
                    cut(current, q, hq)
                pending.clear()
                heading, flat_left = -heading, ramp.pad
            theta += heading * res / max(r_road, res)  # about one raster cell of road per step
        if len(pts) < 3:
            raise PitOptError("the pit is too small for a ramp of this width and gradient")
        return np.asarray(pts, np.float64), theta

    if ramp.switchback:
        # Legs sweep the wall arc centred on the exit, starting from one end at the floor.
        xyz, _ = walk(target - up * half, ramp.gradient)
    else:
        start = target
        for _ in range(5):  # move the start until the exit lands on the target azimuth
            xyz, end = walk(start, ramp.gradient)
            miss = math.remainder(target - end, 2 * math.pi)
            if abs(miss) < math.radians(2.0):
                break
            start += miss
            xyz, end = walk(start, ramp.gradient)
    line = xyz[::-1]
    road = np.full(design.shape, np.inf)
    nearest = np.full(design.shape, np.inf)  # each road cell takes its nearest section
    for (x0, y0, z0), (x1, y1, z1) in itertools.pairwise(line):
        lo_x, hi_x = min(x0, x1) - ramp.width, max(x0, x1) + ramp.width
        lo_y, hi_y = min(y0, y1) - ramp.width, max(y0, y1) + ramp.width
        i0, i1 = np.searchsorted(x, [lo_x, hi_x])
        j0, j1 = np.searchsorted(y, [lo_y, hi_y])
        if i0 == i1 or j0 == j1:
            continue
        sx, sy = xx[j0:j1, i0:i1], yy[j0:j1, i0:i1]
        dx, dy = x1 - x0, y1 - y0
        t = np.clip(((sx - x0) * dx + (sy - y0) * dy) / max(dx * dx + dy * dy, 1e-12), 0, 1)
        dist = np.hypot(sx - (x0 + t * dx), sy - (y0 + t * dy))
        closer = (dist <= ramp.width / 2) & (dist < nearest[j0:j1, i0:i1])
        nearest[j0:j1, i0:i1] = np.where(closer, dist, nearest[j0:j1, i0:i1])
        road[j0:j1, i0:i1] = np.where(closer, z0 + t * (z1 - z0), road[j0:j1, i0:i1])
    quantum = 0.25
    return line, np.floor(road / quantum) * quantum


def _original_surface(  # noqa: PLR0913, PLR0917 - raster inputs
    grid: Grid,
    xx: Floats,
    yy: Floats,
    topography: Surface | None,
    rock: Mask | None,
    cube: Mask,
) -> Floats:
    nx, ny, nz = grid.count
    oz, sz = grid.origin[2], grid.size[2]
    if topography is not None:
        z, _ = topography.elevation(np.column_stack([xx.ravel(), yy.ravel()]))
        return np.asarray(z, np.float64).reshape(xx.shape)
    if rock is not None:
        r = np.asarray(rock, bool).reshape(cube.shape)
        top_k = nz - np.argmax(r[::-1], axis=0)
        col_top = np.where(r.any(axis=0), oz + top_k * sz, oz)
    else:
        col_top = np.full((ny, nx), oz + nz * sz)
    ci, cj = _raster_cells(grid, xx, yy)
    return np.asarray(col_top[np.clip(cj, 0, ny - 1), np.clip(ci, 0, nx - 1)], np.float64)


def _raster_cells(
    grid: Grid, xx: Floats, yy: Floats
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.int64]]:
    """Block column (i, j) under each raster point, unclipped (may be off the model)."""
    pts = np.column_stack([xx.ravel(), yy.ravel(), np.full(xx.size, grid.origin[2])])
    ijk = grid.cell_index(pts)
    return ijk[:, 0].reshape(xx.shape), ijk[:, 1].reshape(xx.shape)


@dataclass(frozen=True)
class DesignReport:
    pit: DesignedPit
    shell_blocks: int
    design_blocks: int
    shell_value: float
    design_value: float  # sum of pit_value of blocks inside the design


def design_from_model(  # noqa: PLR0913 - options are keyword-only
    project: Project,
    model: BlockModel,
    geometry: BenchGeometry,
    *,
    last_shell: int | None = None,
    topography: str | None = None,
    save_surface: str | None = None,
    flag_attribute: str | None = None,
    dxf: Path | None = None,
    ramp: Ramp | None = None,
    sectors: tuple[Sector, ...] = (),
) -> DesignReport:
    """Design benches around shells ``1..last_shell`` of a stored pit optimisation."""
    if "pit_shell" not in model.attributes:
        raise PitOptError(f"{model.name} has no pit_shell; run the pit optimisation first")
    shell = model.values("pit_shell")
    last = last_shell or int(np.nanmax(shell))
    mined = (shell >= 1) & (shell <= last)
    topo = surfaces.load_surface(project, topography) if topography else None
    rock = model.values("kaya") > 0.5 if "kaya" in model.attributes else None
    pit = design_pit(
        model.grid, mined, geometry, topography=topo, rock=rock, ramp=ramp, sectors=sectors
    )
    inside = pit.inside(model.grid.centroids())
    if rock is not None:
        inside &= rock
    value = np.nan_to_num(model.values("pit_value")) if "pit_value" in model.attributes else None
    if save_surface:
        surfaces.save_surface(project, save_surface, pit.surface())
    if flag_attribute:
        model.set_attributes(
            {flag_attribute: inside.astype(np.float64)},
            {
                flag_attribute: {
                    "unit": None,
                    "design": geometry.__dict__,
                    "sectors": [[s.azimuth, s.geometry.__dict__] for s in sectors],
                    "shell": last,
                }
            },
            "Flag blocks inside the bench design",
        )
    if dxf is not None:
        idx = np.arange(pit.z.size).reshape(pit.z.shape)
        a, b = idx[:-1, :-1].ravel(), idx[:-1, 1:].ravel()
        c, d = idx[1:, 1:].ravel(), idx[1:, :-1].ravel()
        tri = np.concatenate([np.column_stack([a, b, c]), np.column_stack([a, c, d])])
        pts = pit.surface().points
        write_dxf(dxf, pit.strings(), [("DESIGN_SURFACE", pts[tri])])
    return DesignReport(
        pit,
        int(mined.sum()),
        int(inside.sum()),
        float(value[mined].sum()) if value is not None else math.nan,
        float(value[inside].sum()) if value is not None else math.nan,
    )
