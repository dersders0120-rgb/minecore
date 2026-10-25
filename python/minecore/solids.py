"""Closed triangulated solids (wireframes): import, inside test, block flagging.

Formats (public specifications): Wavefront OBJ (``v``/``f`` records), STL
(ASCII and binary) and DXF ``3DFACE`` entities (ASCII DXF group codes).

Inside test: a vertical ray is cast upwards from each point; the point is
inside when it crosses the surface an odd number of times. Crossings are
found per triangle with barycentric coordinates in plan. Ray positions that
fall exactly on a triangle edge are nudged by a tiny fixed offset so shared
edges are not counted twice. The solid must be closed (watertight); the
importer reports open edges.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel
from minecore.project import Project, bulk

KIND = "solid"
Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]
NUDGE = np.array([1.234567e-7, 2.345678e-7])


class SolidError(MineCoreError):
    """Invalid solid data."""


@dataclass(frozen=True)
class Solid:
    vertices: Floats  # n x 3
    triangles: Ints  # m x 3

    def __post_init__(self) -> None:
        if len(self.triangles) == 0:
            raise SolidError("solid has no triangles")
        if self.triangles.min() < 0 or self.triangles.max() >= len(self.vertices):
            raise SolidError("triangle refers to a missing vertex")

    def open_edges(self) -> int:
        """Edges used by one triangle only (0 for a closed solid)."""
        t = self.triangles
        edges = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        return int((counts == 1).sum())

    def volume(self) -> float:
        """Signed-volume sum (divergence theorem); absolute value for closed solids."""
        a, b, c = (self.vertices[self.triangles[:, i]] for i in range(3))
        return float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum()) / 6.0)

    def inside(self, points: Floats) -> npt.NDArray[np.bool_]:
        """True for points strictly enclosed by the solid."""
        pts = np.asarray(points, np.float64)
        xy = pts[:, :2] + NUDGE
        order = np.argsort(xy[:, 0], kind="stable")
        xs = xy[order, 0]
        crossings = np.zeros(len(pts), np.int64)
        tri = self.vertices[self.triangles]
        lo = tri[:, :, :2].min(axis=1)
        hi = tri[:, :, :2].max(axis=1)
        for t in range(len(tri)):
            a0 = int(np.searchsorted(xs, lo[t, 0], "left"))
            a1 = int(np.searchsorted(xs, hi[t, 0], "right"))
            if a0 == a1:
                continue
            cand = order[a0:a1]
            y = xy[cand, 1]
            cand = cand[(y >= lo[t, 1]) & (y <= hi[t, 1])]
            if len(cand) == 0:
                continue
            p0, p1, p2 = tri[t]
            d = (p1[1] - p2[1]) * (p0[0] - p2[0]) + (p2[0] - p1[0]) * (p0[1] - p2[1])
            if abs(d) < 1e-15:
                continue  # vertical triangle: a vertical ray never crosses it
            px, py = xy[cand, 0], xy[cand, 1]
            w0 = ((p1[1] - p2[1]) * (px - p2[0]) + (p2[0] - p1[0]) * (py - p2[1])) / d
            w1 = ((p2[1] - p0[1]) * (px - p2[0]) + (p0[0] - p2[0]) * (py - p2[1])) / d
            w2 = 1.0 - w0 - w1
            hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            z = w0 * p0[2] + w1 * p1[2] + w2 * p2[2]
            above = hit & (z > pts[cand, 2])
            np.add.at(crossings, cand[above], 1)
        return np.asarray(crossings % 2 == 1)


def _dedupe(points: Floats) -> tuple[Floats, Ints]:
    rounded = np.round(points, 6)
    vertices, inverse = np.unique(rounded, axis=0, return_inverse=True)
    return vertices.astype(np.float64), inverse.reshape(-1, 3).astype(np.int64)


def read_obj(path: Path) -> Solid:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v":
            vertices.append([float(v) for v in parts[1:4]])
        elif parts[0] == "f":
            idx = [int(p.split("/")[0]) for p in parts[1:]]
            idx = [i - 1 if i > 0 else len(vertices) + i for i in idx]
            for k in range(1, len(idx) - 1):  # fan triangulation of polygons
                faces.append([idx[0], idx[k], idx[k + 1]])
    return Solid(np.array(vertices, np.float64), np.array(faces, np.int64))


def read_stl(path: Path) -> Solid:
    data = path.read_bytes()
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * count == len(data):
            rec = np.frombuffer(
                data[84:],
                dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                count=count,
            )
            return Solid(*_dedupe(rec["v"].reshape(-1, 3).astype(np.float64)))
    pts = [
        [float(v) for v in line.split()[1:4]]
        for line in data.decode("utf-8", errors="replace").splitlines()
        if line.strip().startswith("vertex")
    ]
    if not pts or len(pts) % 3:
        raise SolidError(f"{path.name}: not a readable STL file")
    return Solid(*_dedupe(np.array(pts, np.float64)))


def read_dxf_3dfaces(path: Path) -> Solid:
    """``3DFACE`` entities of an ASCII DXF (quads are split in two triangles)."""
    lines = [s.strip() for s in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    pairs = list(zip(lines[0::2], lines[1::2], strict=False))
    tris: list[list[list[float]]] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "3DFACE":
            corner: dict[int, list[float]] = {k: [0.0, 0.0, 0.0] for k in range(4)}
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                c = int(pairs[i][0])
                if 10 <= c <= 13 or 20 <= c <= 23 or 30 <= c <= 33:
                    corner[c % 10][c // 10 - 1] = float(pairs[i][1])
                i += 1
            a, b, c3, d = (corner[k] for k in range(4))
            tris.append([a, b, c3])
            if d != c3:
                tris.append([a, c3, d])
            continue
        i += 1
    if not tris:
        raise SolidError(f"{path.name}: no 3DFACE entities found")
    return Solid(*_dedupe(np.array(tris, np.float64).reshape(-1, 3)))


def read_solid(path: Path) -> Solid:
    suffix = path.suffix.lower()
    readers = {".obj": read_obj, ".stl": read_stl, ".dxf": read_dxf_3dfaces}
    if suffix not in readers:
        raise SolidError(f"unsupported solid format {suffix!r}; use OBJ, STL or DXF")
    return readers[suffix](path)


def save_solid(project: Project, name: str, solid: Solid) -> None:
    record = project.register_object(KIND, name, metadata={"format": 1})
    v = bulk.write_table(
        project.path,
        record.object_id,
        "vertices",
        pa.table({c: solid.vertices[:, i] for i, c in enumerate("xyz")}),
    )
    t = bulk.write_table(
        project.path,
        record.object_id,
        "triangles",
        pa.table({c: solid.triangles[:, i] for i, c in enumerate(("a", "b", "c"))}),
    )
    project.update_object(
        record.object_id,
        metadata={"format": 1, "vertices": v.to_json(), "triangles": t.to_json()},
        description=f"Store solid {name!r}",
    )


def load_solid(project: Project, name: str) -> Solid:
    for r in project.objects(KIND):
        if r.name == name:
            v = bulk.read_table(project.path, bulk.FileRef.from_json(r.metadata["vertices"]))
            t = bulk.read_table(project.path, bulk.FileRef.from_json(r.metadata["triangles"]))
            return Solid(
                np.column_stack([v.column(c).to_numpy() for c in "xyz"]),
                np.column_stack([t.column(c).to_numpy() for c in ("a", "b", "c")]).astype(np.int64),
            )
    raise SolidError(f"no solid named {name!r}")


def solid_names(project: Project) -> list[str]:
    return [r.name for r in project.objects(KIND)]


def flag_blocks(model: BlockModel, solid: Solid, attribute: str) -> int:
    """Store 1 for blocks whose centroid is inside the solid, else 0; returns the count."""
    inside = solid.inside(model.grid.centroids()).astype(np.float64)
    model.set_attributes(
        {attribute: inside},
        {attribute: {"unit": None}},
        f"Flag blocks inside solid as {attribute!r}",
    )
    return int(inside.sum())


def grade_shell(model: BlockModel, attribute: str, cutoff: float) -> Solid:
    """Closed iso-surface of ``attribute`` at ``cutoff`` (marching cubes over block centres).

    Missing values count as below the cut-off. The block grid is padded by one
    layer below the cut-off on every side so the surface is always closed.
    """
    import pyvista as pv  # noqa: PLC0415

    grid = model.grid
    nx, ny, nz = grid.count
    values = model.values(attribute).reshape(nz, ny, nx)
    low = cutoff - 1.0
    padded = np.full((nz + 2, ny + 2, nx + 2), low)
    padded[1:-1, 1:-1, 1:-1] = np.where(np.isfinite(values), values, low)
    first = np.asarray(grid.origin) + 0.5 * np.asarray(grid.size) - np.asarray(grid.size)
    image = pv.ImageData(dimensions=(nx + 2, ny + 2, nz + 2), spacing=grid.size, origin=first)
    image.point_data["v"] = padded.ravel()  # x fastest, then y, then z
    surface = image.contour([cutoff], scalars="v").triangulate().clean()
    if surface.n_cells == 0:
        raise SolidError(f"no blocks reach {cutoff:g} in {attribute!r}")
    faces = np.asarray(surface.faces).reshape(-1, 4)[:, 1:]
    return Solid(np.asarray(surface.points, np.float64), faces.astype(np.int64))
