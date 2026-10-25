"""Variograms: anisotropy, nested models and experimental variograms.

Conventions (binding; the independent references in T-016 use the same):

* Coordinates are east, north, up (x, y, z) in metres.
* An anisotropy ellipsoid has ranges ``major >= semi >= minor`` and three
  angles in degrees. ``azimuth`` is the direction of the major axis,
  clockwise from north. ``dip`` tilts the major axis below horizontal
  (positive dip = down, as geologists quote it). The semi-major axis starts
  horizontal, 90 degrees clockwise from the major's azimuth; ``rake`` turns it
  towards ``major x semi`` (downward for a horizontal major axis). The frame
  (major, semi, minor) is right-handed with ``minor = major x semi``.
* A lag vector ``h`` is rotated into ellipsoid axes and scaled by the ranges;
  the result's length is the *reduced* distance (1 = at the range).
* Structure models use *practical* ranges: exponential ``1 - exp(-3r)``,
  gaussian ``1 - exp(-3r^2)``, spherical ``1.5r - 0.5r^3`` (r <= 1).
* The variogram is ``nugget * (h > 0) + sum(sill_i * model_i(r_i))``; the
  covariance is ``total_sill - gamma``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from minecore._core import MineCoreError

Floats = npt.NDArray[np.float64]


class EstimationError(MineCoreError):
    """Invalid variogram, search or estimation input."""


class ModelType(StrEnum):
    SPHERICAL = "spherical"
    EXPONENTIAL = "exponential"
    GAUSSIAN = "gaussian"


@dataclass(frozen=True)
class Ellipsoid:
    major: float
    semi: float
    minor: float
    azimuth: float = 0.0
    dip: float = 0.0
    rake: float = 0.0

    def __post_init__(self) -> None:
        values = (self.major, self.semi, self.minor, self.azimuth, self.dip, self.rake)
        if not all(math.isfinite(v) for v in values):
            raise EstimationError("ellipsoid values must be finite")
        if not (self.major > 0 and self.semi > 0 and self.minor > 0):
            raise EstimationError("ellipsoid ranges must be > 0")

    @classmethod
    def isotropic(cls, radius: float) -> Ellipsoid:
        return cls(radius, radius, radius)

    def axes(self) -> Floats:
        """Rows: unit vectors of the major, semi-major and minor axes (x, y, z)."""
        az, dp, rk = (math.radians(a) for a in (self.azimuth, self.dip, self.rake))
        major = np.array([math.sin(az) * math.cos(dp), math.cos(az) * math.cos(dp), -math.sin(dp)])
        # semi0: horizontal, 90 degrees clockwise from the major's azimuth.
        # minor0 = major x semi0 (downward when the major axis is horizontal).
        # Rake turns semi0 towards minor0; the frame (major, semi, minor) is right-handed.
        semi0 = np.array([math.cos(az), -math.sin(az), 0.0])
        minor0 = np.cross(major, semi0)
        semi = math.cos(rk) * semi0 + math.sin(rk) * minor0
        minor = np.cross(major, semi)
        return np.vstack([major, semi, minor])

    def reduce(self, lags: Floats) -> Floats:
        """Reduced (range-normalised) lengths of lag vectors, shape (..., 3) -> (...)."""
        local = lags @ self.axes().T
        return np.sqrt(
            (local[..., 0] / self.major) ** 2
            + (local[..., 1] / self.semi) ** 2
            + (local[..., 2] / self.minor) ** 2
        )


@dataclass(frozen=True)
class Structure:
    type: ModelType
    sill: float
    ranges: Ellipsoid

    def __post_init__(self) -> None:
        if not (math.isfinite(self.sill) and self.sill >= 0):
            raise EstimationError("structure sill must be >= 0")

    def gamma_reduced(self, r: Floats) -> Floats:
        if self.type is ModelType.SPHERICAL:
            rc = np.minimum(r, 1.0)
            return self.sill * (1.5 * rc - 0.5 * rc**3)
        if self.type is ModelType.EXPONENTIAL:
            return self.sill * (1.0 - np.exp(-3.0 * r))
        return self.sill * (1.0 - np.exp(-3.0 * r**2))


@dataclass(frozen=True)
class VariogramModel:
    nugget: float
    structures: tuple[Structure, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not (math.isfinite(self.nugget) and self.nugget >= 0):
            raise EstimationError("nugget must be >= 0")
        if self.total_sill <= 0:
            raise EstimationError("the variogram needs a positive total sill")

    @property
    def total_sill(self) -> float:
        return self.nugget + sum(s.sill for s in self.structures)

    def gamma(self, lags: Floats) -> Floats:
        lags = np.asarray(lags, np.float64)
        dist = np.linalg.norm(lags, axis=-1)
        value = np.where(dist > 0, self.nugget, 0.0)
        for s in self.structures:
            value = value + s.gamma_reduced(s.ranges.reduce(lags))
        return np.asarray(value, np.float64)

    def covariance(self, lags: Floats) -> Floats:
        return self.total_sill - self.gamma(lags)

    def to_json(self) -> dict[str, object]:
        return {
            "nugget": self.nugget,
            "structures": [
                {
                    "type": s.type.value,
                    "sill": s.sill,
                    "ranges": [s.ranges.major, s.ranges.semi, s.ranges.minor],
                    "angles": [s.ranges.azimuth, s.ranges.dip, s.ranges.rake],
                }
                for s in self.structures
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> VariogramModel:
        structures = []
        for s in data["structures"]:  # type: ignore[attr-defined]
            ranges, angles = s["ranges"], s["angles"]
            structures.append(
                Structure(ModelType(s["type"]), float(s["sill"]), Ellipsoid(*ranges, *angles))
            )
        return cls(float(data["nugget"]), tuple(structures))  # type: ignore[arg-type]


@dataclass(frozen=True)
class ExperimentalVariogram:
    lag_centres: Floats
    gamma: Floats
    pairs: npt.NDArray[np.int64]
    mean_distance: Floats


def experimental(  # noqa: PLR0913 - options are keyword-only
    xyz: Floats,
    values: Floats,
    lag: float,
    nlags: int,
    *,
    azimuth: float | None = None,
    dip: float = 0.0,
    tolerance_deg: float = 22.5,
    max_pairs: int = 5_000_000,
    seed: int = 0,
) -> ExperimentalVariogram:
    """Semivariogram ``0.5 * mean((v_i - v_j)^2)`` per lag class.

    Class ``k`` (1..nlags) holds pairs with ``k*lag - lag/2 <= |h| < k*lag + lag/2``.

    ``azimuth=None`` gives the omnidirectional variogram; otherwise pairs
    whose separation is within ``tolerance_deg`` of the direction (either
    sense) are used. When there are more than ``max_pairs`` pairs, a random
    subset of pairs is used (reported via ``pairs``).
    """
    if not (lag > 0 and nlags > 0):
        raise EstimationError("lag must be > 0 and nlags >= 1")
    ok = np.isfinite(values) & np.isfinite(xyz).all(axis=1)
    p, v = xyz[ok], values[ok]
    n = len(v)
    total = n * (n - 1) // 2
    if n < 2:
        raise EstimationError("an experimental variogram needs at least two samples")
    if total <= max_pairs:
        i, j = np.triu_indices(n, k=1)
    else:
        rng = np.random.default_rng(seed)
        i = rng.integers(0, n, max_pairs)
        j = rng.integers(0, n, max_pairs)
        keep = i != j
        i, j = i[keep], j[keep]
    h = p[j] - p[i]
    d = np.linalg.norm(h, axis=1)
    sq = 0.5 * (v[j] - v[i]) ** 2
    if azimuth is not None:
        az, dp = math.radians(azimuth), math.radians(dip)
        u = np.array([math.sin(az) * math.cos(dp), math.cos(az) * math.cos(dp), -math.sin(dp)])
        with np.errstate(invalid="ignore", divide="ignore"):
            cosine = np.abs(h @ u) / d
        keep = (d > 0) & (cosine >= math.cos(math.radians(tolerance_deg)))
        d, sq = d[keep], sq[keep]
    k = np.floor(d / lag + 0.5).astype(np.int64)
    keep = (k >= 1) & (k <= nlags)
    k, d, sq = k[keep], d[keep], sq[keep]
    pairs = np.bincount(k, minlength=nlags + 1)[1:]
    with np.errstate(invalid="ignore", divide="ignore"):
        gamma = np.bincount(k, sq, minlength=nlags + 1)[1:] / pairs
        mean_d = np.bincount(k, d, minlength=nlags + 1)[1:] / pairs
    return ExperimentalVariogram(
        np.arange(1, nlags + 1, dtype=np.float64) * lag, gamma, pairs.astype(np.int64), mean_d
    )


def fit_spherical(exp: ExperimentalVariogram, *, nugget: float | None = None) -> VariogramModel:
    """Fit nugget + one isotropic spherical structure (pair-weighted least squares, grid search)."""
    ok = (exp.pairs > 0) & np.isfinite(exp.gamma)
    h, g, w = exp.mean_distance[ok], exp.gamma[ok], exp.pairs[ok].astype(float)
    if h.size < 2:
        raise EstimationError("too few lag classes with pairs to fit a variogram")
    best: tuple[float, float, float, float] | None = None
    for a in np.linspace(h.min() * 0.5, h.max() * 1.5, 120):
        r = np.minimum(h / a, 1.0)
        shape = 1.5 * r - 0.5 * r**3
        # Weighted linear least squares for (c0, c) given range a.
        design = np.column_stack([np.ones_like(shape), shape]) if nugget is None else shape[:, None]
        sw = np.sqrt(w)
        target = g - (0.0 if nugget is None else nugget)
        coef, *_ = np.linalg.lstsq(design * sw[:, None], target * sw, rcond=None)
        c0 = float(coef[0]) if nugget is None else nugget
        c = float(coef[-1])
        c0, c = float(max(c0, 0.0)), float(max(c, 1e-12))
        err: float = float((w * (c0 + c * shape - g) ** 2).sum())
        if best is None or err < best[0]:
            best = (err, c0, c, float(a))
    assert best is not None
    _, nug, sill, rng = best
    return VariogramModel(nug, (Structure(ModelType.SPHERICAL, sill, Ellipsoid.isotropic(rng)),))
