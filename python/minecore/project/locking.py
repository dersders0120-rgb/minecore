"""Single-writer lock for a project package.

The lock is an operating-system lock on byte 0 of ``project.lock``, held for as
long as the project is open for writing. If the holder crashes, the operating
system releases the lock, so a stale lock file never blocks a project. Holder
details (process id, host, time) are written after byte 0 for error messages.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from minecore.project.errors import ProjectLockedError

LOCK_FILE = "project.lock"


def _try_lock(handle: BinaryIO) -> bool:
    handle.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if sys.platform == "win32":
        import msvcrt  # noqa: PLC0415

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # noqa: PLC0415

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_holder(package: Path) -> dict[str, str] | None:
    """Return the recorded lock holder, if readable."""
    try:
        with (package / LOCK_FILE).open("rb") as fh:
            fh.seek(1)
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else None


class ProjectLock:
    """Exclusive write lock on one project package."""

    def __init__(self, package: Path) -> None:
        self._path = package / LOCK_FILE
        self._handle: BinaryIO | None = None

    @property
    def held(self) -> bool:
        """True while this object holds the lock."""
        return self._handle is not None

    def acquire(self) -> None:
        """Take the lock or raise :class:`ProjectLockedError` immediately."""
        if self._handle is not None:
            return
        handle = self._path.open("a+b")
        if not _try_lock(handle):
            handle.close()
            holder = read_holder(self._path.parent)
            detail = ""
            if holder:
                detail = (
                    f" by process {holder.get('pid')} on {holder.get('host')}"
                    f" since {holder.get('since')}"
                )
            raise ProjectLockedError(f"project is open for writing{detail}: {self._path.parent}")
        info = {
            "pid": str(os.getpid()),
            "host": socket.gethostname(),
            "since": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        handle.seek(1)
        handle.truncate()
        handle.write(json.dumps(info).encode("utf-8"))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        """Release the lock if held."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()
