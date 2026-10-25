"""Drillhole data: databases, tables and validation (ADR-0008)."""

from minecore.drillholes.issues import CATALOGUE, Effect, Issue, IssueType, Severity
from minecore.drillholes.model import (
    DrillholeDatabase,
    DrillholeError,
    FieldSpec,
    FieldType,
    Flag,
    Role,
    TableInfo,
)
from minecore.drillholes.validate import ValidationReport, ValidationSettings, validate

__all__ = [
    "CATALOGUE",
    "DrillholeDatabase",
    "DrillholeError",
    "Effect",
    "FieldSpec",
    "FieldType",
    "Flag",
    "Issue",
    "IssueType",
    "Role",
    "Severity",
    "TableInfo",
    "ValidationReport",
    "ValidationSettings",
    "validate",
]
