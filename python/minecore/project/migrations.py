"""Versioned schema migrations for ``project.sqlite``.

Migrations are numbered SQL files in ``minecore/project/schema``
(``NNNN_name.sql``). The schema version of a project is SQLite's
``user_version``; migration *N* moves a project from version *N-1* to *N*.
Each migration runs in its own transaction together with its version bump and
history row, so a failure leaves the project at the previous version.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources

from minecore.project.errors import MigrationError

_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
# Migrations run inside a transaction owned by apply_migrations().
_TRANSACTION_CONTROL = re.compile(
    r"^\s*(COMMIT|ROLLBACK|END\s+TRANSACTION|"
    r"BEGIN(\s+(DEFERRED|IMMEDIATE|EXCLUSIVE))?(\s+TRANSACTION)?\s*;)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class Migration:
    """One schema migration."""

    version: int
    name: str
    sql: str


def load_migrations() -> tuple[Migration, ...]:
    """Load the bundled migrations, checking they are numbered 1..N without gaps."""
    found: list[Migration] = []
    for entry in resources.files("minecore.project").joinpath("schema").iterdir():
        match = _NAME.match(entry.name)
        if match is None:
            continue
        sql = entry.read_text("utf-8")
        if _TRANSACTION_CONTROL.search(sql):
            raise MigrationError(f"{entry.name} must not contain transaction statements")
        found.append(Migration(int(match.group(1)), match.group(2), sql))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if versions != list(range(1, len(found) + 1)):
        raise MigrationError(f"bundled migrations are not numbered 1..N: {versions}")
    return tuple(found)


def latest_version(migrations: tuple[Migration, ...]) -> int:
    """Schema version reached after all migrations."""
    return migrations[-1].version if migrations else 0


def utc_now() -> str:
    """Current UTC time as ISO 8601 with a ``Z`` suffix."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: tuple[Migration, ...],
    applied_by: str,
) -> list[int]:
    """Apply every migration newer than the database's ``user_version``.

    ``conn`` must be in autocommit mode. Returns the versions applied.

    Raises:
        MigrationError: a migration failed; that migration is rolled back and
            the database stays at the last successfully applied version.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    applied: list[int] = []
    for migration in migrations:
        if migration.version <= current:
            continue
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_utc, applied_by) "
                "VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, utc_now(), applied_by),
            )
            conn.execute(f"PRAGMA user_version = {int(migration.version)}")
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(
                f"migration {migration.version:04d}_{migration.name} failed: {exc}"
            ) from exc
        applied.append(migration.version)
    return applied
