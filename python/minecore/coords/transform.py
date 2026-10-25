"""Transformations between an external CRS and the project's working system.

Policy (ADR-0005):

- PROJ chooses the operation for the data's area, and exactly one operation is
  used for the whole dataset; its name, accuracy and PROJ pipeline are
  reported so the result can be reproduced and audited.
- Ballpark operations (datum shifts that ignore the datum difference, errors
  of up to hundreds of metres) are never used.
- If PROJ's best operation needs a grid that is not installed, the
  transformation is refused and the grid is named; MineCore does not fall back
  to a less accurate operation on its own.
- Nothing is transformed into or out of an unreferenced system.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pyproj
from pyproj.aoi import AreaOfInterest
from pyproj.exceptions import ProjError
from pyproj.transformer import TransformerGroup

from minecore._core import LocalGrid
from minecore.coords.crs import CrsDefinition, CrsError
from minecore.coords.working import (
    LocalGridSystem,
    ProjectedSystem,
    UnreferencedSystem,
    WorkingSystem,
)

FloatArray = npt.NDArray[np.float64]
ArrayLike = npt.ArrayLike
Coordinates = tuple[FloatArray, FloatArray, FloatArray | None]


class TransformationError(CrsError):
    """No acceptable transformation exists, or a point could not be transformed."""


@dataclass(frozen=True)
class GeographicBounds:
    """Area of interest in degrees of longitude and latitude."""

    west: float
    south: float
    east: float
    north: float


@dataclass(frozen=True)
class OperationInfo:
    """The PROJ operation used by a transformer."""

    description: str
    accuracy_m: float | None
    exact: bool
    pipeline: str


IDENTITY = OperationInfo("identity (same CRS)", 0.0, exact=True, pipeline="")


def _as_float(values: ArrayLike, name: str) -> FloatArray:
    array = np.array(values, dtype=np.float64, copy=True).reshape(-1)
    if not np.all(np.isfinite(array)):
        raise TransformationError(f"{name} contains NaN or infinite values")
    return array


def bounds_of_points(
    crs: CrsDefinition, x: ArrayLike, y: ArrayLike, *, margin_deg: float = 0.01
) -> GeographicBounds:
    """Geographic bounding box of points given in ``crs``.

    Uses a conversion to the CRS's own geodetic system, which involves no datum
    change, so the box is exact up to the margin.
    """
    xs, ys = _as_float(x, "x"), _as_float(y, "y")
    if xs.size == 0 or xs.size != ys.size:
        raise TransformationError("need one or more points with matching x and y")
    source = crs.to_pyproj()
    geodetic = source.geodetic_crs
    if geodetic is None:
        raise TransformationError(f"{crs.describe()} has no geodetic CRS")
    to_geodetic = pyproj.Transformer.from_crs(source, geodetic, always_xy=True)
    lon, lat = to_geodetic.transform(xs, ys, errcheck=True)
    return GeographicBounds(
        west=max(-180.0, float(np.min(lon)) - margin_deg),
        south=max(-90.0, float(np.min(lat)) - margin_deg),
        east=min(180.0, float(np.max(lon)) + margin_deg),
        north=min(90.0, float(np.max(lat)) + margin_deg),
    )


def _info(transformer: pyproj.Transformer) -> OperationInfo:
    operations = transformer.operations or ()
    exact = bool(operations) and all(op.type_name == "Conversion" for op in operations)
    accuracy = transformer.accuracy
    return OperationInfo(
        description=transformer.description,
        accuracy_m=0.0 if exact else (accuracy if accuracy >= 0 else None),
        exact=exact,
        pipeline=transformer.definition,
    )


def _no_operation(source: CrsDefinition, target: CrsDefinition) -> TransformationError:
    return TransformationError(
        f"no transformation from {source.describe()} to {target.describe()} is known for the "
        "area of the data without ballpark approximation (a datum shift that ignores the "
        "datum difference, with errors up to hundreds of metres); check both datums"
    )


def _select(
    source: CrsDefinition, target: CrsDefinition, area: GeographicBounds | None
) -> pyproj.Transformer:
    aoi = None if area is None else AreaOfInterest(area.west, area.south, area.east, area.north)
    try:
        with warnings.catch_warnings():
            # pyproj warns about missing grids; we raise our own error instead.
            warnings.simplefilter("ignore", UserWarning)
            group = TransformerGroup(
                source.to_pyproj(),
                target.to_pyproj(),
                always_xy=True,
                allow_ballpark=False,
                # pyproj's stubs name a private copy of the same AreaOfInterest class.
                area_of_interest=aoi,  # type: ignore[arg-type]
            )
    except ProjError:
        raise _no_operation(source, target) from None
    if not group.best_available:
        missing = sorted(
            {grid.short_name for op in group.unavailable_operations for grid in op.grids}
        )
        raise TransformationError(
            f"the most accurate transformation from {source.describe()} to "
            f"{target.describe()} needs grid files that are not installed: "
            f"{', '.join(missing) or 'unknown'}. Install them in the PROJ data directory "
            f"({pyproj.datadir.get_data_dir()}); MineCore does not download grids"
        )
    if not group.transformers:
        raise _no_operation(source, target)
    return group.transformers[0]


class CoordinateTransformer:
    """Transforms coordinates between an external CRS and a working system.

    ``forward`` maps external coordinates into the working system (import);
    ``inverse`` maps working coordinates back to the external CRS (export).
    For a geographic external CRS, x is longitude and y is latitude, in degrees.
    """

    def __init__(
        self,
        external: CrsDefinition,
        working: WorkingSystem,
        *,
        area: GeographicBounds | None = None,
    ) -> None:
        match working:
            case UnreferencedSystem():
                raise TransformationError(
                    "the project coordinate system is unreferenced; coordinates cannot be "
                    "transformed into or out of it"
                )
            case ProjectedSystem(crs=base):
                self._grid: LocalGrid | None = None
            case LocalGridSystem(base=base, grid=grid):
                self._grid = grid
        self.external = external
        self.working = working
        if external.same_as(base):
            self._proj: pyproj.Transformer | None = None
            self.operation = IDENTITY
        else:
            self._proj = _select(external, base, area)
            self.operation = _info(self._proj)

    @classmethod
    def for_points(
        cls, external: CrsDefinition, working: WorkingSystem, x: ArrayLike, y: ArrayLike
    ) -> CoordinateTransformer:
        """Transformer chosen for the area covered by points given in ``external``."""
        return cls(external, working, area=bounds_of_points(external, x, y))

    def forward(self, x: ArrayLike, y: ArrayLike, z: ArrayLike | None = None) -> Coordinates:
        """External CRS to working system. Returns new float64 arrays."""
        xs, ys, zs = self._inputs(x, y, z)
        if self._proj is not None:
            xs, ys, zs = self._run_proj(self._proj, xs, ys, zs, forward=True)
        if self._grid is not None:
            return self._grid.to_local(xs, ys, zs)
        return xs, ys, zs

    def inverse(self, x: ArrayLike, y: ArrayLike, z: ArrayLike | None = None) -> Coordinates:
        """Working system to external CRS. Returns new float64 arrays."""
        xs, ys, zs = self._inputs(x, y, z)
        if self._grid is not None:
            xs, ys, zs = self._grid.to_base(xs, ys, zs)
        if self._proj is not None:
            return self._run_proj(self._proj, xs, ys, zs, forward=False)
        return xs, ys, zs

    @staticmethod
    def _inputs(x: ArrayLike, y: ArrayLike, z: ArrayLike | None) -> Coordinates:
        xs, ys = _as_float(x, "x"), _as_float(y, "y")
        zs = None if z is None else _as_float(z, "z")
        if xs.size != ys.size or (zs is not None and zs.size != xs.size):
            raise TransformationError("x, y and z must have the same length")
        return xs, ys, zs

    def _run_proj(
        self,
        proj: pyproj.Transformer,
        xs: FloatArray,
        ys: FloatArray,
        zs: FloatArray | None,
        *,
        forward: bool,
    ) -> Coordinates:
        direction = "FORWARD" if forward else "INVERSE"
        try:
            if zs is None:
                ox, oy = proj.transform(xs, ys, direction=direction, errcheck=True)
                return np.asarray(ox, dtype=np.float64), np.asarray(oy, dtype=np.float64), None
            ox, oy, oz = proj.transform(xs, ys, zs, direction=direction, errcheck=True)
        except ProjError as exc:
            raise TransformationError(f"{self.operation.description}: {exc}") from None
        return (
            np.asarray(ox, dtype=np.float64),
            np.asarray(oy, dtype=np.float64),
            np.asarray(oz, dtype=np.float64),
        )


def describe_operation(info: OperationInfo) -> Sequence[str]:
    """Human-readable lines for reports and the CLI."""
    if info.exact:
        accuracy = "exact (conversion only)"
    elif info.accuracy_m is None:
        accuracy = "unknown"
    else:
        accuracy = f"{info.accuracy_m:g} m (published)"
    return [f"operation : {info.description}", f"accuracy  : {accuracy}"]
