"""Downhole compositing (length-weighted), with optional domain boundaries.

Semantics are pinned by the independent references in
``tests/reference/compositing`` (T-012):

* Composites are ``[origin + kL, origin + (k+1)L)``, starting with the bin
  that contains the shallowest sample and clipped at the deepest sample end.
* Grade = sum(grade * overlap) / sum(overlap of samples with a grade).
  Missing grades (NaN) are not zero: they reduce coverage.
* A composite whose coverage (sampled length / composite length) is below
  ``min_coverage`` keeps its row but has no grade.
* The final bin, when shorter than L/2: ``drop`` removes it, ``merge`` joins
  it to the previous bin, ``keep`` keeps it.
* With domains, every run of consecutive same-domain samples is composited
  on its own, from the run's first depth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from minecore.drillholes.model import DrillholeError

Floats = npt.NDArray[np.float64]
_EPS = 1e-12


class LastComposite(StrEnum):
    KEEP = "keep"
    DROP = "drop"
    MERGE = "merge"


@dataclass(frozen=True)
class Composite:
    start: float
    end: float
    grade: float
    """NaN when nothing was sampled or coverage is below the minimum."""
    sampled_length: float
    coverage: float
    domain: str | None = None


def _stats(  # noqa: PLR0913, PLR0917 - private arithmetic helper
    start: Floats, end: Floats, grade: Floats, a: float, b: float, min_coverage: float
) -> tuple[float, float, float]:
    overlap = np.clip(np.minimum(b, end) - np.maximum(a, start), 0.0, None)
    has = ~np.isnan(grade)
    sampled = float(overlap[has].sum())
    weighted = float((overlap[has] * grade[has]).sum())
    coverage = sampled / (b - a)
    value = weighted / sampled if sampled > 0 and coverage + _EPS >= min_coverage else math.nan
    return value, sampled, coverage


def _check(start: Floats, end: Floats) -> None:
    if np.any(~np.isfinite(start)) or np.any(~np.isfinite(end)) or np.any(end <= start):
        raise DrillholeError("composite input needs finite intervals with from < to")
    order = np.argsort(start, kind="stable")
    if np.any(start[order][1:] < end[order][:-1] - _EPS):
        raise DrillholeError("composite input intervals overlap; validate the table first")


def composite_fixed(  # noqa: PLR0913 - options are keyword-only
    start: Floats,
    end: Floats,
    grade: Floats,
    length: float,
    *,
    origin: float = 0.0,
    min_coverage: float = 0.5,
    last: LastComposite = LastComposite.KEEP,
    domain: str | None = None,
) -> list[Composite]:
    """Fixed-length composites of one hole (no domains)."""
    if not (math.isfinite(length) and length > 0):
        raise DrillholeError(f"composite length must be > 0, not {length}")
    start, end, grade = (np.asarray(v, np.float64) for v in (start, end, grade))
    if start.size == 0:
        return []
    _check(start, end)
    lo, hi = float(start.min()), float(end.max())
    a = origin + math.floor((lo - origin) / length + _EPS) * length
    bins: list[list[float]] = []
    while a < hi - _EPS:
        bins.append([a, min(a + length, hi)])
        a += length
    if bins and bins[-1][1] - bins[-1][0] < length / 2 - _EPS:
        if last is LastComposite.DROP:
            bins.pop()
        elif last is LastComposite.MERGE and len(bins) > 1:
            tail = bins.pop()
            bins[-1][1] = tail[1]
    return [
        Composite(a, b, *_stats(start, end, grade, a, b, min_coverage), domain=domain)
        for a, b in bins
    ]


def composite_by_domain(  # noqa: PLR0913 - options are keyword-only
    start: Floats,
    end: Floats,
    grade: Floats,
    domains: list[str | None],
    length: float,
    *,
    min_coverage: float = 0.5,
    last: LastComposite = LastComposite.KEEP,
) -> list[Composite]:
    """Fixed-length composites restarting at every domain change."""
    start, end, grade = (np.asarray(v, np.float64) for v in (start, end, grade))
    _check(start, end)
    order = np.argsort(start, kind="stable")
    out: list[Composite] = []
    run: list[int] = []
    for i in [*order.tolist(), None]:
        if run and (i is None or domains[i] != domains[run[-1]]):
            idx = np.array(run)
            out += composite_fixed(
                start[idx],
                end[idx],
                grade[idx],
                length,
                origin=float(start[idx[0]]),
                min_coverage=min_coverage,
                last=last,
                domain=domains[run[0]],
            )
            run = []
        if i is not None:
            run.append(i)
    return out
