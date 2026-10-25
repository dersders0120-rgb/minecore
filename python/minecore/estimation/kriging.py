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


def estimate(  # noqa: PLR0913, PLR0912, PLR0915 - one loop per target, kept explicit
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
    dist, idx = tree.query(targets @ transform, k=k, distance_upper_bound=1.0)
    dist = np.atleast_2d(dist).reshape(m, -1)
    idx = np.atleast_2d(idx).reshape(m, -1)

    if block_size is not None:
        offsets = _discretise(block_size, discretisation)
    else:
        offsets = np.zeros((1, 3))
    cbb = None
    if method is Method.ORDINARY_KRIGING:
        assert variogram is not None
        diff = offsets[:, None, :] - offsets[None, :, :]
        cbb = float(variogram.covariance(diff).mean())

    for t in range(m):
        valid = np.isfinite(dist[t])
        cand, cd = idx[t][valid], dist[t][valid]
        if exclude_hole is not None:
            assert holes is not None
            other = holes[cand] != exclude_hole[t]
            cand, cd = cand[other], cd[other]
        if holes is not None and search.max_per_hole:
            taken: dict[int, int] = {}
            keep = []
            for c in cand:
                h = int(holes[c])
                if taken.get(h, 0) < search.max_per_hole:
                    taken[h] = taken.get(h, 0) + 1
                    keep.append(True)
                else:
                    keep.append(False)
            mask = np.array(keep, bool)
            cand, cd = cand[mask], cd[mask]
        cand, cd = cand[: search.max_samples], cd[: search.max_samples]
        if len(cand) < search.min_samples:
            continue
        used[t] = len(cand)
        mean_dist[t] = float(cd.mean())
        v = values[cand]
        if method is Method.NEAREST:
            value[t] = v[0]
        elif method is Method.INVERSE_DISTANCE:
            if cd[0] < 1e-12:
                value[t] = v[0]
            else:
                w = 1.0 / cd**power
                value[t] = float((w * v).sum() / w.sum())
        else:
            assert variogram is not None
            assert cbb is not None
            p = samples[cand]
            n = len(cand)
            a = np.ones((n + 1, n + 1))
            a[:n, :n] = variogram.covariance(p[:, None, :] - p[None, :, :])
            a[n, n] = 0.0
            points = targets[t] + offsets
            b = np.ones(n + 1)
            b[:n] = variogram.covariance(p[:, None, :] - points[None, :, :]).mean(axis=1)
            try:
                sol = np.linalg.solve(a, b)
            except np.linalg.LinAlgError:
                sol = np.linalg.lstsq(a, b, rcond=None)[0]
            w, mu = sol[:n], sol[n]
            value[t] = float(w @ v)
            variance[t] = cbb - float(w @ b[:n]) - mu
        if progress is not None and t % 2000 == 0:
            progress.update(t / m, f"{t}/{m}")
    if progress is not None:
        progress.update(1.0, f"{m}/{m}")
    unestimated = int(np.count_nonzero(np.isnan(value)))
    return EstimationResult(value, variance, used, mean_dist, unestimated)


def inverse_distance_weights(distances: Floats, power: float) -> Floats:
    """Normalised IDW weights (used in tests and reports)."""
    w = 1.0 / np.asarray(distances, np.float64) ** power
    return w / w.sum()
