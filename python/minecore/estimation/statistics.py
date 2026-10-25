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


@dataclass(frozen=True)
class Declustering:
    cell_sizes: Floats
    means: Floats  # declustered mean per cell size
    best_size: float  # size giving the minimum (or maximum) mean
    weights: Floats  # sample weights at the best size, summing to n


def cell_decluster(  # noqa: PLR0913 - options are keyword-only
    xyz: Floats,
    values: Floats,
    cell_sizes: Floats,
    *,
    anisotropy: tuple[float, float] = (1.0, 1.0),
    offsets: int = 4,
    minimise: bool = True,
) -> Declustering:
    """Cell declustering (Journel 1983; Deutsch, GSLIB DECLUS).

    Each sample is weighted by ``1 / (samples in its cell)`` for a cell of
    size ``s`` in x and ``s * anisotropy`` in y and z, averaged over
    ``offsets`` grid origins. The best size minimises the declustered mean
    when high grades are preferentially drilled (``minimise=True``).
    """
    ok = np.isfinite(values) & np.isfinite(xyz).all(axis=1)
    p, v = np.asarray(xyz, np.float64)[ok], np.asarray(values, np.float64)[ok]
    n = len(v)
    if n < 2:
        raise EstimationError("declustering needs at least two samples")
    scale = np.array([1.0, anisotropy[0], anisotropy[1]])
    lo = p.min(axis=0)
    means = []
    all_weights = []
    for s in np.asarray(cell_sizes, np.float64):
        if s <= 0:
            raise EstimationError("cell sizes must be > 0")
        size = s * scale
        w = np.zeros(n)
        for k in range(offsets):
            shift = size * k / offsets
            cell = np.floor((p - lo + shift) / size).astype(np.int64)
            _, inverse, counts = np.unique(cell, axis=0, return_inverse=True, return_counts=True)
            w += 1.0 / counts[inverse.ravel()]
        w *= n / w.sum()
        means.append(float((w * v).sum() / n))
        all_weights.append(w)
    m = np.array(means)
    best = int(np.argmin(m) if minimise else np.argmax(m))
    weights = np.zeros(len(values))
    weights[ok] = all_weights[best]
    return Declustering(np.asarray(cell_sizes, np.float64), m, float(cell_sizes[best]), weights)
