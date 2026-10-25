"""Immutable bulk files inside a project package (ADR-0008 §4).

Bulk data (Parquet tables, archived source files) lives in files next to
``project.sqlite``. The catalogue refers to them by package-relative POSIX
path, inside the operation that changes the catalogue, so undo and redo only
move references. Files are therefore written once and never modified:

* :func:`write_table` and :func:`archive_source` write to a hidden temporary
  name, fsync, and rename into place; a reader never sees a partial file.
* :func:`referenced_files` lists every file that the catalogue or any
  operation in the history can still point to; :func:`collect_garbage`
  removes the rest.

References are found by walking object metadata for string values under the
key ``"file"`` (and the ``storage_path`` column), in the current catalogue and
in every before/after image of the operation log.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from minecore.project.errors import ProjectError

TABLE_FORMAT = 1
BULK_DIRS = ("tables", "sources")
_TEMP_PREFIX = ".tmp-"
_STEM = re.compile(r"^[a-z][a-z0-9_]*$")
_CHUNK = 1 << 20


@dataclass(frozen=True)
class FileRef:
    """A complete bulk file: package-relative path, size and SHA-256."""

    file: str
    size: int
    sha256: str

    def to_json(self) -> dict[str, Any]:
        return {"file": self.file, "size": self.size, "sha256": self.sha256}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FileRef:
        return cls(str(data["file"]), int(data["size"]), str(data["sha256"]))


def resolve(package: Path, file: str) -> Path:
    """Return the absolute path of a bulk file, refusing paths that escape it."""
    rel = PurePosixPath(file)
    if (
        rel.is_absolute()
        or ".." in rel.parts
        or not rel.parts
        or rel.parts[0] not in BULK_DIRS
        or "\\" in file
    ):
        raise ProjectError(f"invalid bulk file path {file!r}")
    return package.joinpath(*rel.parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(directory: Path) -> None:
    # Directories cannot be opened for fsync on Windows; NTFS journals renames.
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _atomic(target: Path) -> Iterator[Path]:
    """Yield a temporary path; on success fsync it and rename it to ``target``."""
    if target.exists():
        raise ProjectError(f"bulk file already exists and is immutable: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{_TEMP_PREFIX}{uuid.uuid4().hex}-{target.name}")
    try:
        yield temp
        with temp.open("rb+") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        temp.replace(target)
        _fsync_dir(target.parent)
    finally:
        temp.unlink(missing_ok=True)


def write_table(
    package: Path,
    directory: str,
    stem: str,
    table: pa.Table,
    metadata: dict[str, str] | None = None,
) -> FileRef:
    """Write ``table`` as a new Parquet file ``tables/<directory>/<stem>-<id>.parquet``."""
    if not _STEM.match(stem):
        raise ProjectError(f"invalid table file stem {stem!r}")
    file = f"tables/{directory}/{stem}-{uuid.uuid4().hex[:12]}.parquet"
    target = resolve(package, file)
    meta = {**(table.schema.metadata or {})}
    meta[b"minecore.table_format"] = str(TABLE_FORMAT).encode()
    for key, value in (metadata or {}).items():
        meta[f"minecore.{key}".encode()] = value.encode()
    with _atomic(target) as temp:
        pq.write_table(table.replace_schema_metadata(meta), temp, compression="zstd")
    return FileRef(file, target.stat().st_size, _sha256(target))


def read_table(package: Path, ref: FileRef) -> pa.Table:
    """Read a Parquet bulk file, checking that it is complete."""
    path = resolve(package, ref.file)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ProjectError(f"bulk file is missing: {ref.file}") from None
    if size != ref.size:
        raise ProjectError(f"bulk file {ref.file} has {size} bytes, expected {ref.size}")
    table = pq.read_table(path)
    version = (table.schema.metadata or {}).get(b"minecore.table_format")
    if version != str(TABLE_FORMAT).encode():
        raise ProjectError(f"bulk file {ref.file} has unsupported table format {version!r}")
    return table


def archive_source(package: Path, source: Path) -> FileRef:
    """Copy a source file into ``sources/<sha256><suffix>`` once and return its reference."""
    sha = _sha256(source)
    suffix = source.suffix.lower()
    if not re.fullmatch(r"(\.[a-z0-9]{1,8})?", suffix):
        suffix = ""
    file = f"sources/{sha}{suffix}"
    target = resolve(package, file)
    if not target.exists():
        with _atomic(target) as temp:
            digest = hashlib.sha256()
            with source.open("rb") as src, temp.open("wb") as dst:
                while chunk := src.read(_CHUNK):
                    digest.update(chunk)
                    dst.write(chunk)
            if digest.hexdigest() != sha:
                raise ProjectError(f"source file changed while it was archived: {source}")
    return FileRef(file, target.stat().st_size, sha)


def verify(package: Path, ref: FileRef) -> str | None:
    """Return a problem description, or ``None`` when the file matches its reference."""
    path = resolve(package, ref.file)
    if not path.is_file():
        return f"missing bulk file {ref.file}"
    if path.stat().st_size != ref.size:
        return f"bulk file {ref.file} has the wrong size"
    if _sha256(path) != ref.sha256:
        return f"bulk file {ref.file} does not match its SHA-256"
    return None


def file_refs(value: Any) -> Iterator[dict[str, Any]]:
    """Yield every dict inside ``value`` that has a string ``"file"`` entry."""
    if isinstance(value, dict):
        if isinstance(value.get("file"), str):
            yield value
        for item in value.values():
            yield from file_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from file_refs(item)


def _files_in_row(row: dict[str, Any]) -> Iterator[str]:
    if isinstance(row.get("storage_path"), str):
        yield row["storage_path"]
    raw = row.get("metadata_json")
    if isinstance(raw, str):
        for ref in file_refs(json.loads(raw)):
            yield ref["file"]


def referenced_files(conn: sqlite3.Connection) -> set[str]:
    """Every bulk path that the catalogue or the operation log references."""
    rows: list[dict[str, Any]] = [
        {"storage_path": path, "metadata_json": meta}
        for path, meta in conn.execute("SELECT storage_path, metadata_json FROM objects")
    ]
    has_log = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'operation_changes'"
    ).fetchone()
    images: Iterable[tuple[str | None, str | None]] = (
        conn.execute(
            "SELECT old_json, new_json FROM operation_changes WHERE table_name = 'objects'"
        )
        if has_log
        else ()
    )
    for old, new in images:
        rows.extend(json.loads(image) for image in (old, new) if image is not None)
    return {path for row in rows for path in _files_in_row(row)}


def collect_garbage(package: Path, conn: sqlite3.Connection) -> list[str]:
    """Delete unreferenced bulk files and stale temporaries; return what was removed.

    The caller must hold the project's write lock, so no other process is
    writing a temporary file at the same time.
    """
    keep = referenced_files(conn)
    removed: list[str] = []
    for top in BULK_DIRS:
        root = package / top
        if not root.is_dir():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(package).as_posix()
            if rel not in keep or path.name.startswith(_TEMP_PREFIX):
                path.unlink()
                removed.append(rel)
        for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
            with contextlib.suppress(OSError):
                directory.rmdir()
    return removed
