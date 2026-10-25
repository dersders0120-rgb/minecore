"""Grade estimation at points or blocks: nearest neighbour, inverse distance, ordinary kriging.

All methods share one anisotropic search (:class:`Search`): candidate samples
are those inside the search ellipsoid, nearest first (by reduced distance),
at most ``max_samples``; a target with fewer than ``min_samples`` is left
unestimated (NaN) and counted.

Ordinary kriging solves, per target, ``[C 1; 1' 0] [w; mu] = [c; 1]`` where
``C`` holds sample-sample covariances and ``c`` sample-target covariances.
For blocks, ``c`` and the block variance average point covariances over a
regular discretisation of the block (``discretisation`` points per axis).
Kriging variance is ``C_bb - w'c - mu``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt
from scipy.spatial import cKDTree

from minecore._core import estimate_neighbourhood
from minecore.estimation.variogram import Ellipsoid, EstimationError, VariogramModel
from minecore.jobs import Progress

Floats = npt.NDArray[np.float64]


class Method(StrEnum):
    NEAREST = "nearest"
    INVERSE_DISTANCE = "inverse_distance"
    ORDINARY_KRIGING = "ordinary_kriging"


@dataclass(frozen=True)
class Search:
    ellipsoid: Ellipsoid
    min_samples: int = 4
    max_samples: int = 24
    max_per_hole: int = 0
    """0 = no limit; otherwise at most this many samples from one hole."""

    def __post_init__(self) -> None:
        if not (1 <= self.min_samples <= self.max_samples):
            raise EstimationError("search needs 1 <= min_samples <= max_samples")
        if self.max_per_hole < 0:
            raise EstimationError("max_per_hole must be >= 0")


@dataclass(frozen=True)
class EstimationResult:
    value: Floats
    variance: Floats
    """Kriging variance (NaN for other methods)."""
    samples_used: npt.NDArray[np.int64]
    mean_distance: Floats
    """Mean anisotropic (reduced) distance of the samples used."""
    unestimated: int


def _discretise(size: tuple[float, float, float], n: tuple[int, int, int]) -> Floats:
    axes = [(np.arange(k) + 0.5) / k * s - s / 2 for s, k in zip(size, n, strict=True)]
    gx, gy, gz = np.meshgrid(*axes, indexing="ij")
    return np.column_stack([gx.ravel(), gy.ravel(), gz.ravel()])


def estimate(  # noqa: PLR0913 - options are keyword-only
    samples: Floats,
    values: Floats,
    targets: Floats,
    search: Search,
    method: Method,
    *,
    variogram: VariogramModel | None = None,
    power: float = 2.0,
    block_size: tuple[float, float, float] | None = None,
    discretisation: tuple[int, int, int] = (1, 1, 1),
    hole_ids: npt.NDArray[np.int64] | None = None,
    exclude_hole: npt.NDArray[np.int64] | None = None,
    progress: Progress | None = None,
) -> EstimationResult:
    """Estimate ``values`` known at ``samples`` (n, 3) at ``targets`` (m, 3).

    ``exclude_hole[t]`` (with ``hole_ids``) drops every sample of that hole
    from target ``t``'s neighbourhood: leave-one-hole-out cross-validation.
    """
    samples = np.asarray(samples, np.float64)
    values = np.asarray(values, np.float64)
    targets = np.asarray(targets, np.float64)
    ok = np.isfinite(values) & np.isfinite(samples).all(axis=1)
    samples, values = samples[ok], values[ok]
    holes = None if hole_ids is None else np.asarray(hole_ids)[ok]
    if method is Method.ORDINARY_KRIGING and variogram is None:
        raise EstimationError("ordinary kriging needs a variogram model")
    m = len(targets)
    value = np.full(m, np.nan)
    variance = np.full(m, np.nan)
    used = np.zeros(m, np.int64)
    mean_dist = np.full(m, np.nan)
    if len(values) == 0:
        return EstimationResult(value, variance, used, mean_dist, m)

    ell = search.ellipsoid
    transform = (ell.axes().T / np.array([ell.major, ell.semi, ell.minor])).astype(np.float64)
    tree = cKDTree(samples @ transform)
    k = min(len(values), search.max_samples * (4 if search.max_per_hole else 1))
    if exclude_hole is not None:
        if holes is None:
            raise EstimationError("exclude_hole needs hole_ids")
        k = min(len(values), search.max_samples * 4 + 200)
    if block_size is not None and method is Method.ORDINARY_KRIGING:
        offsets = _discretise(block_size, discretisation)
    else:
        offsets = np.zeros((1, 3))
    structures = []
    if method is Method.ORDINARY_KRIGING:
        assert variogram is not None
        for s in variogram.structures:
            e = s.ranges
            rows = e.axes() / np.array([[e.major], [e.semi], [e.minor]])
            structures.append((s.type.value, float(s.sill), rows.tolist()))
    scaled_targets = targets @ transform
    chunk = 100_000
    for lo in range(0, m, chunk):
        hi = min(lo + chunk, m)
        dist, idx = tree.query(scaled_targets[lo:hi], k=k, distance_upper_bound=1.0)
        dist = np.asarray(dist, np.float64).reshape(hi - lo, -1)
        idx = np.asarray(idx).reshape(hi - lo, -1)
        idx = np.where(idx >= len(values), np.iinfo(np.uint32).max, idx).astype(np.uint32)
        v, var, n_used, md = estimate_neighbourhood(
            method.value,
            samples,
            values,
            targets[lo:hi],
            idx,
            dist,
            min_samples=search.min_samples,
            max_samples=search.max_samples,
            max_per_hole=search.max_per_hole if holes is not None else 0,
            holes=None if holes is None else holes.astype(np.int64),
            exclude_hole=None
            if exclude_hole is None
            else np.asarray(exclude_hole[lo:hi], np.int64),
            power=power,
            nugget=0.0 if variogram is None else variogram.nugget,
            structures=structures,
            offsets=offsets,
        )
        value[lo:hi], variance[lo:hi], used[lo:hi], mean_dist[lo:hi] = v, var, n_used, md
        if progress is not None:
            progress.update(hi / m, f"{hi}/{m}")
    unestimated = int(np.count_nonzero(np.isnan(value)))
    return EstimationResult(value, variance, used, mean_dist, unestimated)


def inverse_distance_weights(distances: Floats, power: float) -> Floats:
    """Normalised IDW weights (used in tests and reports)."""
    w = 1.0 / np.asarray(distances, np.float64) ** power
    return w / w.sum()
