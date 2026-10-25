"""Triangulated surfaces (DTM): topography and other horizons.

A surface is a 2.5D triangulation of points in plan (Delaunay on x, y); its
elevation at any (x, y) inside the hull is the linear interpolation on the
triangle containing it. Outside the hull the nearest point's elevation is
used and reported (``outside`` count), never silently extrapolated by slope.

Stored as one ``surface`` catalogue object with a Parquet table of points
(x, y, z); triangles are recomputed on load (Delaunay is deterministic for a
given point set).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pyarrow as pa
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial import Delaunay

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel
from minecore.project import Project, bulk

KIND = "surface"
Floats = npt.NDArray[np.float64]


class SurfaceError(MineCoreError):
    """Invalid surface data."""


@dataclass(frozen=True)
class Surface:
    points: Floats  # n x 3

    def __post_init__(self) -> None:
        p = np.asarray(self.points, np.float64)
        if p.ndim != 2 or p.shape[1] != 3 or len(p) < 3:
            raise SurfaceError("a surface needs at least three x, y, z points")
        if not np.isfinite(p).all():
            raise SurfaceError("surface points must be finite")

    def triangles(self) -> npt.NDArray[np.int64]:
        try:
            tri = Delaunay(self.points[:, :2])
        except Exception as exc:
            raise SurfaceError(f"points cannot be triangulated in plan: {exc}") from exc
        return np.asarray(tri.simplices, np.int64)

    def elevation(self, xy: Floats) -> tuple[Floats, int]:
        """Elevation at each (x, y) and the number of points outside the hull."""
        xy = np.asarray(xy, np.float64)
        linear = LinearNDInterpolator(self.points[:, :2], self.points[:, 2])
        z = np.asarray(linear(xy), np.float64)
        outside = ~np.isfinite(z)
        if outside.any():
            nearest = NearestNDInterpolator(self.points[:, :2], self.points[:, 2])
            z[outside] = nearest(xy[outside])
        return z, int(outside.sum())


def read_points_csv(path: Path) -> Surface:
    """x, y, z columns (header names x/y/z or east/north/elev; else first three)."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t ")
    rows = list(csv.reader(text.splitlines(), dialect))
    header = [h.strip().lower() for h in rows[0]]
    names = {
        "x": ("x", "east", "easting", "dogu", "doğu", "y_saga"),
        "y": ("y", "north", "northing", "kuzey"),
        "z": ("z", "elev", "elevation", "rl", "kot", "h"),
    }
    cols = []
    for key in ("x", "y", "z"):
        found = next((i for i, h in enumerate(header) if h in names[key]), None)
        cols.append(found)
    start = 1
    if None in cols:
        cols, start = [0, 1, 2], (0 if _numeric(rows[0][:3]) else 1)
    data = []
    for row in rows[start:]:
        try:
            data.append([float(row[c].replace(",", ".")) for c in cols])  # type: ignore[index]
        except (ValueError, IndexError):
            continue
    if len(data) < 3:
        raise SurfaceError(f"{path.name}: fewer than three numeric x, y, z rows")
    return Surface(np.array(data))


def _numeric(cells: list[str]) -> bool:
    try:
        [float(c.replace(",", ".")) for c in cells]
    except ValueError:
        return False
    return True


def save_surface(project: Project, name: str, surface: Surface) -> str:
    table = pa.table({c: surface.points[:, i] for i, c in enumerate("xyz")})
    record = project.register_object(KIND, name, metadata={"format": 1})
    ref = bulk.write_table(project.path, record.object_id, "points", table)
    project.update_object(
        record.object_id, metadata={"format": 1, **ref.to_json()}, description=f"Store {name!r}"
    )
    return record.object_id


def load_surface(project: Project, name: str) -> Surface:
    for record in project.objects(KIND):
        if record.name == name:
            table = bulk.read_table(project.path, bulk.FileRef.from_json(record.metadata))
            return Surface(np.column_stack([table.column(c).to_numpy() for c in "xyz"]))
    raise SurfaceError(f"no surface named {name!r}")


def surface_names(project: Project) -> list[str]:
    return [r.name for r in project.objects(KIND)]


def flag_below(model: BlockModel, surface: Surface, attribute: str = "kaya") -> tuple[int, int]:
    """Store 1 for blocks whose centroid is below the surface (rock), else 0 (air).

    Returns (rock blocks, block columns outside the surface hull).
    """
    grid = model.grid
    nx, ny, _ = grid.count
    c = grid.centroids()
    column_xy = c[: nx * ny, :2]
    top, outside = surface.elevation(column_xy)
    k_top = np.tile(top, grid.count[2])
    rock = (c[:, 2] <= k_top).astype(np.float64)
    model.set_attributes(
        {attribute: rock},
        {attribute: {"unit": None}},
        f"Flag blocks below surface as {attribute!r}",
    )
    return int(rock.sum()), outside
