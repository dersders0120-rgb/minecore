"""Coordinates: CRS definitions (PROJ), local mine grids and the project's working system."""

from minecore._core import CoordinateError, GridFit, LocalGrid, fit_local_grid
from minecore.coords.control import (
    ControlPoints,
    fit_control_points,
    fit_report,
    read_control_points,
)
from minecore.coords.crs import CrsDefinition, CrsError, CrsKind, require_working_crs
from minecore.coords.transform import (
    CoordinateTransformer,
    GeographicBounds,
    OperationInfo,
    TransformationError,
    bounds_of_points,
    describe_operation,
)
from minecore.coords.working import (
    LocalGridSystem,
    ProjectedSystem,
    UnreferencedSystem,
    WorkingSystem,
    from_json,
    to_json,
)

__all__ = [
    "ControlPoints",
    "CoordinateError",
    "CoordinateTransformer",
    "CrsDefinition",
    "CrsError",
    "CrsKind",
    "GeographicBounds",
    "GridFit",
    "LocalGrid",
    "LocalGridSystem",
    "OperationInfo",
    "ProjectedSystem",
    "TransformationError",
    "UnreferencedSystem",
    "WorkingSystem",
    "bounds_of_points",
    "describe_operation",
    "fit_control_points",
    "fit_local_grid",
    "fit_report",
    "from_json",
    "read_control_points",
    "require_working_crs",
    "to_json",
]
