"""The MineCore error hierarchy (ADR-0007), in one place.

Every error MineCore raises on purpose derives from :class:`MineCoreError`;
anything else is a bug. Interfaces map them to exit codes (CLI: 1 for a
MineCoreError, 2 for usage errors, 130 for cancellation) or dialogs::

    MineCoreError
    ├── UnitError                 units and quantity kinds (Rust core)
    ├── CoordinateError           local grids, control points (Rust core)
    ├── CrsError                  CRS definitions and working systems
    │   └── TransformationError   no acceptable PROJ operation, failed points
    ├── ProjectError              project package
    │   ├── ProjectFormatError, ProjectVersionError, ProjectLockedError
    │   ├── MigrationError, ReadOnlyProjectError
    └── CancelledError            a job was cancelled; nothing was changed
"""

from minecore._core import CoordinateError, MineCoreError, UnitError
from minecore.coords import CrsError, TransformationError
from minecore.jobs import CancelledError
from minecore.project.errors import (
    MigrationError,
    ProjectError,
    ProjectFormatError,
    ProjectLockedError,
    ProjectVersionError,
    ReadOnlyProjectError,
)

__all__ = [
    "CancelledError",
    "CoordinateError",
    "CrsError",
    "MigrationError",
    "MineCoreError",
    "ProjectError",
    "ProjectFormatError",
    "ProjectLockedError",
    "ProjectVersionError",
    "ReadOnlyProjectError",
    "TransformationError",
    "UnitError",
]
