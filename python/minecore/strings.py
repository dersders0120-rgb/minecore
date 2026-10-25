"""Strings (polylines) and wireframe linking of section outlines into solids.

Strings are read from ASCII DXF ``LWPOLYLINE`` (with elevation, group 38)
and ``POLYLINE``/``VERTEX`` entities, or from x, y, z text files with a
string id column, and stored per project as named string sets.

``link_sections`` builds a closed solid from closed outlines drawn on
successive sections (the classic interpretation-to-wireframe step):

* consecutive outlines are oriented the same way and their start points
  aligned (nearest vertex), then joined by a band of triangles that advances
  along both outlines by normalised arc length (Ganapathy & Dennehy 1982
  style "equal fraction" stitching), which never crosses itself for convex
  or mildly concave outlines;
* the first and last outlines are closed with planar caps (ear clipping in
  the outline's best-fit plane).

The result is checked for open edges; outlines must not self-intersect.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from minecore._core import MineCoreError
from minecore.project import Project, bulk
from minecore.solids import Solid

KIND = "string_set"
Floats = npt.NDArray[np.float64]


class StringError(MineCoreError):
    """Invalid string data."""


@dataclass(frozen=True)
class String:
    points: Floats  # n x 3
    closed: bool
    layer: str = ""


# -- input ------------------------------------------------------------------


def read_dxf_strings(path: Path) -> list[String]:  # noqa: PLR0912, PLR0915 - one DXF pass
    lines = [s.strip() for s in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    pairs = list(zip(lines[0::2], lines[1::2], strict=False))
    out: list[String] = []
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value == "LWPOLYLINE":
            pts: list[list[float]] = []
            layer, flag, elev = "", 0, 0.0
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                c, v = pairs[i]
                if c == "8":
                    layer = v
                elif c == "70":
                    flag = int(v)
                elif c == "38":
                    elev = float(v)
                elif c == "10":
                    pts.append([float(v), 0.0, 0.0])
                elif c == "20" and pts:
                    pts[-1][1] = float(v)
                i += 1
            if len(pts) >= 2:
                arr = np.array(pts)
                arr[:, 2] = elev
                out.append(String(arr, bool(flag & 1), layer))
            continue
        if code == "0" and value == "POLYLINE":
            layer, flag = "", 0
            i += 1
            while i < len(pairs) and pairs[i][0] != "0":
                if pairs[i][0] == "8":
                    layer = pairs[i][1]
                elif pairs[i][0] == "70":
                    flag = int(pairs[i][1])
                i += 1
            vertices: list[list[float]] = []
            while i < len(pairs) and pairs[i] == ("0", "VERTEX"):
                p = [0.0, 0.0, 0.0]
                i += 1
                while i < len(pairs) and pairs[i][0] != "0":
                    c = pairs[i][0]
                    if c in ("10", "20", "30"):
                        p[int(c) // 10 - 1] = float(pairs[i][1])
                    i += 1
                vertices.append(p)
            if len(vertices) >= 2:
                out.append(String(np.array(vertices), bool(flag & 1), layer))
            continue
        i += 1
    if not out:
        raise StringError(f"{path.name}: no LWPOLYLINE or POLYLINE entities found")
    return out


# -- storage ----------------------------------------------------------------


def save_strings(project: Project, name: str, strings: list[String]) -> None:
    sid = np.concatenate([np.full(len(s.points), k) for k, s in enumerate(strings)])
    pts = np.concatenate([s.points for s in strings])
    # Saving under an existing name replaces it (new bulk file, same object).
    existing = [r for r in project.objects(KIND) if r.name == name]
    record = (
        existing[0] if existing else project.register_object(KIND, name, metadata={"format": 1})
    )
    ref = bulk.write_table(
        project.path,
        record.object_id,
        "points",
        pa.table({"sid": sid, "x": pts[:, 0], "y": pts[:, 1], "z": pts[:, 2]}),
    )
    project.update_object(
        record.object_id,
        metadata={
            "format": 1,
            "points": ref.to_json(),
            "closed": [s.closed for s in strings],
            "layers": [s.layer for s in strings],
        },
        description=f"Store strings {name!r}",
    )


def load_strings(project: Project, name: str) -> list[String]:
    for r in project.objects(KIND):
        if r.name == name:
            t = bulk.read_table(project.path, bulk.FileRef.from_json(r.metadata["points"]))
            sid = t.column("sid").to_numpy()
            xyz = np.column_stack([t.column(c).to_numpy() for c in "xyz"]).astype(np.float64)
            closed, layers = r.metadata["closed"], r.metadata["layers"]
            return [String(xyz[sid == k], bool(closed[k]), layers[k]) for k in range(len(closed))]
    raise StringError(f"no string set named {name!r}")


def string_names(project: Project) -> list[str]:
    return [r.name for r in project.objects(KIND)]


# -- geometry ---------------------------------------------------------------


def _unique_ring(points: Floats) -> Floats:
    p = np.asarray(points, np.float64)
    if len(p) > 1 and np.allclose(p[0], p[-1]):
        p = p[:-1]
    keep = np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-9]
    p = p[keep]
    if len(p) < 3:
        raise StringError("an outline needs at least three distinct points")
    return p


def _plane_axes(p: Floats) -> tuple[Floats, Floats]:
    """Normal (Newell) and the two coordinate axes kept for the 2-D projection."""
    n = np.zeros(3)
    q = np.roll(p, -1, axis=0)
    n[0] = ((p[:, 1] - q[:, 1]) * (p[:, 2] + q[:, 2])).sum()
    n[1] = ((p[:, 2] - q[:, 2]) * (p[:, 0] + q[:, 0])).sum()
    n[2] = ((p[:, 0] - q[:, 0]) * (p[:, 1] + q[:, 1])).sum()
    if np.linalg.norm(n) < 1e-12:
        raise StringError("an outline has no area")
    drop = int(np.argmax(np.abs(n)))
    keep = np.array([a for a in range(3) if a != drop])
    return n / np.linalg.norm(n), keep


def _signed_area(xy: Floats) -> float:
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * float((x * np.roll(y, -1) - np.roll(x, -1) * y).sum())


def _ear_clip(xy: Floats) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon given counter-clockwise."""
    idx = list(range(len(xy)))
    tris: list[tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 10 * len(xy) ** 2:
        guard += 1
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            pa_, pb, pc = xy[a], xy[b], xy[c]
            cross = (pb[0] - pa_[0]) * (pc[1] - pa_[1]) - (pb[1] - pa_[1]) * (pc[0] - pa_[0])
            if cross <= 1e-12:
                continue  # reflex or degenerate corner
            if any(_in_triangle(xy[o], pa_, pb, pc) for o in idx if o not in (a, b, c)):
                continue
            tris.append((a, b, c))
            idx.pop(k)
            break
        else:
            raise StringError("outline cannot be triangulated (self-intersecting?)")
    tris.append((idx[0], idx[1], idx[2]))
    return tris


def _in_triangle(p: Floats, a: Floats, b: Floats, c: Floats) -> bool:
    def side(u: Floats, v: Floats, w: Floats) -> float:
        return float((v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0]))

    return side(a, b, p) >= 0 and side(b, c, p) >= 0 and side(c, a, p) >= 0


