"""Re-blocking: combine ``fx x fy x fz`` blocks into one larger block.

Used to move from an estimation grid to a selective mining unit (SMU) or a
coarser optimisation grid. Grades are tonnage-weighted means over the known
sub-blocks; density attributes are volume-weighted; other attributes take
the volume-weighted mean as well. A new block is empty when none of its
sub-blocks has a value. The model extent must be divisible by the factors;
otherwise the remainder at the high end is padded with empty blocks.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from minecore.blockmodel.model import BlockModel, BlockModelError, Grid
from minecore.project import Project

Floats = npt.NDArray[np.float64]


def _group_mean(values: Floats, weights: Floats, cell: npt.NDArray[np.int64], n: int) -> Floats:
    ok = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    num = np.bincount(cell[ok], weights[ok] * values[ok], minlength=n)
    den = np.bincount(cell[ok], weights[ok], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def reblock(
    project: Project,
    model: BlockModel,
    name: str,
    factors: tuple[int, int, int],
    *,
    density: float | str = 2.7,
) -> BlockModel:
    if min(factors) < 1:
        raise BlockModelError("re-blocking factors must be >= 1")
    g = model.grid
    nx, ny, nz = g.count
    fx, fy, fz = factors
    count = (-(-nx // fx), -(-ny // fy), -(-nz // fz))
    grid = Grid(g.origin, (g.size[0] * fx, g.size[1] * fy, g.size[2] * fz), count, g.azimuth)
    index = np.arange(g.blocks)
    i, j, k = index % nx, (index // nx) % ny, index // (nx * ny)
    cell = (i // fx) + count[0] * ((j // fy) + count[1] * (k // fz))
    rho = model.values(density) if isinstance(density, str) else np.full(g.blocks, float(density))
    volume = np.ones(g.blocks)
    attrs = model.attributes
    out: dict[str, Floats] = {}
    for a in attrs:
        values = model.values(a)
        unit = attrs[a].get("unit")
        grade_like = unit is not None and unit not in ("t/m3", "g/cm3")
        out[a] = _group_mean(values, rho if grade_like else volume, cell, grid.blocks)
    new = BlockModel.create(project, name, grid)
    new.set_attributes(
        out,
        {a: {**attrs[a], "reblocked_from": model.name, "factors": list(factors)} for a in out},
        f"Re-block {model.name} by {factors}",
    )
    return new
