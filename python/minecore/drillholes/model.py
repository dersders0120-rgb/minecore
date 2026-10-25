"""Drillhole databases: tables, fields and the manifest (ADR-0008 §1-4).

A :class:`DrillholeDatabase` is one ``drillhole_db`` catalogue object. Its
metadata is the manifest naming each table's immutable Parquet file, so
putting, replacing or removing a table is one undoable operation.

This module enforces the *structural* contract: required columns, types,
field declarations and assay flags. Whether the values make sense (duplicate
collars, overlaps, impossible dips) is the job of :mod:`.validate`, which
reports instead of refusing.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from minecore._core import MineCoreError, QuantityKind, Unit
from minecore.project import ObjectRecord, Project, bulk
from minecore.project.migrations import utc_now

KIND = "drillhole_db"
MANIFEST_FORMAT = 1
SRC_LINE = "_src_line"
FLAG_SUFFIX = "__flag"


class DrillholeError(MineCoreError):
    """A drillhole table or database violates the data contract."""


class Role(StrEnum):
    COLLAR = "collar"
    SURVEY = "survey"
    INTERVAL = "interval"
    POINT = "point"


class FieldType(StrEnum):
    NUMERIC = "numeric"
    CATEGORY = "category"
    TEXT = "text"


class Flag(IntEnum):
    """Meaning of a numeric value (the ``<field>__flag`` column)."""

    MEASURED = 0
    BELOW_DETECTION = 1
    ABOVE_LIMIT = 2
    MISSING = 3
    NOT_SAMPLED = 4
    INSUFFICIENT = 5


_FLAGS_WITH_LIMIT = (Flag.BELOW_DETECTION, Flag.ABOVE_LIMIT)

# Required and optional key columns per role; all float64 except hole_id.
_KEYS: dict[Role, tuple[tuple[str, ...], tuple[str, ...]]] = {
    Role.COLLAR: (("hole_id", "x", "y", "z"), ("max_depth", "azimuth", "dip")),
    Role.SURVEY: (("hole_id", "depth", "azimuth", "dip"), ("azimuth_correction",)),
    Role.INTERVAL: (("hole_id", "from", "to"), ()),
    Role.POINT: (("hole_id", "depth"), ()),
}


def key_columns(role: Role) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Required and optional key columns of a table role."""
    return _KEYS[role]


RESERVED = frozenset(c for req, opt in _KEYS.values() for c in (*req, *opt)) | {SRC_LINE}


@dataclass(frozen=True)
class FieldSpec:
    """An attribute column. ``unit`` and ``quantity`` apply to numeric fields."""

    name: str
    type: FieldType
    unit: str | None = None
    quantity: str | None = None
    source_column: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or self.name != self.name.strip():
            raise DrillholeError(f"invalid field name {self.name!r}")
        if self.name.casefold() in {r.casefold() for r in RESERVED} or self.name.endswith(
            FLAG_SUFFIX
        ):
            raise DrillholeError(f"field name {self.name!r} is reserved")
        if self.type is FieldType.NUMERIC:
            if self.unit is not None:
                unit = Unit(self.unit)
                if self.quantity is not None:
                    QuantityKind.from_name(self.quantity).check(unit)
            elif self.quantity is not None:
                raise DrillholeError(f"field {self.name!r}: a quantity needs a unit")
        elif self.unit is not None or self.quantity is not None:
            raise DrillholeError(f"field {self.name!r}: only numeric fields have units")

    @property
    def flag_column(self) -> str:
        return self.name + FLAG_SUFFIX

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name, "type": self.type.value}
        for key in ("unit", "quantity", "source_column"):
            if getattr(self, key) is not None:
                data[key] = getattr(self, key)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FieldSpec:
        return cls(
            data["name"],
            FieldType(data["type"]),
            data.get("unit"),
            data.get("quantity"),
            data.get("source_column"),
        )


@dataclass(frozen=True)
class TableInfo:
    name: str
    role: Role
    rows: int
    fields: tuple[FieldSpec, ...]
    file: bulk.FileRef
    source: str | None
    import_issues: tuple[dict[str, Any], ...] = ()


