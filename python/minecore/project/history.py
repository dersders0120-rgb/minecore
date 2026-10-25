"""Operation log and linear undo/redo (ADR-0007).

Every change to a tracked table runs inside an operation. SQLite triggers
(schema migration 0002) record a before/after image of each changed row, so
undo and redo need no per-command inverse code: undo writes the before images
in reverse order, redo the after images in order. Each operation, undo and
redo is one transaction; a crash leaves the project at the last committed
state.
"""

from __future__ import annotations

import getpass
import json
import re
import socket
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from minecore._core import core_version
from minecore.project.errors import ProjectError
from minecore.project.migrations import utc_now

_KIND = re.compile(r"^[a-z][a-z0-9_]*$")

# Tracked table -> (primary key column, all columns). Must match the triggers.
TRACKED: dict[str, tuple[str, tuple[str, ...]]] = {
    "settings": ("key", ("key", "value_json")),
    "objects": (
        "object_id",
        (
            "object_id",
            "kind",
            "name",
            "parent_id",
            "storage_path",
            "metadata_json",
            "created_utc",
            "modified_utc",
        ),
    ),
}


@dataclass(frozen=True)
class OperationRecord:
    """One entry of the operation log."""

    seq: int
    operation_id: str
    kind: str
    description: str
    parameters: dict[str, Any]
    state: str
    user_name: str
    host: str
    minecore_version: str
    utc: str
    changes: int


def _user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return "unknown"


_SELECT = (
    "SELECT o.seq, o.operation_id, o.kind, o.description, o.parameters_json, o.state, "
    "o.user_name, o.host, o.minecore_version, o.utc, "
    "(SELECT count(*) FROM operation_changes c WHERE c.seq = o.seq) FROM operations o"
)


def _record(row: tuple[Any, ...]) -> OperationRecord:
    return OperationRecord(
        seq=row[0],
        operation_id=row[1],
        kind=row[2],
        description=row[3],
        parameters=json.loads(row[4]),
        state=row[5],
        user_name=row[6],
        host=row[7],
        minecore_version=row[8],
        utc=row[9],
        changes=row[10],
    )


def history(conn: sqlite3.Connection) -> list[OperationRecord]:
    """All operations, oldest first, including undone ones still redoable."""
    return [_record(r) for r in conn.execute(_SELECT + " ORDER BY o.seq")]


def open_operation(conn: sqlite3.Connection) -> int | None:
    """Sequence number of the operation open on this connection, if any."""
    row = conn.execute("SELECT seq FROM operation_context").fetchone()
    return None if row is None or row[0] is None else int(row[0])


@contextmanager
def operation(
    conn: sqlite3.Connection,
    kind: str,
    description: str,
    parameters: dict[str, Any] | None = None,
) -> Iterator[int | None]:
    """Run the body as one undoable operation and one transaction.

    Nested use joins the outer operation. An operation that changes nothing is
    rolled back and leaves no log entry (and keeps the redo history). Starting
    a new operation discards operations that were undone.
    """
    if conn.in_transaction:
        if open_operation(conn) is None:
            raise ProjectError("a transaction is open outside an operation")
        yield None
        return
    if not _KIND.match(kind):
        raise ProjectError(f"invalid operation kind {kind!r}; use lower_snake_case")
    params = json.dumps(parameters or {}, ensure_ascii=False, allow_nan=False, sort_keys=True)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM operations WHERE state = 'undone'")
        cursor = conn.execute(
            "INSERT INTO operations (operation_id, kind, description, parameters_json, state, "
            "user_name, host, minecore_version, utc) VALUES (?, ?, ?, ?, 'done', ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                kind,
                description,
                params,
                _user(),
                socket.gethostname(),
                core_version(),
                utc_now(),
            ),
        )
        seq = cursor.lastrowid
        conn.execute("UPDATE operation_context SET seq = ?", (seq,))
        yield seq
        changed = conn.execute(
            "SELECT count(*) FROM operation_changes WHERE seq = ?", (seq,)
        ).fetchone()[0]
        if changed == 0:
            conn.execute("ROLLBACK")
            return
        conn.execute("UPDATE operation_context SET seq = NULL")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _apply(conn: sqlite3.Connection, table: str, key: str, image: str | None) -> None:
    key_column, columns = TRACKED[table]
    if image is None:
        conn.execute(f"DELETE FROM {table} WHERE {key_column} = ?", (key,))
        return
    values: dict[str, Any] = json.loads(image)
    if set(values) != set(columns):
        raise ProjectError(f"operation log row for {table} does not match its columns")
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != key_column)
    conn.execute(
        f"INSERT INTO {table} ({names}) VALUES ({marks}) "
        f"ON CONFLICT({key_column}) DO UPDATE SET {updates}",
        tuple(values[c] for c in columns),
    )


def _replay(conn: sqlite3.Connection, *, undo: bool) -> OperationRecord | None:
    if conn.in_transaction:
        raise ProjectError("cannot undo or redo inside an operation")
    conn.execute("BEGIN IMMEDIATE")
    try:
        state, pick, order, image = (
            ("done", "max", "DESC", "old_json") if undo else ("undone", "min", "ASC", "new_json")
        )
        seq = conn.execute(
            f"SELECT {pick}(seq) FROM operations WHERE state = ?", (state,)
        ).fetchone()[0]
        if seq is None:
            conn.execute("ROLLBACK")
            return None
        conn.execute("UPDATE operation_context SET seq = ?, replaying = 1", (seq,))
        changes = conn.execute(
            f"SELECT table_name, row_key, {image} FROM operation_changes WHERE seq = ? "
            f"ORDER BY ord {order}",
            (seq,),
        ).fetchall()
        for table, key, data in changes:
            _apply(conn, table, key, data)
        conn.execute(
            "UPDATE operations SET state = ? WHERE seq = ?", ("undone" if undo else "done", seq)
        )
        conn.execute("UPDATE operation_context SET seq = NULL, replaying = 0")
        record = _record(conn.execute(_SELECT + " WHERE o.seq = ?", (seq,)).fetchone())
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return record


def undo(conn: sqlite3.Connection) -> OperationRecord | None:
    """Undo the latest done operation; ``None`` if there is none."""
    return _replay(conn, undo=True)


def redo(conn: sqlite3.Connection) -> OperationRecord | None:
    """Redo the earliest undone operation; ``None`` if there is none."""
    return _replay(conn, undo=False)
