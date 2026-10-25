"""Printable section and plan plots (PDF/PNG/SVG) without the workbench.

A plot shows, for one section plane and slab:

* the block model slice through the plane, coloured by an attribute;
* drillhole traces inside the slab and the samples of one field coloured by
  value;
* surfaces cut by the plane (topography, pit, design) as lines;
* solids cut by the plane (domain, pit, stopes) as outlines.

Axis 0 is a north-south section (x constant, looking west to east the
horizontal axis is northing), axis 1 an east-west section (y constant), and
axis 2 a plan (z constant; surfaces are not drawn in plan).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import numpy.typing as npt

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel, Grid
from minecore.blockmodel.workflow import interval_samples
from minecore.drillholes import DrillholeDatabase
from minecore.drillholes.workflow import hole_geometry
from minecore.project import Project
from minecore.solids import Solid, load_solid
from minecore.surfaces import load_surface

Floats = npt.NDArray[np.float64]
AXIS_NAMES = ("Doğu (X)", "Kuzey (Y)", "Kot (Z)")
SECTION_NAMES = ("Kuzey-güney kesiti, X =", "Doğu-batı kesiti, Y =", "Plan, Z =")


class PlotError(MineCoreError):
    """Invalid plot request."""


@dataclass(frozen=True)
class SectionSpec:
    axis: int  # 0: x constant, 1: y constant, 2: plan (z constant)
    at: float
    width: float  # slab thickness for drillholes and samples
    model: str | None = None
    attribute: str | None = None
    drill: tuple[str, str] | None = None  # (table, field)
    surfaces: tuple[str, ...] = ()
    solids: tuple[str, ...] = ()
    title: str = ""

    def __post_init__(self) -> None:
        if self.axis not in (0, 1, 2) or not self.width > 0:
            raise PlotError("axis must be 0, 1 or 2 and the slab width > 0")


@dataclass
class SectionPlot:
    path: Path
    blocks: int = 0
    samples: int = 0
    holes: int = 0
    surface_lines: dict[str, int] = field(default_factory=dict)
    solid_segments: dict[str, int] = field(default_factory=dict)


def _plane_axes(axis: int) -> tuple[int, int]:
    return {0: (1, 2), 1: (0, 2), 2: (0, 1)}[axis]


def cut_solid(solid: Solid, axis: int, at: float) -> Floats:
    """Segments (m x 2 x 3) where the plane ``coord[axis] = at`` cuts the solid."""
    tri = solid.vertices[solid.triangles]
    d = tri[:, :, axis] - at
    d = np.where(np.abs(d) < 1e-9, 1e-9, d)  # vertices on the plane count as above
    above = d > 0
    crossing = above.any(axis=1) & ~above.all(axis=1)
    out = []
    for t, dd in zip(tri[crossing], d[crossing], strict=True):
        pts = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            if (dd[a] > 0) != (dd[b] > 0):
                s = dd[a] / (dd[a] - dd[b])
                pts.append(t[a] + s * (t[b] - t[a]))
        if len(pts) == 2:
            out.append(pts)
    return np.asarray(out, np.float64).reshape(-1, 2, 3)


def block_section_polygons(
    grid: Grid, axis: int, at: float
) -> tuple[npt.NDArray[np.int64], Floats]:
    """Blocks cut by the world plane ``coord[axis] = at`` and their outlines in that plane.

    Returns flat block indices and ``m x 4 x 2`` polygons in the plane's
    (horizontal, vertical) coordinates (see ``_plane_axes``). Works for rotated
    models (ADR-0009), where a world section cuts blocks obliquely: each block
    is drawn over the stretch of its footprint that the plane crosses.
    """
    nx, ny, nz = grid.count
    dx, dy, dz = grid.size
    oz = grid.origin[2]
    jj, ii = np.divmod(np.arange(nx * ny), nx)
    local = np.stack(
        [
            np.column_stack([ii + a, jj + b, np.zeros(nx * ny)]) * np.array([dx, dy, 0.0])
            for a, b in ((0, 0), (1, 0), (1, 1), (0, 1))
        ],
        axis=1,
    )  # columns x 4 corners x 3, counter-clockwise
    foot = grid.to_world(local.reshape(-1, 3)).reshape(nx * ny, 4, 3)[:, :, :2]
    if axis == 2:
        k = int(np.floor((at - oz) / dz))
        if not 0 <= k < nz:
            return np.zeros(0, np.int64), np.zeros((0, 4, 2))
        return np.arange(nx * ny, dtype=np.int64) + k * nx * ny, foot
    other = 1 - axis  # the horizontal plane coordinate
    d = foot[:, :, axis] - at
    d = np.where(np.abs(d) < 1e-9, 1e-9, d)
    lo = np.full(nx * ny, np.inf)
    hi = np.full(nx * ny, -np.inf)
    for a in range(4):
        b = (a + 1) % 4
        cross = (d[:, a] > 0) != (d[:, b] > 0)
        t = np.where(cross, d[:, a] / np.where(cross, d[:, a] - d[:, b], 1.0), 0.0)
        o = foot[:, a, other] + t * (foot[:, b, other] - foot[:, a, other])
        lo = np.where(cross, np.minimum(lo, o), lo)
        hi = np.where(cross, np.maximum(hi, o), hi)
    cols = np.flatnonzero(hi > lo)
    lev = np.repeat(np.arange(nz), cols.size)
    c = np.tile(cols, nz)
    z0, z1 = oz + lev * dz, oz + (lev + 1) * dz
    polys = np.stack(
        [
            np.column_stack([lo[c], z0]),
            np.column_stack([hi[c], z0]),
            np.column_stack([hi[c], z1]),
            np.column_stack([lo[c], z1]),
        ],
        axis=1,
    )
    return (c + lev * nx * ny).astype(np.int64), polys


def section_plot(  # noqa: PLR0912, PLR0915 - one figure, one pass per layer
    project: Project,
    spec: SectionSpec,
    path: Path,
    *,
    db: DrillholeDatabase | None = None,
    paper: tuple[float, float] = (16.54, 11.69),  # A3 landscape, inches
) -> SectionPlot:
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg", force=False)
    from matplotlib.collections import LineCollection  # noqa: PLC0415
    from matplotlib.figure import Figure  # noqa: PLC0415

    h_ax, v_ax = _plane_axes(spec.axis)
    result = SectionPlot(path)
    fig = Figure(figsize=paper)
    ax = fig.add_subplot(111)
    ax.set_aspect("equal")
    ax.set_xlabel(f"{AXIS_NAMES[h_ax]} (m)")
    ax.set_ylabel(f"{AXIS_NAMES[v_ax]} (m)")
    ax.grid(True, color="#bdbdbd", linewidth=0.4)
    if spec.model:
        model = BlockModel.open(project, spec.model)
        g = model.grid
        if g.rotated:
            flat, polys = block_section_polygons(g, spec.axis, spec.at)
            if flat.size == 0:
                raise PlotError(f"the plane is outside block model {spec.model!r}")
            if spec.attribute:
                from matplotlib.collections import PolyCollection  # noqa: PLC0415

                vals = model.values(spec.attribute)[flat]
                ok = np.isfinite(vals)
                coll = PolyCollection(list(polys[ok]), cmap="viridis", alpha=0.85)
                coll.set_array(vals[ok])
                coll.set_edgecolor("face")
                ax.add_collection(coll)
                unit = model.attributes[spec.attribute].get("unit")
                fig.colorbar(coll, ax=ax, shrink=0.6, pad=0.01).set_label(
                    f"Blok: {spec.attribute}" + (f" ({unit})" if unit else "")
                )
                result.blocks = int(ok.sum())
        else:
            idx = int(np.floor((spec.at - g.origin[spec.axis]) / g.size[spec.axis]))
            if not 0 <= idx < g.count[spec.axis]:
                raise PlotError(f"the plane is outside block model {spec.model!r}")
            if spec.attribute:
                nx, ny, nz = g.count
                cube = model.values(spec.attribute).reshape(nz, ny, nx)
                if spec.axis == 0:
                    plane = cube[:, :, idx]
                elif spec.axis == 1:
                    plane = cube[:, idx, :]
                else:
                    plane = cube[idx, :, :]
                he = g.origin[h_ax] + g.size[h_ax] * np.arange(g.count[h_ax] + 1)
                ve = g.origin[v_ax] + g.size[v_ax] * np.arange(g.count[v_ax] + 1)
                masked = np.ma.masked_invalid(plane)
                mesh = ax.pcolormesh(he, ve, masked, cmap="viridis", alpha=0.85, shading="flat")
                unit = model.attributes[spec.attribute].get("unit")
                fig.colorbar(mesh, ax=ax, shrink=0.6, pad=0.01).set_label(
                    f"Blok: {spec.attribute}" + (f" ({unit})" if unit else "")
                )
                result.blocks = int(masked.count())
    if spec.drill is not None or db is not None:
        if db is None:
            dbs = project.objects("drillhole_db")
            if not dbs:
                raise PlotError("the project has no drillhole database")
            db = DrillholeDatabase(project, dbs[0].object_id)
        lines = []
        for geo in hole_geometry(db).values():
            tr = geo.trace(2.0)
            inside = np.abs(tr[:, spec.axis] - spec.at) <= spec.width / 2
            if inside.sum() >= 2:
                pts = np.where(inside[:, None], tr, np.nan)
                lines.append(pts[:, [h_ax, v_ax]])
                result.holes += 1
        if lines:
            ax.add_collection(
                LineCollection(
                    [seg for ln in lines for seg in _runs(ln)], colors="#424242", linewidths=0.8
                )
            )
        if spec.drill is not None:
            samples = interval_samples(db, *spec.drill)
            keep = np.abs(samples.xyz[:, spec.axis] - spec.at) <= spec.width / 2
            if keep.any():
                sc = ax.scatter(
                    samples.xyz[keep, h_ax],
                    samples.xyz[keep, v_ax],
                    c=samples.values[keep],
                    cmap="plasma",
                    s=14,
                    edgecolors="black",
                    linewidths=0.3,
                    zorder=5,
                )
                fig.colorbar(sc, ax=ax, shrink=0.6, pad=0.01).set_label(
                    f"Sondaj: {spec.drill[1]}" + (f" ({samples.unit})" if samples.unit else "")
                )
            result.samples = int(keep.sum())
    colours = ("#6d4c41", "#d32f2f", "#1976d2", "#388e3c", "#7b1fa2")
    if spec.axis != 2:
        from scipy.interpolate import LinearNDInterpolator  # noqa: PLC0415

        for n, name in enumerate(spec.surfaces):
            surface = load_surface(project, name)
            lo, hi = surface.points[:, h_ax].min(), surface.points[:, h_ax].max()
            u = np.linspace(lo, hi, 600)
            xy = np.empty((u.size, 2))
            xy[:, h_ax] = u
            xy[:, spec.axis] = spec.at
            z = LinearNDInterpolator(surface.points[:, :2], surface.points[:, 2])(xy)
            ax.plot(u, z, color=colours[n % len(colours)], linewidth=1.4, label=name)
            result.surface_lines[name] = int(np.isfinite(z).sum())
    for n, name in enumerate(spec.solids):
        segs = cut_solid(load_solid(project, name), spec.axis, spec.at)
        if len(segs):
            ax.add_collection(
                LineCollection(
                    list(segs[:, :, [h_ax, v_ax]]),
                    colors=colours[(n + 1) % len(colours)],
                    linewidths=1.2,
                    label=name,
                )
            )
        result.solid_segments[name] = len(segs)
    ax.autoscale_view()
    if spec.model:
        # Frame the block model (surfaces such as a design raster extend further).
        g = BlockModel.open(project, spec.model).grid
        pad = 0.05
        low, high = g.bounds()
        for axis, setter in ((h_ax, ax.set_xlim), (v_ax, ax.set_ylim)):
            start, end = float(low[axis]), float(high[axis])
            setter(start - pad * (end - start), end + pad * (end - start))
    ax.ticklabel_format(useOffset=False, style="plain")
    if spec.surfaces or spec.solids:
        ax.legend(loc="upper right", fontsize=8)
    title = spec.title or project.info.name
    ax.set_title(
        f"{title} - {SECTION_NAMES[spec.axis]} {spec.at:,.1f} m "
        f"(kalınlık {spec.width:g} m) - {date.today().isoformat()}"
    )
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    return result


def _runs(points: Floats) -> list[Floats]:
    """Split a polyline at NaN rows into runs of at least two points."""
    ok = np.isfinite(points).all(axis=1)
    runs, start = [], None
    for i, good in enumerate(np.r_[ok, False]):
        if good and start is None:
            start = i
        elif not good and start is not None:
            if i - start >= 2:
                runs.append(points[start:i])
            start = None
    return runs
