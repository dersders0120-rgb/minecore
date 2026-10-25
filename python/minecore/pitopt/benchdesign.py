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
bench strings are offset with round joins. Ramps are not designed.
Crest and toe strings are contours of the design surface (contourpy).
"""

from __future__ import annotations

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
class DesignedPit:
    x: Floats  # raster column coordinates
    y: Floats  # raster row coordinates
    z: Floats  # ny x nx design surface (cut by topography)
    top: Floats  # ny x nx original surface
    geometry: BenchGeometry
    reaches_edge: bool  # design meets the raster border below the original surface
    on_model: Mask  # ny x nx raster cells over the block model area

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
) -> DesignedPit:
    nx, ny, nz = grid.count
    cube = np.asarray(mined, bool).reshape(nz, ny, nx)
    if not cube.any():
        raise PitOptError("no mined blocks")
    (ox, oy, oz), (sx, sy, sz) = grid.origin, grid.size
    res = resolution or min(sx, sy) / 2.0
    depth = nz * sz
    pad = margin if margin is not None else depth / math.tan(math.radians(geometry.overall_angle))
    x = np.arange(ox - pad + res / 2, ox + nx * sx + pad, res)
    y = np.arange(oy - pad + res / 2, oy + ny * sy + pad, res)
    xx, yy = np.meshgrid(x, y)
    # Column floor (bottom of the deepest mined block) on the raster, +inf if unmined.
    lowest = np.argmax(cube, axis=0)
    floor_col = np.where(cube.any(axis=0), oz + lowest * sz, np.inf)
    ci = np.floor((xx - ox) / sx).astype(int)
    cj = np.floor((yy - oy) / sy).astype(int)
    on_grid = (ci >= 0) & (ci < nx) & (cj >= 0) & (cj < ny)
    floor = np.full(xx.shape, np.inf)
    floor[on_grid] = floor_col[cj[on_grid], ci[on_grid]]
    design = np.full(xx.shape, np.inf)
    for level in np.unique(floor[np.isfinite(floor)]):
        dist = distance_transform_edt(floor > level, sampling=res)
        design = np.minimum(design, level + geometry.rise(np.asarray(dist, np.float64)))
    top = _original_surface(grid, xx, yy, topography, rock, cube)
    z = np.minimum(design, top)
    below = design < top - 1e-6
    edge = bool(below[0, :].any() or below[-1, :].any() or below[:, 0].any() or below[:, -1].any())
    return DesignedPit(x, y, z, top, geometry, edge, on_grid)


def _original_surface(  # noqa: PLR0913, PLR0917 - raster inputs
    grid: Grid,
    xx: Floats,
    yy: Floats,
    topography: Surface | None,
    rock: Mask | None,
    cube: Mask,
) -> Floats:
    nx, ny, nz = grid.count
    (ox, oy, oz), (sx, sy, sz) = grid.origin, grid.size
    if topography is not None:
        z, _ = topography.elevation(np.column_stack([xx.ravel(), yy.ravel()]))
        return np.asarray(z, np.float64).reshape(xx.shape)
    if rock is not None:
        r = np.asarray(rock, bool).reshape(cube.shape)
        top_k = nz - np.argmax(r[::-1], axis=0)
        col_top = np.where(r.any(axis=0), oz + top_k * sz, oz)
    else:
        col_top = np.full((ny, nx), oz + nz * sz)
    ci = np.clip(np.floor((xx - ox) / sx).astype(int), 0, nx - 1)
    cj = np.clip(np.floor((yy - oy) / sy).astype(int), 0, ny - 1)
    return np.asarray(col_top[cj, ci], np.float64)


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
) -> DesignReport:
    """Design benches around shells ``1..last_shell`` of a stored pit optimisation."""
    if "pit_shell" not in model.attributes:
        raise PitOptError(f"{model.name} has no pit_shell; run the pit optimisation first")
    shell = model.values("pit_shell")
    last = last_shell or int(np.nanmax(shell))
    mined = (shell >= 1) & (shell <= last)
    topo = surfaces.load_surface(project, topography) if topography else None
    rock = model.values("kaya") > 0.5 if "kaya" in model.attributes else None
    pit = design_pit(model.grid, mined, geometry, topography=topo, rock=rock)
    inside = pit.inside(model.grid.centroids())
    if rock is not None:
        inside &= rock
    value = np.nan_to_num(model.values("pit_value")) if "pit_value" in model.attributes else None
    if save_surface:
        surfaces.save_surface(project, save_surface, pit.surface())
    if flag_attribute:
        model.set_attributes(
            {flag_attribute: inside.astype(np.float64)},
            {flag_attribute: {"unit": None, "design": geometry.__dict__, "shell": last}},
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
