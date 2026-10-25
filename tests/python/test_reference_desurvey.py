"""Independent re-verification of the T-009 desurvey reference values.

The reference file was produced by another model (ChatGPT). Before any kernel
is tested against it, this test recomputes every point with a different
formulation: the industry ratio-factor formula applied to the partial arc, with
the partial-arc tangent from a Rodrigues rotation. Pure ``math``; no numpy,
no MineCore code. Case C03 is also checked against closed-form circle geometry.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

CASES = Path(__file__).resolve().parents[1] / "reference" / "desurvey" / "cases.csv"
# Published values are rounded to 1e-6 m per axis; 3D rounding error <= ~8.7e-7 m.
TOLERANCE_M = 1e-6

Vec = tuple[float, float, float]


def _vec(azimuth_deg: float, dip_deg: float) -> Vec:
    a, d = math.radians(azimuth_deg), math.radians(dip_deg)
    return (math.cos(d) * math.sin(a), math.cos(d) * math.cos(a), math.sin(d))


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(k: float, a: Vec) -> Vec:
    return (k * a[0], k * a[1], k * a[2])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _rotate(v: Vec, axis: Vec, angle: float) -> Vec:
    k = _scale(1.0 / math.sqrt(_dot(axis, axis)), axis)
    return _add(
        _add(_scale(math.cos(angle), v), _scale(math.sin(angle), _cross(k, v))),
        _scale(_dot(k, v) * (1.0 - math.cos(angle)), k),
    )


def _partial_arc(t0: Vec, t1: Vec, length: float, s: float) -> Vec:
    theta = math.acos(max(-1.0, min(1.0, _dot(t0, t1))))
    if theta < 1e-12:
        return _scale(s, t0)
    phi = theta * s / length
    ts = _rotate(t0, _cross(t0, t1), phi)
    rf = math.tan(phi / 2.0) / (phi / 2.0) if phi > 1e-12 else 1.0
    return _scale(s / 2.0 * rf, _add(t0, ts))


def _position(
    collar: Vec, depths: list[float], azs: list[float], dips: list[float], md: float
) -> Vec:
    ts = [_vec(a, d) for a, d in zip(azs, dips, strict=True)]
    if md <= depths[0]:
        return _add(collar, _scale(md, ts[0]))
    p = _add(collar, _scale(depths[0], ts[0]))
    for i in range(len(depths) - 1):
        length = depths[i + 1] - depths[i]
        if md <= depths[i + 1]:
            return _add(p, _partial_arc(ts[i], ts[i + 1], length, md - depths[i]))
        p = _add(p, _partial_arc(ts[i], ts[i + 1], length, length))
    return _add(p, _scale(md - depths[-1], ts[-1]))


def _rows() -> list[dict[str, str]]:
    with CASES.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _floats(text: str) -> list[float]:
    return [float(v) for v in text.split("|")]


def _distance(a: Vec, b: Vec) -> float:
    d = (a[0] - b[0], a[1] - b[1], a[2] - b[2])
    return math.sqrt(_dot(d, d))


def test_reference_file_has_all_cases() -> None:
    rows = _rows()
    assert len(rows) == 55
    assert len({r["case_id"] for r in rows}) == 8


@pytest.mark.parametrize("row", _rows(), ids=lambda r: f"{r['case_id']}@{r['depth']}")
def test_reference_point_matches_independent_method(row: dict[str, str]) -> None:
    collar = (float(row["collar_x"]), float(row["collar_y"]), float(row["collar_z"]))
    expected = _position(
        collar,
        _floats(row["station_depths"]),
        _floats(row["station_azimuths"]),
        _floats(row["station_dips"]),
        float(row["depth"]),
    )
    published = (float(row["x"]), float(row["y"]), float(row["z"]))
    assert _distance(expected, published) <= TOLERANCE_M


def test_pure_build_case_matches_circle_geometry() -> None:
    # Vertical collar building to dip -60 toward east over 100 m: a 30 degree arc.
    radius = 100.0 / math.radians(30.0)
    for row in (r for r in _rows() if r["case_id"] == "C03_PURE_BUILD"):
        md = float(row["depth"])
        s, extra = min(md, 100.0), max(0.0, md - 100.0)
        east = radius * (1.0 - math.cos(s / radius)) + extra * math.cos(math.radians(60.0))
        down = radius * math.sin(s / radius) + extra * math.sin(math.radians(60.0))
        expected = (500250.0 + east, 4500250.0, 1020.0 - down)
        published = (float(row["x"]), float(row["y"]), float(row["z"]))
        assert _distance(expected, published) <= TOLERANCE_M, md
