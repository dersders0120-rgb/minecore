"""Solid (wireframe) tools: verification, repair, booleans and cutting by a surface.

Verification reports open edges (used by one triangle), non-manifold edges
(used by more than two), degenerate triangles (zero area), duplicate
triangles, edges walked the same way by both of their triangles
(inconsistent orientation) and whether the normals point inwards (negative
signed volume). Repair merges coincident vertices, drops degenerate and
duplicate triangles, makes the orientation consistent across each connected
shell (flood fill over shared edges) and turns inward shells outwards.

Booleans (union, intersection, difference) and cuts by a surface are done on
a voxel raster: the inside indicator of each solid is sampled at voxel
centres, combined, and the result surface is extracted with marching cubes.
The result is always closed, but it is accurate to about one voxel (edges
and corners are rounded at the voxel size); choose the voxel size from the
detail that matters (e.g. a quarter of the block size).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from minecore.blockmodel import Grid
from minecore.solids import Solid, SolidError, iso_solid
from minecore.surfaces import Surface

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]


@dataclass(frozen=True)
class SolidReport:
    triangles: int
    vertices: int
    open_edges: int
    non_manifold_edges: int
    degenerate: int
    duplicates: int
    flipped_edges: int  # interior edges both triangles walk the same way
    shells: int
    volume: float  # signed; negative = normals inwards
    area: float

    @property
    def valid(self) -> bool:
        return (
            self.open_edges == 0
            and self.non_manifold_edges == 0
            and self.degenerate == 0
            and self.duplicates == 0
            and self.flipped_edges == 0
            and self.volume > 0
        )


def _areas(s: Solid) -> Floats:
    a, b, c = (s.vertices[s.triangles[:, i]] for i in range(3))
    return np.asarray(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1), np.float64)


def _signed_volume(v: Floats, t: Ints) -> float:
    a, b, c = (v[t[:, i]] for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def _directed_edges(t: Ints) -> Ints:
    return np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])


def _shells(t: Ints) -> Ints:
    """Connected component (over shared edges) of each triangle."""
    n = len(t)
    edges = np.sort(_directed_edges(t), axis=1)
    owner = np.tile(np.arange(n), 3)
    _, inv = np.unique(edges, axis=0, return_inverse=True)
    parent = np.arange(n)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return i

    first: dict[int, int] = {}
    for e, tri in zip(inv.ravel().tolist(), owner.tolist(), strict=True):
        if e in first:
            a, b = find(first[e]), find(tri)
            if a != b:
                parent[a] = b
        else:
            first[e] = tri
    roots = np.array([find(i) for i in range(n)])
    _, label = np.unique(roots, return_inverse=True)
    return np.asarray(label, np.int64)


def verify(s: Solid) -> SolidReport:
    t = s.triangles
    und = np.sort(_directed_edges(t), axis=1)
    _, counts = np.unique(und, axis=0, return_counts=True)
    directed = _directed_edges(t)
    _, dcounts = np.unique(directed, axis=0, return_counts=True)
    tri_sorted = np.sort(t, axis=1)
    _, tcounts = np.unique(tri_sorted, axis=0, return_counts=True)
    areas = _areas(s)
    return SolidReport(
        len(t),
        len(s.vertices),
        int((counts == 1).sum()),
        int((counts > 2).sum()),
        int((areas <= 1e-12).sum()),
        int((tcounts - 1).sum()),
        int((dcounts > 1).sum()),
        int(_shells(t).max() + 1) if len(t) else 0,
        _signed_volume(s.vertices, t),
        float(areas.sum()),
    )


def repair(s: Solid, tolerance: float = 1e-6) -> Solid:
    """Merge vertices, drop bad triangles, orient every shell consistently and outwards."""
    key = np.round(s.vertices / max(tolerance, 1e-12)).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    v = s.vertices[first]
    t = inv.ravel()[s.triangles]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 2] != t[:, 0])]
    a, b, c = (v[t[:, i]] for i in range(3))
    t = t[np.linalg.norm(np.cross(b - a, c - a), axis=1) > 1e-12]
    _, keep = np.unique(np.sort(t, axis=1), axis=0, return_index=True)
    t = t[np.sort(keep)]
    if len(t) == 0:
        raise SolidError("nothing is left after removing degenerate triangles")
    t = _orient(t)
    shells = _shells(t)
    for k in range(int(shells.max()) + 1):
        sel = shells == k
        if _signed_volume(v, t[sel]) < 0:
            t[sel] = t[sel][:, ::-1]
    return Solid(v, t.astype(np.int64))


def _orient(t: Ints) -> Ints:
    """Flip triangles so neighbours walk shared edges in opposite directions."""
    t = t.copy()
    n = len(t)
    by_edge: dict[tuple[int, int], list[int]] = {}
    for i, (a, b, c) in enumerate(t.tolist()):
        for e in ((a, b), (b, c), (c, a)):
            by_edge.setdefault((min(e), max(e)), []).append(i)
    seen = np.zeros(n, bool)
    for start in range(n):
        if seen[start]:
            continue
        seen[start] = True
        queue = deque([start])
        while queue:
            i = queue.popleft()
            a, b, c = t[i].tolist()
            for u, w in ((a, b), (b, c), (c, a)):
                for j in by_edge[(min(u, w), max(u, w))]:
                    if j == i or seen[j]:
                        continue
                    x, y, z = t[j].tolist()
                    same = (x, y) == (u, w) or (y, z) == (u, w) or (z, x) == (u, w)
                    if same:
                        t[j] = t[j][::-1]
                    seen[j] = True
                    queue.append(j)
    return t


class Boolean(StrEnum):
    UNION = "union"
    INTERSECTION = "intersection"
    DIFFERENCE = "difference"  # a minus b


def _raster(lo: Floats, hi: Floats, voxel: float) -> Grid:
    if not (math.isfinite(voxel) and voxel > 0):
        raise SolidError("voxel size must be > 0")
    count = np.maximum(np.ceil((hi - lo) / voxel).astype(np.int64), 1)
    if int(np.prod(count)) > 30_000_000:
        raise SolidError("too many voxels; use a larger voxel size")
    return Grid(tuple(float(v) for v in lo), (voxel, voxel, voxel), tuple(int(c) for c in count))  # type: ignore[arg-type]


def boolean(a: Solid, b: Solid, op: Boolean, voxel: float) -> Solid:
    """``a op b`` on a voxel raster (see the module text)."""
    lo = np.minimum(a.vertices.min(axis=0), b.vertices.min(axis=0)) - voxel
    hi = np.maximum(a.vertices.max(axis=0), b.vertices.max(axis=0)) + voxel
    grid = _raster(lo, hi, voxel)
    c = grid.centroids()
    ia, ib = a.inside(c), b.inside(c)
    keep = {
        Boolean.UNION: ia | ib,
        Boolean.INTERSECTION: ia & ib,
        Boolean.DIFFERENCE: ia & ~ib,
    }[Boolean(op)]
    if not keep.any():
        raise SolidError(f"the {Boolean(op).value} is empty")
    return iso_solid(grid, keep.astype(np.float64), 0.5)


def cut_by_surface(s: Solid, surface: Surface, keep_below: bool, voxel: float) -> Solid:
    """The part of ``s`` below (or above) a surface, on a voxel raster."""
    lo = s.vertices.min(axis=0) - voxel
    hi = s.vertices.max(axis=0) + voxel
    grid = _raster(lo, hi, voxel)
    c = grid.centroids()
    z = surface.elevation(c[:, :2])[0]
    side = c[:, 2] < z if keep_below else c[:, 2] > z
    keep = s.inside(c) & side
    if not keep.any():
        raise SolidError("nothing of the solid is on that side of the surface")
    return iso_solid(grid, keep.astype(np.float64), 0.5)
