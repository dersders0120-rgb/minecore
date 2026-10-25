"""Control point files and local grid fit reports."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minecore._core import GridFit, fit_local_grid
from minecore.coords.crs import CrsError

REQUIRED = ("name", "base_e", "base_n", "local_x", "local_y")
VERTICAL = ("base_z", "local_z")


@dataclass(frozen=True)
class ControlPoints:
    """Points known in both the base CRS and the local grid."""

    names: list[str]
    base_xy: list[tuple[float, float]]
    local_xy: list[tuple[float, float]]
    base_z: list[float] | None
    local_z: list[float] | None


def _number(row: dict[str, str], column: str, line: int) -> float:
    text = (row.get(column) or "").strip()
    try:
        return float(text)
    except ValueError:
        raise CrsError(f"line {line}: {column} {text!r} is not a number") from None


def read_control_points(path: str | Path) -> ControlPoints:
    """Read a UTF-8 CSV with columns ``name,base_e,base_n,local_x,local_y``
    and optionally ``base_z,local_z`` (both or neither)."""
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = [c.strip() for c in reader.fieldnames or []]
        reader.fieldnames = columns
        missing = [c for c in REQUIRED if c not in columns]
        if missing:
            raise CrsError(f"{path}: missing columns {', '.join(missing)}")
        has_z = [c in columns for c in VERTICAL]
        if any(has_z) and not all(has_z):
            raise CrsError(f"{path}: give both base_z and local_z, or neither")
        names: list[str] = []
        base: list[tuple[float, float]] = []
        local: list[tuple[float, float]] = []
        bz: list[float] = []
        lz: list[float] = []
        for line, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            if not name:
                raise CrsError(f"line {line}: control point has no name")
            if name in names:
                raise CrsError(f"line {line}: duplicate control point {name!r}")
            names.append(name)
            base.append((_number(row, "base_e", line), _number(row, "base_n", line)))
            local.append((_number(row, "local_x", line), _number(row, "local_y", line)))
            if all(has_z):
                bz.append(_number(row, "base_z", line))
                lz.append(_number(row, "local_z", line))
    vertical = all(has_z)
    return ControlPoints(names, base, local, bz if vertical else None, lz if vertical else None)


def fit_control_points(
    points: ControlPoints, *, scale: float | None = None, z_offset: float | None = None
) -> GridFit:
    """Least-squares local grid from control points (see ``fit_local_grid``)."""
    return fit_local_grid(
        points.base_xy,
        points.local_xy,
        base_z=points.base_z,
        local_z=points.local_z,
        scale=scale,
        z_offset=z_offset,
    )


def fit_report(points: ControlPoints, fit: GridFit) -> dict[str, Any]:
    """Machine-readable fit report: parameters, quality figures, per-point residuals."""
    g = fit.grid
    return {
        "parameters": {
            "base_origin": list(g.base_origin),
            "local_origin": list(g.local_origin),
            "rotation_deg": g.rotation_deg,
            "scale": g.scale,
            "z_offset": g.z_offset,
        },
        "points": len(points.names),
        "rms_horizontal_m": fit.rms_horizontal,
        "max_horizontal_m": fit.max_horizontal,
        "sigma0_horizontal_m": fit.sigma0_horizontal,
        "vertical_points": fit.vertical_points,
        "rms_vertical_m": fit.rms_vertical,
        "residuals": [
            {"name": n, "dx": dx, "dy": dy, "dz": dz}
            for n, (dx, dy, dz) in zip(points.names, fit.residuals, strict=True)
        ],
    }