def _fractions(ring: Floats) -> Floats:
    seg = np.linalg.norm(np.diff(np.vstack([ring, ring[:1]]), axis=0), axis=1)
    return np.asarray(np.r_[0.0, np.cumsum(seg)[:-1]] / seg.sum(), np.float64)


def link_sections(outlines: list[Floats]) -> Solid:
    """Closed solid through closed outlines given in section order."""
    if len(outlines) < 2:
        raise StringError("at least two outlines are needed")
    rings = [_unique_ring(o) for o in outlines]
    normal, keep = _plane_axes(rings[0])
    # Orient every ring counter-clockwise seen along the first ring's normal axis.
    drop = 3 - int(keep.sum())
    oriented = [
        r[::-1] if _signed_area(r[:, keep]) * np.sign(normal[drop]) < 0 else r for r in rings
    ]
    # Align start points: rotate each ring so its first vertex is nearest the previous start.
    for n in range(1, len(oriented)):
        d = np.linalg.norm(oriented[n] - oriented[n - 1][0], axis=1)
        oriented[n] = np.roll(oriented[n], -int(np.argmin(d)), axis=0)
    vertices = np.vstack(oriented)
    offset = np.cumsum([0] + [len(r) for r in oriented])
    tris: list[tuple[int, int, int]] = []
    for n in range(len(oriented) - 1):
        a, b = oriented[n], oriented[n + 1]
        fa, fb = np.r_[_fractions(a), 1.0], np.r_[_fractions(b), 1.0]
        i = j = 0
        while i < len(a) or j < len(b):
            ia, ia1 = offset[n] + i % len(a), offset[n] + (i + 1) % len(a)
            jb, jb1 = offset[n + 1] + j % len(b), offset[n + 1] + (j + 1) % len(b)
            if j >= len(b) or (i < len(a) and fa[i + 1] <= fb[j + 1]):
                tris.append((ia, ia1, jb))
                i += 1
            else:
                tris.append((ia, jb1, jb))
                j += 1
    for n, flip in ((0, True), (len(oriented) - 1, False)):
        ring = oriented[n]
        xy = ring[:, keep]
        ccw = _signed_area(xy) > 0
        order = np.arange(len(ring)) if ccw else np.arange(len(ring))[::-1]
        for t in _ear_clip(xy[order]):
            tri = tuple(int(offset[n] + order[v]) for v in t)
            tris.append((tri[0], tri[2], tri[1]) if flip else (tri[0], tri[1], tri[2]))
    solid = Solid(vertices, np.array(tris, np.int64))
    if _signed_volume(solid) < 0:
        solid = Solid(vertices, solid.triangles[:, ::-1].copy())
    return solid


def _signed_volume(solid: Solid) -> float:
    a, b, c = (solid.vertices[solid.triangles[:, i]] for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def section_order(strings: list[String]) -> list[String]:
    """Closed strings sorted along the axis on which their centroids spread most."""
    closed = [s for s in strings if s.closed or np.allclose(s.points[0], s.points[-1])]
    if len(closed) < 2:
        raise StringError("at least two closed outlines are needed")
    centres = np.array([s.points.mean(axis=0) for s in closed])
    axis = int(np.argmax(centres.max(axis=0) - centres.min(axis=0)))
    return [closed[k] for k in np.argsort(centres[:, axis], kind="stable")]
