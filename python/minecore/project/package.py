"""The MineCore project package (``*.mcproj``).

A project is a directory::

    Example.mcproj/
        project.sqlite   metadata, object catalogue, settings, history
        project.lock     single-writer lock (held while open for writing)
        tables/          Parquet tables (drillhole data, composites, ...)
        sources/         archived copies of imported source files
        blocks/          chunked block model storage
        geometry/        meshes, strings and solids
        backups/         automatic copies of project.sqlite made before migrations

``project.sqlite`` is identified by SQLite's ``application_id`` and versioned
by ``user_version`` (see :mod:`minecore.project.migrations`). It uses a
rollback journal, not WAL, so projects on network shares stay safe.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, Self

from minecore import coords
from minecore._core import core_version
from minecore.project import bulk as _bulk
from minecore.project import history as _history
from minecore.project.errors import (
    ProjectError,
    ProjectFormatError,
    ProjectVersionError,
    ReadOnlyProjectError,
)
from minecore.project.history import OperationRecord
from minecore.project.locking import ProjectLock
from minecore.project.migrations import (
    Migration,
    apply_migrations,
    latest_version,
    load_migrations,
    utc_now,
)

APPLICATION_ID = 0x4D434F52  # "MCOR"
PACKAGE_SUFFIX = ".mcproj"
DB_FILE = "project.sqlite"
DATA_DIRS = ("tables", "sources", "blocks", "geometry")
BACKUP_DIR = "backups"
COORDINATE_SYSTEM_KEY = "coordinate_system"

_KIND = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ProjectInfo:
    """Identity of a project."""

    project_id: str
    name: str
    created_utc: str
    created_by: str
    schema_version: int


@dataclass(frozen=True)
class ObjectRecord:
    """One entry of the project object catalogue."""

    object_id: str
    kind: str
    name: str
    parent_id: str | None
    storage_path: str | None
    metadata: dict[str, Any]
    created_utc: str
    modified_utc: str


def _connect(db: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, autocommit=True)
    else:
        conn = sqlite3.connect(db, autocommit=True)
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 2000")
    return conn


def _check_storage_path(storage_path: str) -> str:
    path = PurePosixPath(storage_path)
    if path.is_absolute() or ".." in path.parts or "\\" in storage_path or not path.parts:
        raise ProjectError(f"storage path must be relative inside the package: {storage_path!r}")
    return str(path)


def _backup(conn: sqlite3.Connection, package: Path, version: int) -> Path:
    folder = package / BACKUP_DIR
    folder.mkdir(exist_ok=True)
    stamp = utc_now().replace(":", "").replace("-", "").replace(".", "")
    target = folder / f"project.v{version}.{stamp}.sqlite"
    dest = sqlite3.connect(target)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return target


class Project:
    """An open MineCore project. Use :meth:`create` or :meth:`open`."""

    def __init__(
        self,
        package: Path,
        conn: sqlite3.Connection,
        lock: ProjectLock | None,
        *,
        read_only: bool,
    ) -> None:
        self._package = package
        self._conn: sqlite3.Connection | None = conn
        self._lock = lock
        self._read_only = read_only
        self.migration_backup: Path | None = None

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def create(
        cls, path: str | Path, name: str, *, migrations: tuple[Migration, ...] | None = None
    ) -> Project:
        """Create a new, empty project package at ``path`` (must end in ``.mcproj``)."""
        package = Path(path)
        if package.suffix != PACKAGE_SUFFIX:
            raise ProjectError(f"project path must end with {PACKAGE_SUFFIX}: {package}")
        if not name.strip():
            raise ProjectError("project name must not be empty")
        if package.exists() and (not package.is_dir() or any(package.iterdir())):
            raise ProjectError(f"path exists and is not an empty directory: {package}")
        steps = load_migrations() if migrations is None else migrations

        created_dir = not package.exists()
        package.mkdir(parents=True, exist_ok=True)
        lock = ProjectLock(package)
        conn: sqlite3.Connection | None = None
        try:
            lock.acquire()
            for sub in DATA_DIRS:
                (package / sub).mkdir()
            conn = _connect(package / DB_FILE, read_only=False)
            conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            apply_migrations(conn, steps, core_version())
            conn.execute(
                "INSERT INTO project_info (singleton, project_id, name, created_utc, created_by) "
                "VALUES (1, ?, ?, ?, ?)",
                (str(uuid.uuid4()), name.strip(), utc_now(), core_version()),
            )
        except BaseException:
            if conn is not None:
                conn.close()
            lock.release()
            _remove_contents(package, remove_root=created_dir)
            raise
        return cls(package, conn, lock, read_only=False)

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        read_only: bool = False,
        migrations: tuple[Migration, ...] | None = None,
    ) -> Project:
        """Open an existing project.

        Read-write opening takes the single-writer lock and migrates an older
        schema after backing up ``project.sqlite`` to ``backups/``. Read-only
        opening takes no lock and never migrates.

        Raises:
            ProjectFormatError: not a MineCore project, or damaged.
            ProjectVersionError: written by a newer MineCore, or (read-only)
                needs a migration first.
            ProjectLockedError: another process has it open for writing.
            MigrationError: a migration failed; the project is unchanged.
        """
        package = Path(path)
        db = package / DB_FILE
        if not package.is_dir() or not db.is_file():
            raise ProjectFormatError(f"not a MineCore project (no {DB_FILE}): {package}")
        steps = load_migrations() if migrations is None else migrations
        latest = latest_version(steps)

        # Inspect read-only first so a foreign or too-new file is never touched.
        conn = _connect_checked(db, package, read_only=True)
        try:
            _check_version(_check_identity(conn, package), latest, read_only=read_only)
        except BaseException:
            conn.close()
            raise
        if read_only:
            return cls(package, conn, None, read_only=True)
        conn.close()

        lock = ProjectLock(package)
        lock.acquire()
        backup: Path | None = None
        try:
            conn = _connect_checked(db, package, read_only=False)
            try:
                version = _check_identity(conn, package)
                _check_version(version, latest, read_only=False)
                if version < latest:
                    backup = _backup(conn, package, version)
                    apply_migrations(conn, steps, core_version())
            except BaseException:
                conn.close()
                raise
        except BaseException:
            lock.release()
            raise
        project = cls(package, conn, lock, read_only=False)
        project.migration_backup = backup
        project.collect_garbage()
        return project

    def close(self) -> None:
        """Close the database and release the lock. Safe to call twice."""
        conn, self._conn = self._conn, None
        if conn is not None:
            conn.close()
        if self._lock is not None:
            self._lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- properties ----------------------------------------------------------

    @property
    def path(self) -> Path:
        """Package directory."""
        return self._package

    @property
    def read_only(self) -> bool:
        """True when opened read-only."""
        return self._read_only

    @property
    def closed(self) -> bool:
        """True after :meth:`close`."""
        return self._conn is None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise ProjectError("project is closed")
        return self._conn

    def _writable(self) -> sqlite3.Connection:
        if self._read_only:
            raise ReadOnlyProjectError(f"project is open read-only: {self._package}")
        return self._db()

    @property
    def schema_version(self) -> int:
        """Schema version of ``project.sqlite``."""
        return int(self._db().execute("PRAGMA user_version").fetchone()[0])

    @property
    def info(self) -> ProjectInfo:
        """Project identity."""
        row = (
            self._db()
            .execute("SELECT project_id, name, created_utc, created_by FROM project_info")
            .fetchone()
        )
        if row is None:
            raise ProjectFormatError(f"project_info is missing: {self._package}")
        return ProjectInfo(
            project_id=row[0],
            name=row[1],
            created_utc=row[2],
            created_by=row[3],
            schema_version=self.schema_version,
        )

    def integrity_errors(self) -> list[str]:
        """Check the database and every referenced bulk file; empty means healthy.

        Runs SQLite's full integrity check and verifies the size and SHA-256
        of each bulk file the current catalogue refers to.
        """
        rows = self._db().execute("PRAGMA integrity_check").fetchall()
        messages = [str(r[0]) for r in rows]
        errors = [] if messages == ["ok"] else messages
        for record in self.objects():
            for ref in _bulk.file_refs(record.metadata):
                problem = _bulk.verify(self._package, _bulk.FileRef.from_json(ref))
                if problem is not None:
                    errors.append(f"{record.kind} {record.name!r}: {problem}")
        return errors

    def collect_garbage(self) -> list[str]:
        """Delete bulk files nothing can refer to any more; return their paths.

        A file stays while the catalogue or any operation in the history
        (including undone ones that can be redone) refers to it.
        """
        return _bulk.collect_garbage(self._package, self._writable())

    # -- settings ------------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Return a JSON setting, or ``default`` when absent."""
        row = self._db().execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_setting(self, key: str, value: Any) -> None:
        """Store a JSON-serialisable setting (an undoable operation)."""
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self.operation("set_setting", f"Set {key}", {"key": key}):
            self._writable().execute(
                "INSERT INTO settings (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (key, payload),
            )

    # -- operations and undo -------------------------------------------------

    @contextmanager
    def operation(
        self, kind: str, description: str, parameters: dict[str, Any] | None = None
    ) -> Iterator[None]:
        """Group changes into one undoable operation and one transaction.

        Nested calls join the outer operation. If the body raises, nothing is
        changed and nothing is logged.
        """
        with _history.operation(self._writable(), kind, description, parameters):
            yield

    def history(self) -> list[OperationRecord]:
        """The operation log, oldest first; undone entries are still redoable."""
        return _history.history(self._db())

    def can_undo(self) -> bool:
        """True when an operation can be undone."""
        return any(op.state == "done" for op in self.history())

    def can_redo(self) -> bool:
        """True when an undone operation can be redone."""
        return any(op.state == "undone" for op in self.history())

    def undo(self) -> OperationRecord | None:
        """Undo the latest operation and return it; ``None`` if nothing to undo."""
        return _history.undo(self._writable())

    def redo(self) -> OperationRecord | None:
        """Redo the next undone operation and return it; ``None`` if nothing to redo."""
        return _history.redo(self._writable())

    # -- coordinate system ---------------------------------------------------

    def coordinate_system(self) -> coords.WorkingSystem | None:
        """The working coordinate system, or ``None`` if not yet defined."""
        data = self.get_setting(COORDINATE_SYSTEM_KEY)
        return None if data is None else coords.from_json(data)

    def set_coordinate_system(self, system: coords.WorkingSystem) -> None:
        """Define the working coordinate system.

        It can be changed freely while the project holds no objects. Afterwards
        a change would silently move existing geometry, so it is refused until
        reprojection of stored data exists.
        """
        new = coords.to_json(system)
        current = self.get_setting(COORDINATE_SYSTEM_KEY)
        if current is not None and current != new and self.objects():
            raise ProjectError(
                "the coordinate system cannot change once the project holds objects; "
                "reprojecting stored data is not supported yet"
            )
        with self.operation(
            "set_coordinate_system", f"Set coordinate system: {system.describe()}", {"system": new}
        ):
            self.set_setting(COORDINATE_SYSTEM_KEY, new)

    # -- object catalogue ----------------------------------------------------

    def register_object(
        self,
        kind: str,
        name: str,
        *,
        parent_id: str | None = None,
        storage_path: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObjectRecord:
        """Add an object to the catalogue and return its record.

        Raises:
            ProjectError: invalid kind, name or storage path, duplicate
                ``(kind, name)``, or unknown parent.
        """
        conn = self._writable()
        if not _KIND.match(kind):
            raise ProjectError(f"invalid object kind {kind!r}; use lower_snake_case")
        if not name.strip():
            raise ProjectError("object name must not be empty")
        path = None if storage_path is None else _check_storage_path(storage_path)
        meta = json.dumps(metadata or {}, ensure_ascii=False, allow_nan=False, sort_keys=True)
        now = utc_now()
        object_id = str(uuid.uuid4())
        try:
            with self.operation(
                "register_object", f"Add {kind} {name.strip()!r}", {"object_id": object_id}
            ):
                conn.execute(
                    "INSERT INTO objects (object_id, kind, name, parent_id, storage_path, "
                    "metadata_json, created_utc, modified_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (object_id, kind, name.strip(), parent_id, path, meta, now, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectError(f"cannot register {kind} {name!r}: {exc}") from exc
        return self.get_object(object_id)

    def update_object(
        self,
        object_id: str,
        *,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> ObjectRecord:
        """Change an object's name and/or metadata in one operation.

        Bulk files referenced by the new metadata must already be complete.

        Raises:
            ProjectError: unknown object, empty or duplicate name.
        """
        conn = self._writable()
        current = self.get_object(object_id)
        new_name = current.name if name is None else name.strip()
        if not new_name:
            raise ProjectError("object name must not be empty")
        meta = current.metadata if metadata is None else metadata
        text = json.dumps(meta, ensure_ascii=False, allow_nan=False, sort_keys=True)
        try:
            with self.operation(
                "update_object",
                description or f"Update {current.kind} {new_name!r}",
                {"object_id": object_id},
            ):
                conn.execute(
                    "UPDATE objects SET name = ?, metadata_json = ?, modified_utc = ? "
                    "WHERE object_id = ?",
                    (new_name, text, utc_now(), object_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectError(f"cannot rename {current.kind} to {new_name!r}: {exc}") from exc
        return self.get_object(object_id)

    def delete_object(self, object_id: str) -> None:
        """Remove an object from the catalogue (undoable; its files stay until unreferenced).

        Raises:
            ProjectError: unknown object, or other objects have it as parent.
        """
        conn = self._writable()
        current = self.get_object(object_id)
        try:
            with self.operation(
                "delete_object",
                f"Delete {current.kind} {current.name!r}",
                {"object_id": object_id},
            ):
                conn.execute("DELETE FROM objects WHERE object_id = ?", (object_id,))
        except sqlite3.IntegrityError as exc:
            raise ProjectError(f"cannot delete {current.kind} {current.name!r}: {exc}") from exc

    def get_object(self, object_id: str) -> ObjectRecord:
        """Return one catalogue record."""
        row = (
            self._db()
            .execute(f"SELECT {_OBJECT_COLUMNS} FROM objects WHERE object_id = ?", (object_id,))
            .fetchone()
        )
        if row is None:
            raise ProjectError(f"no object with id {object_id}")
        return _record(row)

    def objects(self, kind: str | None = None) -> list[ObjectRecord]:
        """List catalogue records, optionally of one kind, ordered by kind and name."""
        sql = f"SELECT {_OBJECT_COLUMNS} FROM objects"
        args: tuple[str, ...] = ()
        if kind is not None:
            sql += " WHERE kind = ?"
            args = (kind,)
        rows = self._db().execute(sql + " ORDER BY kind, name", args).fetchall()
        return [_record(r) for r in rows]


_OBJECT_COLUMNS = (
    "object_id, kind, name, parent_id, storage_path, metadata_json, created_utc, modified_utc"
)


def _record(row: tuple[Any, ...]) -> ObjectRecord:
    return ObjectRecord(
        object_id=row[0],
        kind=row[1],
        name=row[2],
        parent_id=row[3],
        storage_path=row[4],
        metadata=json.loads(row[5]),
        created_utc=row[6],
        modified_utc=row[7],
    )


def _check_identity(conn: sqlite3.Connection, package: Path) -> int:
    try:
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise ProjectFormatError(f"{DB_FILE} is not a readable database: {package}") from exc
    if app_id != APPLICATION_ID:
        raise ProjectFormatError(f"{DB_FILE} is not a MineCore project database: {package}")
    if check != "ok":
        raise ProjectFormatError(f"project database is damaged ({check}): {package}")
    return version


def _check_version(version: int, latest: int, *, read_only: bool) -> None:
    if version > latest:
        raise ProjectVersionError(
            f"project format v{version} is newer than this MineCore supports "
            f"(v{latest}); upgrade MineCore to open it"
        )
    if version < latest and read_only:
        raise ProjectVersionError(
            f"project format v{version} must be upgraded to v{latest}; open it read-write once"
        )


def _connect_checked(db: Path, package: Path, *, read_only: bool) -> sqlite3.Connection:
    try:
        return _connect(db, read_only=read_only)
    except sqlite3.DatabaseError as exc:
        raise ProjectFormatError(f"{DB_FILE} is not a readable database: {package}") from exc


def _remove_contents(path: Path, *, remove_root: bool) -> None:
    for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink()
    if remove_root:
        path.rmdir()
