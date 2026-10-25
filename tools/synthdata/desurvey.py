from __future__ import annotations

from collections.abc import Sequence
from math import acos, cos, radians, sin, sqrt, tan

from synthdata.config import Collar, Station

Vec = tuple[float, float, float]


def _direction(azimuth: float, dip: float) -> Vec:
    a = radians(azimuth)
    d = radians(dip)
    return (cos(d) * sin(a), cos(d) * cos(a), sin(d))


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(v: Vec, factor: float) -> Vec:
    return (v[0] * factor, v[1] * factor, v[2] * factor)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _arc_delta(a: Station, b: Station, s: float) -> Vec:
    """Displacement after arc length ``s`` along the minimum-curvature arc from a to b."""
    v1 = _direction(a.azimuth, a.dip)
    v2 = _direction(b.azimuth, b.dip)
    theta = acos(max(-1.0, min(1.0, _dot(v1, v2))))
    length = b.depth - a.depth
    axis = _cross(v1, v2)
    norm = sqrt(_dot(axis, axis))
    if norm < 1e-12 or length <= 0.0:
        return _scale(v1, s)
    phi = theta * s / length
    # Tangent after turning by phi in the plane of v1 and v2 (Rodrigues rotation).
    k = _scale(axis, 1.0 / norm)
    vs = _add(_scale(v1, cos(phi)), _scale(_cross(k, v1), sin(phi)))
    rf = tan(phi / 2.0) / (phi / 2.0) if phi > 1e-12 else 1.0
    return _scale(_add(v1, vs), s / 2.0 * rf)


def position_at(
    stations: Sequence[Station],
    collar: Collar,
    depth: float,
) -> Vec:
    """Position at ``depth`` along the hole (minimum curvature)."""
    collar_vec: Vec = (collar.x, collar.y, collar.z)
    if depth <= 0.0 or not stations:
        return collar_vec
    first = stations[0]
    v_first = _direction(first.azimuth, first.dip)
    if depth <= first.depth:
        return _add(collar_vec, _scale(v_first, depth))
    pos = _add(collar_vec, _scale(v_first, first.depth))
    prev = first
    for station in stations[1:]:
        length = station.depth - prev.depth
        if depth <= station.depth:
            return _add(pos, _arc_delta(prev, station, depth - prev.depth))
        pos = _add(pos, _arc_delta(prev, station, length))
        prev = station
    return _add(pos, _scale(_direction(prev.azimuth, prev.dip), depth - prev.depth))
