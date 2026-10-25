"""Long-running work: cancellation and progress (ADR-0007).

Every engine entry point that can run for more than a moment takes an optional
:class:`Progress`. It reports a fraction and a message, and it is also the
cancellation point: :meth:`Progress.update` raises :class:`CancelledError` once
the job's :class:`CancellationToken` is cancelled. Because a job's changes to a
project run inside one operation (one transaction), a cancelled job leaves the
project exactly as it was.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from minecore._core import MineCoreError


class CancelledError(MineCoreError):
    """The job was cancelled by the user; nothing was changed."""


class CancellationToken:
    """Thread-safe cancellation flag shared by a job and its controller (e.g. the GUI)."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation. Idempotent."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """True once :meth:`cancel` was called."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`CancelledError` if cancellation was requested."""
        if self._event.is_set():
            raise CancelledError("cancelled by user")


@dataclass(frozen=True)
class ProgressEvent:
    """One progress report: fraction in [0, 1] (``None`` if unknown) and a message."""

    fraction: float | None
    message: str


Callback = Callable[[ProgressEvent], None]


class Progress:
    """Progress reporter and cancellation point.

    Reports are throttled to one per ``min_interval_s`` (the first and the
    final report always pass). :meth:`span` gives a child reporter for a
    sub-step that maps its own [0, 1] onto a part of the parent's range.
    """

    def __init__(
        self,
        callback: Callback | None = None,
        token: CancellationToken | None = None,
        *,
        min_interval_s: float = 0.1,
    ) -> None:
        self._callback = callback
        self.token = token or CancellationToken()
        self._min_interval = min_interval_s
        self._last_report = -float("inf")
        self._offset = 0.0
        self._width = 1.0
        self._root: Progress = self

    def span(self, start: float, end: float) -> Progress:
        """Child reporter covering ``[start, end]`` of this reporter's range."""
        if not 0.0 <= start <= end <= 1.0:
            raise ValueError(f"invalid progress span [{start}, {end}]")
        child = Progress.__new__(Progress)
        child._callback = self._callback
        child.token = self.token
        child._min_interval = self._min_interval
        child._root = self._root
        child._offset = self._offset + start * self._width
        child._width = (end - start) * self._width
        return child

    def update(self, fraction: float | None, message: str = "") -> None:
        """Report progress; raises :class:`CancelledError` if cancelled."""
        self.token.raise_if_cancelled()
        if self._callback is None:
            return
        overall = None
        if fraction is not None:
            overall = self._offset + min(1.0, max(0.0, fraction)) * self._width
        root = self._root
        now = time.monotonic()
        final = overall is not None and overall >= 1.0
        if not final and now - root._last_report < root._min_interval:
            return
        root._last_report = now
        self._callback(ProgressEvent(overall, message))
