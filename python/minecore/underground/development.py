"""Access development for an underground orebody: decline and level crosscuts.

Layout (a common footwall spiral):

* the spiral centre lies beside the orebody, in plan towards the portal, at
  ``standoff + radius`` beyond the orebody's furthest block from its plan
  centroid (unless a centre is given);
* from the portal a straight leg descends at ``gradient`` to the tangent
  point of the spiral circle, then the decline spirals around the centre at
  constant ``radius`` and ``gradient`` down to the deepest level;
* at every level elevation a horizontal crosscut runs from the decline to the
  nearest ore block centroid of that level;
* from there an ore drive runs along strike (the principal plan direction of
  that level's ore blocks) over the full extent of the level's ore, through
  the crosscut's end point (``ore_drives``).

Quantities are reported as metres and excavated volume/tonnes for given
drive sizes. This is a concept layout: no ventilation, sumps, stockpile
bays, passing bays, geotechnical checks or scheduling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from minecore.blockmodel import BlockModel
from minecore.io.dxf import Polyline, write_dxf
from minecore.project import Project
from minecore.strings import String, save_strings
from minecore.underground.stopes import StopeError

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class DeclineDesign:
    gradient: float = 1.0 / 7.0  # rise over run
    radius: float = 25.0  # spiral radius, m (>= minimum turning radius)
    standoff: float = 40.0  # minimum plan distance from the orebody, m
    width: float = 5.0  # drive width, m
    height: float = 5.5  # drive height, m
    density: float = 2.7  # t/m3 of waste rock
    clockwise: bool = True  # spiral direction going down
    ore_drives: bool = True  # drive along strike on every level

    def __post_init__(self) -> None:
        if not 0.01 <= self.gradient <= 0.25:
            raise StopeError("decline gradient must be between 1 % and 25 %")
        if min(self.radius, self.width, self.height, self.density) <= 0 or self.standoff < 0:
            raise StopeError("radius, drive size and density must be > 0, standoff >= 0")


@dataclass(frozen=True)
class LevelAccess:
    elevation: float
    start: Floats  # on the decline
    end: Floats  # nearest ore block centroid on the level
    length: float
    drive: Floats | None = None  # 2 x 3 ore drive end points, through ``end``

    @property
    def drive_length(self) -> float:
        return 0.0 if self.drive is None else float(np.linalg.norm(self.drive[1] - self.drive[0]))


@dataclass(frozen=True)
class Development:
    decline: Floats  # n x 3 centreline, portal first
    accesses: tuple[LevelAccess, ...]
    centre: Floats  # spiral centre (x, y)
    design: DeclineDesign

    @property
    def decline_length(self) -> float:
        return float(np.linalg.norm(np.diff(self.decline, axis=0), axis=1).sum())

    @property
    def crosscut_length(self) -> float:
        return float(sum(a.length for a in self.accesses))

    @property
    def drive_length(self) -> float:
        return float(sum(a.drive_length for a in self.accesses))

    @property
    def total_length(self) -> float:
        return self.decline_length + self.crosscut_length + self.drive_length

    @property
    def volume(self) -> float:
        return self.total_length * self.design.width * self.design.height

    @property
    def tonnes(self) -> float:
        return self.volume * self.design.density

    def polylines(self) -> list[Polyline]:
        lines = [Polyline("DECLINE", self.decline, closed=False)]
        lines += [
            Polyline(f"XCUT_{a.elevation:.0f}", np.vstack([a.start, a.end]), closed=False)
            for a in self.accesses
        ]
        lines += [
            Polyline(f"DRIVE_{a.elevation:.0f}", a.drive, closed=False)
            for a in self.accesses
            if a.drive is not None and a.drive_length > 0
        ]
        return lines


def design_development(  # noqa: PLR0913 - options are keyword-only
    ore_xyz: Floats,
    portal: tuple[float, float, float],
    levels: list[float],
    design: DeclineDesign,
    *,
    centre: tuple[float, float] | None = None,
    step: float = 2.0,
) -> Development:
    """Decline and crosscuts to the ore points (block centroids) on ``levels``."""
    ore = np.asarray(ore_xyz, np.float64)
    if ore.size == 0:
        raise StopeError("no ore blocks")
    if not levels:
        raise StopeError("at least one level elevation is needed")
    p0 = np.asarray(portal, np.float64)
    if min(levels) >= p0[2]:
        raise StopeError("levels must be below the portal")
    c = _spiral_centre(ore, p0, design) if centre is None else np.asarray(centre, np.float64)
    line = _centreline(p0, c, min(levels), design, step)
    accesses = []
    layer = _layer(ore)
    for level in sorted(levels, reverse=True):
        k = int(np.argmin(np.abs(line[:, 2] - level)))
        on_level = ore[np.abs(ore[:, 2] - level) <= 0.5 * layer]
        if abs(line[k, 2] - level) > design.gradient * step + 1e-6 or len(on_level) == 0:
            continue  # the decline does not reach this level, or no ore on it
        start = np.array([line[k, 0], line[k, 1], level])
        j = int(np.argmin(np.linalg.norm(on_level[:, :2] - start[:2], axis=1)))
        end = np.array([on_level[j, 0], on_level[j, 1], level])
        drive = _ore_drive(on_level, end) if design.ore_drives else None
        accesses.append(LevelAccess(level, start, end, float(np.linalg.norm(end - start)), drive))
    return Development(line, tuple(accesses), c, design)


def _ore_drive(on_level: Floats, through: Floats) -> Floats:
    """Drive along the principal plan axis of the level's ore, through ``through``."""
    xy = on_level[:, :2]
    if len(xy) < 2:
        return np.vstack([through, through])
    centred = xy - xy.mean(axis=0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    strike = vt[0]
    t = (xy - through[:2]) @ strike
    a = through[:2] + t.min() * strike
    b = through[:2] + t.max() * strike
    z = through[2]
    return np.array([[a[0], a[1], z], [b[0], b[1], z]])


def _spiral_centre(ore: Floats, portal: Floats, design: DeclineDesign) -> Floats:
    ore_c = ore[:, :2].mean(axis=0)
    reach = float(np.linalg.norm(ore[:, :2] - ore_c, axis=1).max())
    to_portal = portal[:2] - ore_c
    norm = float(np.linalg.norm(to_portal))
    direction = to_portal / norm if norm > 1e-9 else np.array([1.0, 0.0])
    return np.asarray(ore_c + direction * (reach + design.standoff + design.radius))


def _centreline(p0: Floats, c: Floats, bottom: float, design: DeclineDesign, step: float) -> Floats:
    """Portal leg tangent to the spiral circle, then the spiral down to ``bottom``."""
    g, r = design.gradient, design.radius
    d = p0[:2] - c
    dist = float(np.linalg.norm(d))
    sign = -1.0 if design.clockwise else 1.0  # polar angle decreases going clockwise
    if dist > r:
        # Tangent point from which the spiral continues in the ``sign`` direction.
        theta = math.atan2(d[1], d[0]) + sign * math.acos(r / dist)
        tangent = c + r * np.array([math.cos(theta), math.sin(theta)])
        leg = float(np.linalg.norm(tangent - p0[:2]))
        t = np.linspace(0.0, 1.0, max(1, math.ceil(leg / step)) + 1)
        pts = [np.column_stack([p0[:2] + np.outer(t, tangent - p0[:2]), p0[2] - g * leg * t])]
    else:
        theta = math.atan2(d[1], d[0]) if dist > 1e-9 else 0.0
        start = c + r * np.array([math.cos(theta), math.sin(theta)])
        pts = [np.array([[*p0], [start[0], start[1], p0[2]]])]
    z_now = float(pts[0][-1, 2])
    if z_now > bottom:
        arc = (z_now - bottom) / g
        s = np.linspace(0.0, arc, max(1, math.ceil(arc / step)) + 1)[1:]
        ang = theta + sign * s / r
        pts.append(np.column_stack([c[0] + r * np.cos(ang), c[1] + r * np.sin(ang), z_now - g * s]))
    return np.vstack(pts)


def _layer(ore: Floats) -> float:
    z = np.unique(np.round(ore[:, 2], 6))
    return float(np.diff(z).min()) if len(z) > 1 else 1.0


def development_from_model(  # noqa: PLR0913 - options are keyword-only
    model: BlockModel,
    portal: tuple[float, float, float],
    design: DeclineDesign,
    *,
    ore_attribute: str = "ug_pano",
    level_interval: float = 25.0,
    levels: list[float] | None = None,
    centre: tuple[float, float] | None = None,
    dxf: Path | None = None,
    project: Project | None = None,
    save_as: str | None = None,
) -> Development:
    """Development to the blocks flagged by ``ore_attribute`` (> 0.5).

    With ``project`` and ``save_as`` the centrelines are stored as a string set
    (shown in the 3-D view and in section plots of strings).
    """
    flag = model.values(ore_attribute) > 0.5
    if not flag.any():
        raise StopeError(f"no blocks flagged in {ore_attribute!r}")
    ore = model.grid.centroids()[flag]
    if levels is None:
        if not level_interval > 0:
            raise StopeError("level interval must be > 0")
        z = np.unique(ore[:, 2])
        lo, hi = float(z.min()), float(z.max())
        # Levels on block centre elevations, from the lowest ore upwards.
        dz = model.grid.size[2]
        stride = max(1, round(level_interval / dz))
        levels = [lo + k * stride * dz for k in range(int((hi - lo) / (stride * dz)) + 1)]
    dev = design_development(ore, portal, levels, design, centre=centre)
    if dxf is not None:
        write_dxf(dxf, dev.polylines())
    if project is not None and save_as:
        save_strings(
            project,
            save_as,
            [String(p.points, False, p.layer) for p in dev.polylines()],
        )
    return dev
