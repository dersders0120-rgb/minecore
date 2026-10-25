"""Turn a block pit (set of mined blocks) into design outputs.

* ``bench_outlines``: per bench, the closed boundary loops of the mined
  blocks in plan (block edges), at the bench crest (top) and toe (bottom).
  Outer boundaries run counter-clockwise, holes clockwise.
* ``pit_surface``: the mined-out surface as x, y, z points at block column
  centres: the floor of the deepest mined block where a column is mined,
  otherwise the original surface (topography, else the top of rock).
* ``pit_solid``: closed iso-surface around the mined blocks (marching cubes
  at 0.5 of the mined indicator), for volume checks and CAD export.

These are block-resolution outlines; ramps, berms and smoothed toe/crest
lines (pit design) are not generated; see docs/KNOWN_LIMITATIONS.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from minecore import surfaces
from minecore.blockmodel import BlockModel, Grid
from minecore.io.dxf import Polyline, write_dxf
from minecore.pitopt.economics import PitOptError
from minecore.project import Project
from minecore.solids import Solid, iso_solid
from minecore.surfaces import Surface

Floats = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]
Loop = list[tuple[int, int]]


def _loops(cells: Mask) -> list[Loop]:
    """Boundary loops (grid-node indices) of a 2-D cell mask, region on the left."""
    ny, nx = cells.shape
    pad = np.zeros((ny + 2, nx + 2), bool)
    pad[1:-1, 1:-1] = cells
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    jj, ii = np.nonzero(cells)
    for j, i in zip(jj.tolist(), ii.tolist(), strict=True):
        if not pad[j, i + 1]:  # south neighbour empty: edge west -> east
            nxt[(i, j)].append((i + 1, j))
        if not pad[j + 1, i + 2]:  # east
            nxt[(i + 1, j)].append((i + 1, j + 1))
        if not pad[j + 2, i + 1]:  # north: east -> west
            nxt[(i + 1, j + 1)].append((i, j + 1))
        if not pad[j + 1, i]:  # west
            nxt[(i, j + 1)].append((i, j))
    loops: list[Loop] = []
    while nxt:
        start = next(iter(nxt))
        loop = [start]
        node = start
        while True:
            ends = nxt[node]
            end = ends.pop()
            if not ends:
                del nxt[node]
            if end == start:
                break
            loop.append(end)
            node = end
        loops.append(_simplify(loop))
    return loops


def _simplify(loop: Loop) -> Loop:
    """Drop vertices in the middle of straight runs."""
    out: Loop = []
    n = len(loop)
    for k in range(n):
        a, b, c = loop[k - 1], loop[k], loop[(k + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) != (b[1] - a[1]) * (c[0] - b[0]):
            out.append(b)
    return out


def bench_outlines(grid: Grid, mined: Mask, name: str = "PIT") -> list[Polyline]:
    """Crest and toe loops of every bench that has mined blocks."""
    nx, ny, nz = grid.count
    cube = np.asarray(mined, bool).reshape(nz, ny, nx)
    dx, dy, dz = grid.size
    lines: list[Polyline] = []
    for k in range(nz - 1, -1, -1):
        if not cube[k].any():
            continue
        crest, toe = (k + 1) * dz, k * dz
        for loop in _loops(cube[k]):
            ij = np.array([(i * dx, j * dy) for i, j in loop], np.float64)
            for layer, z in ((f"{name}_CREST", crest), (f"{name}_TOE", toe)):
                local = np.column_stack([ij, np.full(len(ij), z)])
                lines.append(Polyline(layer, grid.to_world(local)))
    return lines


def pit_surface(
    grid: Grid, mined: Mask, topography: Surface | None = None, rock: Mask | None = None
) -> Surface:
    nx, ny, nz = grid.count
    cube = np.asarray(mined, bool).reshape(nz, ny, nx)
    if not cube.any():
        raise PitOptError("no mined blocks")
    centres = grid.centroids()[: nx * ny, :2]
    oz, dz = grid.origin[2], grid.size[2]
    if topography is not None:
        z, _ = topography.elevation(centres)
    elif rock is not None:
        r = np.asarray(rock, bool).reshape(nz, ny, nx)
        top = nz - np.argmax(r[::-1], axis=0)  # index above the highest rock block
        z = np.where(r.any(axis=0), oz + top * dz, oz).ravel().astype(np.float64)
    else:
        z = np.full(nx * ny, oz + nz * dz)
    any_mined = cube.any(axis=0)
    lowest = np.argmax(cube, axis=0)  # first (deepest) mined block per column
    floor = (oz + lowest * dz).astype(np.float64)
    z = np.where(any_mined.ravel(), np.minimum(floor.ravel(), z), z)
    return Surface(np.column_stack([centres, z]))


def pit_solid(grid: Grid, mined: Mask) -> Solid:
    return iso_solid(grid, np.asarray(mined, np.float64), 0.5)


@dataclass(frozen=True)
class PitExport:
    last_shell: int
    blocks: int
    volume: float  # m3 of the pit solid
    outlines: int
    surface: Surface


def export_pit(  # noqa: PLR0913 - options are keyword-only
    project: Project,
    model: BlockModel,
    path: Path,
    *,
    last_shell: int | None = None,
    phases: bool = False,
    topography: str | None = None,
    save_surface: str | None = None,
) -> PitExport:
    """Write the pit of shells ``1..last_shell`` (outlines + solid) to a DXF file.

    Layers: ``PIT_CREST``/``PIT_TOE`` per bench, ``PB<n>_CREST``/``PB<n>_TOE``
    per pushback when ``phases`` is set, and ``PIT_SOLID`` (3DFACE).
    """
    if "pit_shell" not in model.attributes:
        raise PitOptError(f"{model.name} has no pit_shell; run the pit optimisation first")
    shell = model.values("pit_shell")
    last = last_shell or int(np.nanmax(shell))
    mined = (shell >= 1) & (shell <= last)
    if not mined.any():
        raise PitOptError("the pit is empty")
    grid = model.grid
    lines = bench_outlines(grid, mined)
    if phases and "pit_phase" in model.attributes:
        phase = model.values("pit_phase")
        for p in range(1, int(np.nanmax(phase)) + 1):
            if (mined & (phase == p)).any():
                lines += bench_outlines(grid, mined & (phase == p), f"PB{p}")
    solid = pit_solid(grid, mined)
    write_dxf(path, lines, [("PIT_SOLID", solid.vertices[solid.triangles])])
    topo = surfaces.load_surface(project, topography) if topography else None
    rock = model.values("kaya") > 0.5 if "kaya" in model.attributes else None
    mined_out = pit_surface(grid, mined, topo, rock)
    if save_surface:
        surfaces.save_surface(project, save_surface, mined_out)
    return PitExport(last, int(mined.sum()), solid.volume(), len(lines), mined_out)


@dataclass(frozen=True)
class PeriodSurface:
    period: int
    blocks: int  # blocks mined in this period
    cumulative_blocks: int
    name: str  # stored surface


def period_surfaces(
    project: Project,
    model: BlockModel,
    *,
    topography: str | None = None,
    prefix: str = "donem",
    dxf: Path | None = None,
) -> list[PeriodSurface]:
    """End-of-period mined-out surfaces from the stored schedule (``pit_period``).

    Surface ``<prefix>_<p>`` is the topography (or top of rock) lowered to the
    floor of every block mined up to and including period ``p``. With ``dxf``
    the cumulative pit outlines of each period are written on layers
    ``Y<p>_CREST``/``Y<p>_TOE``.
    """
    if "pit_period" not in model.attributes:
        raise PitOptError(f"{model.name} has no pit_period; run the pit schedule first")
    period = np.nan_to_num(model.values("pit_period"), nan=0.0).astype(np.int64)
    last = int(period.max())
    if last < 1:
        raise PitOptError("the schedule mines no blocks")
    grid = model.grid
    topo = surfaces.load_surface(project, topography) if topography else None
    rock = model.values("kaya") > 0.5 if "kaya" in model.attributes else None
    out, lines = [], []
    for p in range(1, last + 1):
        mined = (period > 0) & (period <= p)
        name = f"{prefix}_{p}"
        surfaces.save_surface(project, name, pit_surface(grid, mined, topo, rock))
        if dxf is not None:
            lines += bench_outlines(grid, mined, f"Y{p}")
        out.append(PeriodSurface(p, int((period == p).sum()), int(mined.sum()), name))
    if dxf is not None:
        write_dxf(dxf, lines)
    return out


def edge_blocks(grid: Grid, mined: Mask) -> int:
    """Mined blocks on the lateral faces of the model.

    The optimiser cannot see slope constraints beyond the model, so a pit
    that reaches the side of the model is probably truncated: build a wider
    model (padding) before trusting it.
    """
    nx, ny, nz = grid.count
    cube = np.asarray(mined, bool).reshape(nz, ny, nx)
    side = np.zeros_like(cube)
    side[:, 0, :] = side[:, -1, :] = True
    side[:, :, 0] = side[:, :, -1] = True
    return int((cube & side).sum())
