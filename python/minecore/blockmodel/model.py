"""Regular block models: grid definition, storage and grade-tonnage reporting.

A block model is one ``block_model`` catalogue object. Its metadata holds the
grid and the attribute list; values live in one immutable Parquet file with
one row per block, ordered with ``i`` fastest, then ``j``, then ``k``
(``index = i + nx * (j + ny * k)``). Changing attributes writes a new file in
one undoable operation, as for drillhole tables (ADR-0008 §4).

Grid: ``origin`` is the minimum corner (x, y, z); block ``(i, j, k)`` has its
centroid at ``origin + (i + 0.5, j + 0.5, k + 0.5) * size``. Rotated and
sub-celled models come with M4.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa

from minecore._core import MineCoreError, Unit
from minecore.project import Project, bulk

KIND = "block_model"
FORMAT = 1
Floats = npt.NDArray[np.float64]


class BlockModelError(MineCoreError):
    """Invalid block model definition or attribute."""


@dataclass(frozen=True)
class Grid:
    origin: tuple[float, float, float]
    size: tuple[float, float, float]
    count: tuple[int, int, int]

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (*self.origin, *self.size)):
            raise BlockModelError("grid origin and size must be finite")
        if not all(s > 0 for s in self.size):
            raise BlockModelError("block size must be > 0")
        if not all(int(c) == c and c > 0 for c in self.count):
            raise BlockModelError("block counts must be positive integers")
        if self.blocks > 50_000_000:
            raise BlockModelError(f"{self.blocks} blocks exceed the regular model limit of 50 M")

    @property
    def blocks(self) -> int:
        return self.count[0] * self.count[1] * self.count[2]

    @property
    def block_volume(self) -> float:
        return self.size[0] * self.size[1] * self.size[2]

    @classmethod
    def covering(
        cls, points: Floats, size: tuple[float, float, float], padding: float = 0.0
    ) -> Grid:
        """Smallest grid aligned to whole multiples of ``size`` covering ``points`` (+ padding)."""
        pts = np.asarray(points, np.float64)
        pts = pts[np.isfinite(pts).all(axis=1)]
        if len(pts) == 0:
            raise BlockModelError("no points to cover")
        s = np.asarray(size, np.float64)
        lo = np.floor((pts.min(axis=0) - padding) / s) * s
        hi = np.ceil((pts.max(axis=0) + padding) / s) * s
        count = np.maximum(np.round((hi - lo) / s).astype(int), 1)
        return cls(
            (float(lo[0]), float(lo[1]), float(lo[2])),
            (float(s[0]), float(s[1]), float(s[2])),
            (int(count[0]), int(count[1]), int(count[2])),
        )

    def centroids(self) -> Floats:
        nx, ny, nz = self.count
        k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
        ijk = np.column_stack([i.ravel(), j.ravel(), k.ravel()]).astype(np.float64)
        result: Floats = np.asarray(self.origin) + (ijk + 0.5) * np.asarray(self.size)
        return result

    def to_json(self) -> dict[str, Any]:
        return {"origin": list(self.origin), "size": list(self.size), "count": list(self.count)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Grid:
        o, s, c = data["origin"], data["size"], data["count"]
        return cls((o[0], o[1], o[2]), (s[0], s[1], s[2]), (int(c[0]), int(c[1]), int(c[2])))


@dataclass(frozen=True)
class GradeTonnageRow:
    cutoff: float
    blocks: int
    volume: float
    tonnes: float
    grade: float
    metal: float


@dataclass(frozen=True)
class GradeTonnage:
    attribute: str
    grade_unit: str | None
    density: float | str
    metal_unit: str
    rows: tuple[GradeTonnageRow, ...]
    unestimated_blocks: int


class BlockModel:
    def __init__(self, project: Project, object_id: str) -> None:
        self._project = project
        self._id = object_id
        if project.get_object(object_id).kind != KIND:
            raise BlockModelError(f"object {object_id} is not a block model")

    @classmethod
    def create(cls, project: Project, name: str, grid: Grid) -> BlockModel:
        table = pa.table({"_block": pa.array(np.arange(grid.blocks, dtype=np.int64))})
        record = project.register_object(KIND, name, metadata={"format": FORMAT})
        ref = bulk.write_table(project.path, record.object_id, "blocks", table)
        meta = {"format": FORMAT, "grid": grid.to_json(), "attributes": {}, **ref.to_json()}
        project.update_object(
            record.object_id, metadata=meta, description=f"Define grid of {name!r}"
        )
        return cls(project, record.object_id)

    @classmethod
    def open(cls, project: Project, name: str) -> BlockModel:
        for record in project.objects(KIND):
            if record.name == name:
                return cls(project, record.object_id)
        raise BlockModelError(f"no block model named {name!r}")

    @property
    def name(self) -> str:
        return self._project.get_object(self._id).name

    @property
    def metadata(self) -> dict[str, Any]:
        return self._project.get_object(self._id).metadata

    @property
    def grid(self) -> Grid:
        return Grid.from_json(self.metadata["grid"])

    @property
    def attributes(self) -> dict[str, dict[str, Any]]:
        return dict(self.metadata["attributes"])

    def table(self) -> pa.Table:
        return bulk.read_table(self._project.path, bulk.FileRef.from_json(self.metadata))

    def values(self, name: str) -> Floats:
        if name not in self.attributes:
            raise BlockModelError(f"block model {self.name!r} has no attribute {name!r}")
        return np.asarray(self.table().column(name).to_numpy(zero_copy_only=False), np.float64)

    def set_attributes(
        self, values: dict[str, Floats], info: dict[str, dict[str, Any]], description: str
    ) -> None:
        """Add or replace numeric attributes in one undoable operation."""
        grid = self.grid
        table = self.table()
        meta = copy.deepcopy(self.metadata)
        for name, array in values.items():
            arr = np.asarray(array, np.float64)
            if arr.shape != (grid.blocks,):
                raise BlockModelError(f"attribute {name!r} needs {grid.blocks} values")
            if name.startswith("_"):
                raise BlockModelError(f"attribute names starting with '_' are reserved: {name!r}")
            unit = info.get(name, {}).get("unit")
            if unit is not None:
                Unit(unit)
            column = pa.array(arr)
            if name in table.column_names:
                table = table.set_column(table.column_names.index(name), name, column)
            else:
                table = table.append_column(name, column)
            meta["attributes"][name] = info.get(name, {})
        ref = bulk.write_table(self._project.path, self._id, "blocks", table)
        meta.update(ref.to_json())
        self._project.update_object(self._id, metadata=meta, description=description)

    def grade_tonnage(
        self,
        attribute: str,
        cutoffs: list[float],
        *,
        density: float | str,
        mask: npt.NDArray[np.bool_] | None = None,
    ) -> GradeTonnage:
        """Tonnes, mean grade and contained metal above each cut-off (grade >= cut-off).

        ``density`` is a constant in t/m3 or the name of a density attribute
        (t/m3). Blocks without an estimate are excluded and counted. ``mask``
        restricts the report to selected blocks (domain, category, pit).
        """
        grid = self.grid
        grade = self.values(attribute)
        if isinstance(density, str):
            rho = self.values(density)
        else:
            if not (math.isfinite(density) and density > 0):
                raise BlockModelError("density must be > 0 t/m3")
            rho = np.full(grid.blocks, float(density))
        unit = self.attributes[attribute].get("unit")
        known = np.isfinite(grade) & np.isfinite(rho)
        if mask is not None:
            selected = np.asarray(mask, bool)
            known &= selected
        else:
            selected = np.ones(grid.blocks, bool)
        # Contained metal: tonnes * grade converted to a mass fraction.
        if unit is None:
            fraction, metal_unit = 1.0, "t x grade"
        else:
            fraction = Unit(unit).si_factor
            metal_unit = "g" if unit in ("g/t", "ppm", "gpt") else "t"
        to_metal = fraction * (1e6 if metal_unit == "g" else 1.0)
        rows = []
        for c in sorted(cutoffs):
            sel = known & (grade >= c)
            tonnes_each = rho[sel] * grid.block_volume
            tonnes = float(tonnes_each.sum())
            mean = float((tonnes_each * grade[sel]).sum() / tonnes) if tonnes > 0 else math.nan
            metal = tonnes * mean * to_metal if tonnes > 0 else 0.0
            rows.append(
                GradeTonnageRow(
                    c, int(sel.sum()), int(sel.sum()) * grid.block_volume, tonnes, mean, metal
                )
            )
        return GradeTonnage(
            attribute,
            unit,
            density,
            metal_unit,
            tuple(rows),
            int(np.count_nonzero(selected & ~known)),
        )
