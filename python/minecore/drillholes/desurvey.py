"""Minimum-curvature desurveying.

Between two survey stations the hole is a circular arc tangent to both
station directions (the industry-standard minimum-curvature method). Points
inside a segment lie on that arc; above the first station the hole is
straight along the first direction; below the last station it continues
straight along the last direction.

Directions use azimuth clockwise from grid north and dip negative below
horizontal (ADR-0008). Coordinates are east, north, up (x, y, z).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from minecore.drillholes.model import DrillholeError

Floats = npt.NDArray[np.float64]
_SMALL = 1e-9


def directions(azimuth: Floats, dip: Floats) -> Floats:
    """Unit direction vectors (n, 3) for azimuth/dip in degrees."""
    az, dp = np.radians(azimuth), np.radians(dip)
    return np.column_stack([np.sin(az) * np.cos(dp), np.cos(az) * np.cos(dp), np.sin(dp)])


def _arc(t1: Floats, t2: Floats, length: Floats) -> tuple[Floats, Floats]:
    """Displacement and end direction of minimum-curvature arcs (row-wise)."""
    cosine = np.clip(np.einsum("ij,ij->i", t1, t2), -1.0, 1.0)
    beta = np.arccos(cosine)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(beta > _SMALL, 2.0 / beta * np.tan(beta / 2.0), 1.0)
    return (length * ratio / 2.0)[:, None] * (t1 + t2), t2


def _slerp(t1: Floats, t2: Floats, f: Floats) -> Floats:
    cosine = np.clip(np.einsum("ij,ij->i", t1, t2), -1.0, 1.0)
    beta = np.arccos(cosine)
    sin_beta = np.sin(beta)
    straight = sin_beta < _SMALL
    with np.errstate(invalid="ignore", divide="ignore"):
        a = np.where(straight, 1.0 - f, np.sin((1.0 - f) * beta) / sin_beta)
        b = np.where(straight, f, np.sin(f * beta) / sin_beta)
    t = a[:, None] * t1 + b[:, None] * t2
    return t / np.linalg.norm(t, axis=1)[:, None]


def desurvey(
    collar: tuple[float, float, float],
    station_depth: Floats,
    azimuth: Floats,
    dip: Floats,
    depths: Floats,
) -> Floats:
    """Coordinates (n, 3) of the points at ``depths`` along one hole.

    ``station_depth`` must be strictly increasing and at least one station
    given. Depths may be anywhere >= 0.
    """
    md = np.asarray(station_depth, dtype=np.float64)
    if md.size == 0:
        raise DrillholeError("desurvey needs at least one station")
    if np.any(np.diff(md) <= 0):
        raise DrillholeError("survey station depths must be strictly increasing")
    t = directions(np.asarray(azimuth, np.float64), np.asarray(dip, np.float64))
    # Position of every station: start at the collar, straight to the first station.
    pos = np.empty((md.size, 3))
    pos[0] = np.asarray(collar, np.float64) + md[0] * t[0]
    if md.size > 1:
        steps, _ = _arc(t[:-1], t[1:], np.diff(md))
        pos[1:] = pos[0] + np.cumsum(steps, axis=0)

    d = np.asarray(depths, dtype=np.float64)
    out = np.empty((d.size, 3))
    seg = np.searchsorted(md, d, side="right") - 1
    above = seg < 0
    out[above] = pos[0] + (d[above] - md[0])[:, None] * t[0]
    below = seg >= md.size - 1
    out[below] = pos[-1] + (d[below] - md[-1])[:, None] * t[-1]
    inside = ~above & ~below
    if np.any(inside):
        k = seg[inside]
        length = md[k + 1] - md[k]
        f = (d[inside] - md[k]) / length
        t1, t2 = t[k], t[k + 1]
        tf = _slerp(t1, t2, f)
        step, _ = _arc(t1, tf, f * length)
        out[inside] = pos[k] + step
    return out