def _column(table: pa.Table, name: str, kind: pa.DataType, what: str) -> pa.ChunkedArray:
    column = table.column(name)
    if column.type == kind:
        return column
    if kind == pa.float64() and (
        pa.types.is_integer(column.type) or pa.types.is_floating(column.type)
    ):
        return column.cast(pa.float64())
    if kind == pa.string() and (
        pa.types.is_large_string(column.type) or pa.types.is_dictionary(column.type)
    ):
        return column.cast(pa.string())
    if kind == pa.int64() and pa.types.is_integer(column.type):
        return column.cast(pa.int64())
    raise DrillholeError(f"{what} must be {kind}, not {column.type}")


def _flags(spec: FieldSpec, values: pa.ChunkedArray, flags: pa.ChunkedArray | None) -> pa.Array:
    v = values.to_numpy(zero_copy_only=False)
    finite = np.isfinite(v)
    if flags is None:
        return pa.array(np.where(finite, Flag.MEASURED, Flag.MISSING).astype(np.uint8))
    if flags.null_count:
        raise DrillholeError(f"{spec.flag_column} must not contain nulls")
    f = flags.to_numpy(zero_copy_only=False).astype(np.int64)
    if f.size and (f.min() < 0 or f.max() > max(Flag)):
        raise DrillholeError(f"{spec.flag_column} holds an unknown flag")
    limited = np.isin(f, [int(x) for x in _FLAGS_WITH_LIMIT])
    bad = np.flatnonzero(
        ((f == Flag.MEASURED) & ~finite)
        | (limited & ~(finite & (v > 0)))
        | ((f >= Flag.MISSING) & ~np.isnan(v))
    )
    if bad.size:
        raise DrillholeError(
            f"field {spec.name!r}: value and flag disagree at row {int(bad[0])} "
            "(measured needs a number, a limit flag needs the positive limit, "
            "missing flags need NaN)"
        )
    return pa.array(f.astype(np.uint8))


def conform(table: pa.Table, role: Role, fields: tuple[FieldSpec, ...]) -> pa.Table:
    """Return ``table`` in canonical column order and types, or raise DrillholeError."""
    required, optional = _KEYS[role]
    names = set(table.column_names)
    missing = [c for c in required if c not in names]
    if missing:
        raise DrillholeError(f"{role} table lacks required columns: {', '.join(missing)}")
    seen: set[str] = set()
    for spec in fields:
        if spec.name.casefold() in seen:
            raise DrillholeError(f"duplicate field name {spec.name!r} (names ignore case)")
        seen.add(spec.name.casefold())
        if spec.name not in names:
            raise DrillholeError(f"declared field {spec.name!r} has no column")
    expected = {*required, *optional, SRC_LINE}
    expected |= {s.name for s in fields} | {s.flag_column for s in fields if s.type == "numeric"}
    unknown = sorted(names - expected)
    if unknown:
        raise DrillholeError(f"undeclared columns: {', '.join(unknown)}")

    columns: dict[str, pa.ChunkedArray | pa.Array] = {}
    for name in (*required, *optional):
        if name in names:
            kind = pa.string() if name == "hole_id" else pa.float64()
            columns[name] = _column(table, name, kind, f"column {name!r}")
    if SRC_LINE in names:
        columns[SRC_LINE] = _column(table, SRC_LINE, pa.int64(), SRC_LINE)
    else:
        columns[SRC_LINE] = pa.nulls(table.num_rows, pa.int64())
    for spec in fields:
        if spec.type is FieldType.NUMERIC:
            values = _column(table, spec.name, pa.float64(), f"field {spec.name!r}")
            # Arrow nulls become NaN: missing is expressed by the flag, never by null.
            values = pc.fill_null(values, np.nan)
            flags = table.column(spec.flag_column) if spec.flag_column in names else None
            columns[spec.name] = values
            columns[spec.flag_column] = _flags(spec, values, flags)
        else:
            text = _column(table, spec.name, pa.string(), f"field {spec.name!r}")
            columns[spec.name] = (
                text.dictionary_encode() if spec.type is FieldType.CATEGORY else text
            )
    return pa.table(columns)


