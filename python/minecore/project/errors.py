"""Errors raised by project persistence."""

from __future__ import annotations

from minecore._core import MineCoreError


class ProjectError(MineCoreError):
    """Base class for project package errors."""


class ProjectFormatError(ProjectError):
    """The path is not a MineCore project, or the project is damaged."""


class ProjectVersionError(ProjectError):
    """The project format version cannot be opened by this MineCore version."""


class ProjectLockedError(ProjectError):
    """Another process has the project open for writing."""


class MigrationError(ProjectError):
    """A schema migration failed; the project was left at its previous version."""


class ReadOnlyProjectError(ProjectError):
    """A write was attempted on a project opened read-only."""
