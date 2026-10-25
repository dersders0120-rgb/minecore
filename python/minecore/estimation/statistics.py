"""Sample statistics for outlier treatment (top-cut) decisions.

Length weighting is not applied: use composites of equal length, which is
the usual input to these checks. The log-probability points use the Hazen
plotting position ``(i - 0.5) / n``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.stats import norm

from minecore.estimation.variogram import EstimationError

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    std: float
    cv: float
    minimum: float
    median: float
    maximum: float
    percentiles: dict[float, float]


def summary(values: Floats, percentiles: tuple[float, ...] = (90, 95, 97.5, 99, 99.5)) -> Summary:
    v = np.asarray(values, np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise EstimationError("no values")
    mean = float(v.mean())
    std = float(v.std())
    return Summary(
        int(v.size),
        mean,
        std,
        std / mean if mean else math.nan,
        float(v.min()),
        float(np.median(v)),
        float(v.max()),
        {p: float(np.percentile(v, p)) for p in percentiles},
    )


@dataclass(frozen=True)
class TopCutEffect:
    cap: float
    capped: int  # samples above the cap
    mean_before: float
    mean_after: float
    metal_removed: float  # fraction of the uncapped mean removed
    cv_after: float


def top_cut_effect(values: Floats, cap: float) -> TopCutEffect:
    v = np.asarray(values, np.float64)
    v = v[np.isfinite(v)]
    if v.size == 0:
        raise EstimationError("no values")
    after = np.minimum(v, cap)
    before = float(v.mean())
    mean_after = float(after.mean())
    return TopCutEffect(
        cap,
        int((v > cap).sum()),
        before,
        mean_after,
        1.0 - mean_after / before if before else math.nan,
        float(after.std() / mean_after) if mean_after else math.nan,
    )


def probability_points(values: Floats) -> tuple[Floats, Floats]:
    """Sorted positive values and their standard normal quantiles (log-probability plot)."""
    v = np.sort(np.asarray(values, np.float64)[np.isfinite(values)])
    v = v[v > 0]
    if v.size == 0:
        raise EstimationError("no positive values")
    p = (np.arange(1, v.size + 1) - 0.5) / v.size
    return v, np.asarray(norm.ppf(p), np.float64)
