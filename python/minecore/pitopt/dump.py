"""Waste dump design inside a footprint: lifts, face angle, berms, capacity.

The dump surface at a point is its toe elevation (the original ground at the
nearest point of the footprint edge) plus the lift-stair rise over the
distance to that edge, capped at the top elevation:

    z(p) = min(top, ground(q*) + stair(|p - q*|)),  q* nearest edge point

with the same stair profile as the pit benches (lift height, face angle,
berm). It is evaluated on a raster with the Euclidean distance transform.
The dump is the material between the ground and z where z is above the
ground. Capacity in tonnes = loose volume / swell x in-situ density.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.ndimage import distance_transform_edt

from minecore.drillblast import _inside
from minecore.pitopt.benchdesign import BenchGeometry
from minecore.pitopt.economics import PitOptError
from minecore.surfaces import Surface

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class DumpDesign:
    x: Floats
    y: Floats
    ground: Floats  # ny x nx
    z: Floats  # ny x nx dump surface (ground outside the dump)
    geometry: BenchGeometry
    top: float

    @property
    def thickness(self) -> Floats:
        return np.clip(self.z - self.ground, 0.0, None)

    @property
    def cell_area(self) -> float:
        return float((self.x[1] - self.x[0]) * (self.y[1] - self.y[0]))

    def volume(self) -> float:
        """Loose (dumped) volume, m3."""
        return float(self.thickness.sum() * self.cell_area)

    def area(self) -> float:
        return float((self.thickness > 1e-6).sum() * self.cell_area)

    def surface(self) -> Surface:
        xx, yy = np.meshgrid(self.x, self.y)
        return Surface(np.column_stack([xx.ravel(), yy.ravel(), self.z.ravel()]))

    def volume_by_lift(self) -> list[tuple[float, float]]:
        """(lift base elevation, loose volume) from the lowest toe up."""
        base = float(np.min(np.where(self.thickness > 0, self.ground, np.inf)))
        h = self.geometry.bench_height
        out = []
        level = base
        while level < float(self.z.max()) - 1e-9:
            lo = np.maximum(self.ground, level)
            hi = np.minimum(self.z, level + h)
            out.append((level, float(np.clip(hi - lo, 0.0, None).sum() * self.cell_area)))
            level += h
        return out


def design_dump(  # noqa: PLR0913 - options are keyword-only
    footprint: Floats,
    geometry: BenchGeometry,
    *,
    ground: Surface | None = None,
    ground_z: float = 0.0,
    top: float | None = None,
    max_height: float | None = None,
    resolution: float = 5.0,
) -> DumpDesign:
    """Dump within a closed footprint (n x 2 or n x 3), toe on the footprint edge.

    ``top``: elevation of the dump crest; or ``max_height`` above the lowest
    toe; neither = as high as the footprint allows.
    """
    poly = np.asarray(footprint, np.float64)
    if len(poly) < 3:
        raise PitOptError("the dump footprint needs at least three points")
    if resolution <= 0:
        raise PitOptError("resolution must be > 0")
    lo, hi = poly[:, :2].min(axis=0) - 2 * resolution, poly[:, :2].max(axis=0) + 2 * resolution
    x = np.arange(lo[0] + resolution / 2, hi[0], resolution)
    y = np.arange(lo[1] + resolution / 2, hi[1], resolution)
    xx, yy = np.meshgrid(x, y)
    pts = np.column_stack([xx.ravel(), yy.ravel()])
    inside = _inside(poly[:, :2], pts).reshape(xx.shape)
    if not inside.any():
        raise PitOptError("the footprint is smaller than one raster cell")
    if ground is not None:
        g, _ = ground.elevation(pts)
        gz = np.asarray(g, np.float64).reshape(xx.shape)
        gz = np.where(np.isfinite(gz), gz, np.nanmin(gz))
    else:
        gz = np.full(xx.shape, float(ground_z))
    dist, (nj, ni) = distance_transform_edt(inside, sampling=resolution, return_indices=True)
    toe = gz[nj, ni]
    z = toe + geometry.rise(np.asarray(dist, np.float64))
    toe_low = float(toe[inside].min())
    cap = math.inf
    if top is not None:
        cap = top
    elif max_height is not None:
        cap = toe_low + max_height
    z = np.where(inside, np.minimum(z, cap), gz)
    z = np.maximum(z, gz)
    return DumpDesign(x, y, gz, z, geometry, float(z.max()))


def capacity_t(design: DumpDesign, density: float, swell: float) -> float:
    """In-situ tonnes the dump holds (loose volume / swell factor x density)."""
    if density <= 0 or swell < 1:
        raise PitOptError("density must be > 0 and the swell factor >= 1")
    return design.volume() / swell * density
