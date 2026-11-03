"""Open Mining Format (OMF v1) export and import.

OMF is the open exchange format of the Global Mining Guidelines Group,
written with the MIT-licensed ``omf`` package. Mapping:

* block model -> ``VolumeElement`` on a regular ``VolumeGridGeometry``
  (origin = model corner, axis_u/v from the azimuth, uniform tensors), one
  ``ScalarData`` per numeric attribute on the cells (i fastest, then j, k);
* surface -> ``SurfaceElement`` (plan Delaunay triangles of its points);
* solid -> ``SurfaceElement`` (its triangles), named with a ``solid:`` prefix
  in the description so it comes back as a solid;
* string set -> ``LineSetElement`` (one segment per string edge, closing
  edge for closed strings);
* drillhole traces -> one ``LineSetElement`` (desurveyed, 2 m step).

Import reads volumes with uniform tensors as block models, surfaces as
solids when closed or marked as solids, else as surfaces (vertices only;
MineCore surfaces are re-triangulated in plan), line sets as string sets
(segments chained into polylines). Point sets are skipped and reported.
Volume data must be on cells; vertex data, textures, colour legends and non-uniform tensors are
skipped and reported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel, Grid
from minecore.drillholes import DrillholeDatabase
from minecore.drillholes.workflow import hole_geometry
from minecore.project import Project
from minecore.solids import Solid, load_solid, save_solid, solid_names
from minecore.strings import String, load_strings, save_strings, string_names
from minecore.surfaces import Surface, load_surface, save_surface, surface_names

Floats = npt.NDArray[np.float64]
Ints = npt.NDArray[np.int64]
SOLID_TAG = "solid:"


class OmfError(MineCoreError):
    """OMF file cannot be written or read."""


@dataclass
class OmfReport:
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _omf() -> Any:
    import warnings  # noqa: PLC0415

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        import omf  # type: ignore[import-untyped]  # noqa: PLC0415

    return omf


def _segments(strings: list[String]) -> tuple[Floats, Ints]:
    verts, segs, start = [], [], 0
    for s in strings:
        n = len(s.points)
        if n < 2:
            continue
        idx = np.arange(start, start + n)
        pairs = np.column_stack([idx[:-1], idx[1:]])
        if s.closed and n > 2:
            pairs = np.vstack([pairs, [idx[-1], idx[0]]])
        verts.append(s.points)
        segs.append(pairs)
        start += n
    if not verts:
        raise OmfError("no strings with two or more points")
    return np.vstack(verts), np.vstack(segs).astype(np.int64)


def export_omf(  # noqa: PLR0913 - one selection list per object kind
    project: Project,
    path: Path,
    *,
    block_models: list[str] | None = None,
    surfaces: list[str] | None = None,
    solids: list[str] | None = None,
    strings: list[str] | None = None,
    drillholes: bool = True,
) -> OmfReport:
    """Write the named objects (all of each kind when ``None``) to an OMF file."""
    omf = _omf()
    report = OmfReport()
    elements = []
    for name in block_models if block_models is not None else _models(project):
        model = BlockModel.open(project, name)
        g = model.grid
        axes = g.axes
        data = []
        for attr in model.attributes:
            data.append(omf.ScalarData(name=attr, array=model.values(attr), location="cells"))
        geom = omf.VolumeGridGeometry(
            origin=list(g.origin),
            axis_u=list(axes[:, 0]),
            axis_v=list(axes[:, 1]),
            axis_w=list(axes[:, 2]),
            tensor_u=[g.size[0]] * g.count[0],
            tensor_v=[g.size[1]] * g.count[1],
            tensor_w=[g.size[2]] * g.count[2],
        )
        elements.append(omf.VolumeElement(name=name, geometry=geom, data=data))
        report.written.append(f"blok modeli {name}")
    for name in surfaces if surfaces is not None else surface_names(project):
        s = load_surface(project, name)
        geom = omf.SurfaceGeometry(vertices=s.points, triangles=s.triangles())
        elements.append(omf.SurfaceElement(name=name, geometry=geom))
        report.written.append(f"yüzey {name}")
    for name in solids if solids is not None else solid_names(project):
        so = load_solid(project, name)
        geom = omf.SurfaceGeometry(vertices=so.vertices, triangles=so.triangles)
        elements.append(omf.SurfaceElement(name=name, description=SOLID_TAG, geometry=geom))
        report.written.append(f"katı {name}")
    for name in strings if strings is not None else string_names(project):
        v, seg = _segments(load_strings(project, name))
        geom = omf.LineSetGeometry(vertices=v, segments=seg)
        elements.append(omf.LineSetElement(name=name, geometry=geom))
        report.written.append(f"çizgi seti {name}")
    if drillholes:
        for record in project.objects("drillhole_db"):
            db = DrillholeDatabase(project, record.object_id)
            traces = [String(g.trace(2.0), False) for g in hole_geometry(db).values()]
            if traces:
                v, seg = _segments(traces)
                geom = omf.LineSetGeometry(vertices=v, segments=seg)
                name = f"sondaj_izleri_{record.name}"
                elements.append(omf.LineSetElement(name=name, geometry=geom))
                report.written.append(f"sondaj izleri {record.name} ({len(traces)} kuyu)")
    if not elements:
        raise OmfError("nothing to export")
    proj = omf.Project(name=project.path.stem, elements=elements)
    try:
        proj.validate()
        omf.OMFWriter(proj, str(path))
    except Exception as exc:
        raise OmfError(f"OMF file could not be written: {exc}") from exc
    return report


def _models(project: Project) -> list[str]:
    return [r.name for r in project.objects("block_model")]


def _chains(vertices: Floats, segments: Ints) -> list[String]:
    """Chain segments into polylines (closed when a chain returns to its start)."""
    nbrs: dict[int, list[int]] = {}
    for a, b in segments.tolist():
        nbrs.setdefault(a, []).append(b)
        nbrs.setdefault(b, []).append(a)
    used: set[tuple[int, int]] = set()
    out = []
    starts = [v for v, n in nbrs.items() if len(n) != 2] + list(nbrs)
    for s in starts:
        for nxt in nbrs[s]:
            if (min(s, nxt), max(s, nxt)) in used:
                continue
            path, prev, cur = [s], s, nxt
            used.add((min(s, nxt), max(s, nxt)))
            while True:
                path.append(cur)
                if cur == s or len(nbrs[cur]) != 2:
                    break
                follow = nbrs[cur][0] if nbrs[cur][1] == prev else nbrs[cur][1]
                key = (min(cur, follow), max(cur, follow))
                if key in used:
                    break
                used.add(key)
                prev, cur = cur, follow
            closed = path[-1] == path[0] and len(path) > 3
            pts = vertices[path[:-1] if closed else path]
            out.append(String(np.asarray(pts, np.float64), closed))
    return out


def _free(name: str, taken: set[str]) -> str:
    base, n = name or "omf", 2
    out = base
    while out in taken:
        out, n = f"{base}_{n}", n + 1
    taken.add(out)
    return out


def import_omf(project: Project, path: Path) -> OmfReport:  # noqa: PLR0912, PLR0915 - one branch per kind
    """Read every supported element of an OMF file into the project (new names on clashes)."""
    omf = _omf()
    try:
        proj = omf.OMFReader(str(path)).get_project()
    except Exception as exc:
        raise OmfError(f"not a readable OMF file: {exc}") from exc
    report = OmfReport()
    taken = {*(_models(project)), *surface_names(project), *solid_names(project)}
    taken |= set(string_names(project))
    for el in proj.elements:
        kind = type(el).__name__
        geom = el.geometry
        name = _free(str(el.name), taken)
        if kind == "VolumeElement":
            tu, tv, tw = (
                np.asarray(t, np.float64) for t in (geom.tensor_u, geom.tensor_v, geom.tensor_w)
            )
            if any(np.ptp(t) > 1e-6 * max(t.max(), 1.0) for t in (tu, tv, tw)):
                report.skipped.append(f"{el.name}: düzensiz blok boyutları (desteklenmiyor)")
                continue
            u = np.asarray(geom.axis_u, np.float64)
            if abs(float(np.asarray(geom.axis_w)[2])) < 0.999:
                report.skipped.append(f"{el.name}: eğik (dalımlı) model (desteklenmiyor)")
                continue
            azimuth = math.degrees(math.atan2(-u[1], u[0]))
            origin = np.asarray(geom.origin, np.float64) + np.asarray(proj.origin, np.float64)
            grid = Grid(
                (float(origin[0]), float(origin[1]), float(origin[2])),
                (float(tu[0]), float(tv[0]), float(tw[0])),
                (len(tu), len(tv), len(tw)),
                round(azimuth, 9) % 360.0,
            )
            model = BlockModel.create(project, name, grid)
            values = {}
            for d in el.data:
                arr = np.asarray(d.array, dtype=object)
                if d.location != "cells" or arr.shape != (grid.blocks,):
                    report.skipped.append(f"{el.name}/{d.name}: hücre verisi değil")
                    continue
                try:
                    values[str(d.name)] = np.asarray(arr, np.float64)
                except (TypeError, ValueError):
                    report.skipped.append(f"{el.name}/{d.name}: sayısal değil")
            if values:
                model.set_attributes(values, {}, f"Import OMF attributes of {name!r}")
            report.written.append(f"blok modeli {name} ({len(values)} öznitelik)")
        elif kind == "SurfaceElement" and type(geom).__name__ == "SurfaceGeometry":
            v = np.asarray(geom.vertices, np.float64) + np.asarray(geom.origin, np.float64)
            v = v + np.asarray(proj.origin, np.float64)
            tri = np.asarray(geom.triangles, np.int64)
            solid = Solid(v, tri)
            if str(el.description).startswith(SOLID_TAG) or solid.open_edges() == 0:
                save_solid(project, name, solid)
                report.written.append(f"katı {name}")
            else:
                save_surface(project, name, Surface(v))
                report.written.append(f"yüzey {name}")
        elif kind == "LineSetElement":
            v = np.asarray(geom.vertices, np.float64) + np.asarray(geom.origin, np.float64)
            v = v + np.asarray(proj.origin, np.float64)
            chains = _chains(v, np.asarray(geom.segments, np.int64))
            save_strings(project, name, chains)
            report.written.append(f"çizgi seti {name} ({len(chains)} çizgi)")
        else:
            report.skipped.append(f"{el.name}: {kind} desteklenmiyor")
    return report
