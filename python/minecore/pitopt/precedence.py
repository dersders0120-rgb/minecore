"""Slope precedence arcs for a regular block model.

A block at bench ``k`` requires every block on higher benches whose centre
lies inside the inverted cone opening upwards from the block centre with the
overall slope angle (measured from horizontal). The slope angle can vary with
direction (azimuth sectors, interpolated linearly in azimuth).

Only the arcs that are not implied by others are generated: the block
directly above (one bench), and on bench ``d`` above, the offsets whose
horizontal distance lies in ``(r(d-1), r(d)]`` with ``r(d) = d*dz/tan(angle)``.
Every block inside the cone is then reachable by a chain of arcs, because the
cone radius is linear in height. ``max_levels`` bounds how many benches the
pattern spans (more = closer to the true cone, more arcs).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from minecore.blockmodel import Grid
from minecore.pitopt.economics import PitOptError


@dataclass(frozen=True)
class SlopeProfile:
    """Overall slope angle (degrees from horizontal), optionally by azimuth.

    ``sectors`` holds ``(azimuth, angle)`` pairs; between them the angle is
    interpolated linearly around the compass. Empty means ``angle`` everywhere.
    """

    angle: float = 45.0
    sectors: tuple[tuple[float, float], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for a in (self.angle, *(s[1] for s in self.sectors)):
            if not 10.0 <= a <= 89.0:
                raise PitOptError("slope angles must be between 10 and 89 degrees")

    def angles(self) -> list[float]:
        return [s[1] for s in self.sectors] if self.sectors else [self.angle]

    def at(self, azimuth: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        if not self.sectors:
            return np.full(np.shape(azimuth), self.angle)
        pts = sorted((az % 360.0, ang) for az, ang in self.sectors)
        az = np.array([p[0] for p in pts])
        ang = np.array([p[1] for p in pts])
        # Wrap around the compass for interpolation.
        az = np.concatenate([az - 360.0, az, az + 360.0])
        ang = np.concatenate([ang, ang, ang])
        return np.asarray(np.interp(np.asarray(azimuth) % 360.0, az, ang), np.float64)


def cone_offsets(grid: Grid, slope: SlopeProfile, max_levels: int) -> list[tuple[int, int, int]]:
    """Non-redundant ``(di, dj, dk)`` offsets with ``dk >= 1``."""
    sx, sy, sz = grid.size
    offsets: list[tuple[int, int, int]] = [(0, 0, 1)]
    for d in range(1, max_levels + 1):
        # Largest possible radius bounds the search window.
        r_max = d * sz / math.tan(math.radians(min(slope.angles())))
        ni, nj = int(r_max // sx) + 1, int(r_max // sy) + 1
        di, dj = np.meshgrid(np.arange(-ni, ni + 1), np.arange(-nj, nj + 1), indexing="ij")
        dx, dy = di.ravel() * sx, dj.ravel() * sy
        dist = np.hypot(dx, dy)
        azimuth = np.degrees(np.arctan2(dx, dy))
        tan = np.tan(np.radians(slope.at(azimuth)))
        r_now = d * sz / tan
        r_prev = (d - 1) * sz / tan
        keep = (dist <= r_now + 1e-9) & (dist > r_prev + 1e-9)
        kept = zip(di.ravel()[keep], dj.ravel()[keep], strict=True)
        offsets.extend((int(a), int(b), d) for a, b in kept)
    return offsets


def default_levels(grid: Grid, slope: SlopeProfile, radius_blocks: float = 3.0) -> int:
    """Benches needed for the pattern to reach about ``radius_blocks`` sideways."""
    sx, sy, sz = grid.size
    steepest = max(slope.angles())
    per_level = sz / math.tan(math.radians(steepest))
    return int(min(max(math.ceil(radius_blocks * max(sx, sy) / per_level), 1), 12))


def precedence_arcs(
    grid: Grid, slope: SlopeProfile, max_levels: int | None = None
) -> npt.NDArray[np.uint32]:
    """``m x 2`` array of ``(block, required_block)`` arcs (``i`` fastest indexing)."""
    nx, ny, nz = grid.count
    if grid.blocks >= 2**32 - 2:
        raise PitOptError("block model too large for the optimiser")
    levels = default_levels(grid, slope) if max_levels is None else max_levels
    index = np.arange(grid.blocks, dtype=np.int64)
    i, j, k = index % nx, (index // nx) % ny, index // (nx * ny)
    parts = []
    for di, dj, dk in cone_offsets(grid, slope, levels):
        ok = (i + di >= 0) & (i + di < nx) & (j + dj >= 0) & (j + dj < ny) & (k + dk < nz)
        src = index[ok]
        dst = (i[ok] + di) + nx * ((j[ok] + dj) + ny * (k[ok] + dk))
        parts.append(np.column_stack([src, dst]))
    if not parts:
        return np.zeros((0, 2), np.uint32)
    return np.concatenate(parts).astype(np.uint32)
