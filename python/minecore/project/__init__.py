"""Project persistence: the ``.mcproj`` package, its schema and migrations."""

from minecore.project.errors import (
    MigrationError,
    ProjectError,
    ProjectFormatError,
    ProjectLockedError,
    ProjectVersionError,
    ReadOnlyProjectError,
)
from minecore.project.history import OperationRecord
from minecore.project.package import (
    APPLICATION_ID,
    PACKAGE_SUFFIX,
    ObjectRecord,
    Project,
    ProjectInfo,
)

__all__ = [
    "APPLICATION_ID",
    "PACKAGE_SUFFIX",
    "MigrationError",
    "ObjectRecord",
    "OperationRecord",
    "Project",
    "ProjectError",
    "ProjectFormatError",
    "ProjectInfo",
    "ProjectLockedError",
    "ProjectVersionError",
    "ReadOnlyProjectError",
]