class DrillholeDatabase:
    """A drillhole database inside an open project."""

    def __init__(self, project: Project, object_id: str) -> None:
        self._project = project
        self._id = object_id
        if self._record().kind != KIND:
            raise DrillholeError(f"object {object_id} is not a drillhole database")

    @classmethod
    def create(cls, project: Project, name: str) -> DrillholeDatabase:
        manifest = {"format": MANIFEST_FORMAT, "tables": {}, "sources": {}, "conventions": {}}
        record = project.register_object(KIND, name, metadata=manifest)
        return cls(project, record.object_id)

    @classmethod
    def open(cls, project: Project, name: str) -> DrillholeDatabase:
        for record in project.objects(KIND):
            if record.name == name:
                return cls(project, record.object_id)
        raise DrillholeError(f"no drillhole database named {name!r}")

    def _record(self) -> ObjectRecord:
        return self._project.get_object(self._id)

    @property
    def object_id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._record().name

    @property
    def manifest(self) -> dict[str, Any]:
        manifest = self._record().metadata
        if manifest.get("format") != MANIFEST_FORMAT:
            raise DrillholeError(f"unsupported drillhole manifest format {manifest.get('format')}")
        return manifest

    @property
    def conventions(self) -> dict[str, Any]:
        return dict(self.manifest["conventions"])

    def tables(self) -> dict[str, TableInfo]:
        manifest = self.manifest
        return {name: _info(name, entry) for name, entry in manifest["tables"].items()}

    def table_info(self, name: str) -> TableInfo:
        try:
            return self.tables()[name]
        except KeyError:
            raise DrillholeError(f"database {self.name!r} has no table {name!r}") from None

    def table(self, name: str) -> pa.Table:
        return bulk.read_table(self._project.path, self.table_info(name).file)

    def tables_with_role(self, role: Role) -> list[TableInfo]:
        return [t for t in self.tables().values() if t.role is role]

    def put_table(  # noqa: PLR0913 - one call states a whole table import
        self,
        name: str,
        role: Role,
        table: pa.Table,
        fields: tuple[FieldSpec, ...] = (),
        *,
        source: Path | None = None,
        source_info: dict[str, Any] | None = None,
        conventions: dict[str, Any] | None = None,
        import_issues: list[dict[str, Any]] | None = None,
    ) -> TableInfo:
        """Add or replace table ``name`` in one undoable operation.

        ``source`` is archived in the package; ``source_info`` (dialect, column
        mapping, ...) is recorded with it. ``conventions`` are merged into the
        database conventions (dip sign, azimuth reference, ...).
        """
        if not name or not name.strip() or name != name.strip():
            raise DrillholeError(f"invalid table name {name!r}")
        manifest = copy.deepcopy(self.manifest)
        others = {n.casefold() for n in manifest["tables"] if n != name}
        if name.casefold() in others:
            raise DrillholeError(f"table name {name!r} differs from another only by case")
        current = manifest["tables"].get(name)
        if role in (Role.COLLAR, Role.SURVEY):
            clash = [n for n, e in manifest["tables"].items() if e["role"] == role and n != name]
            if clash:
                raise DrillholeError(f"database already has a {role} table: {clash[0]!r}")
        canonical = conform(table, role, fields)

        package = self._project.path
        source_id: str | None = None
        if source is not None:
            ref = bulk.archive_source(package, source)
            source_id = ref.sha256[:16]
            manifest["sources"][source_id] = {
                **ref.to_json(),
                "name": source.name,
                "imported_utc": utc_now(),
                **(source_info or {}),
            }
        ref = bulk.write_table(package, self._id, role.value, canonical, {"role": role.value})
        manifest["tables"][name] = {
            "role": role.value,
            "rows": canonical.num_rows,
            "fields": [f.to_json() for f in fields],
            "source": source_id,
            "import_issues": import_issues or [],
            **ref.to_json(),
        }
        manifest["conventions"].update(conventions or {})
        verb = "Replace" if current is not None else "Add"
        self._project.update_object(
            self._id,
            metadata=manifest,
            description=f"{verb} {role} table {name!r} in {self.name!r} "
            f"({canonical.num_rows} rows)",
        )
        return self.table_info(name)

    def remove_table(self, name: str) -> None:
        manifest = copy.deepcopy(self.manifest)
        if manifest["tables"].pop(name, None) is None:
            raise DrillholeError(f"database {self.name!r} has no table {name!r}")
        self._project.update_object(
            self._id, metadata=manifest, description=f"Remove table {name!r} from {self.name!r}"
        )


def _info(name: str, entry: dict[str, Any]) -> TableInfo:
    return TableInfo(
        name=name,
        role=Role(entry["role"]),
        rows=int(entry["rows"]),
        fields=tuple(FieldSpec.from_json(f) for f in entry["fields"]),
        file=bulk.FileRef.from_json(entry),
        source=entry.get("source"),
        import_issues=tuple(entry.get("import_issues", [])),
    )
