"""Estimate validation: leave-one-hole-out cross-validation and swath plots."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore.blockmodel.model import BlockModel, BlockModelError
from minecore.blockmodel.workflow import SampleSet
from minecore.estimation import Method, Search, VariogramModel, estimate
from minecore.jobs import Progress

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class CrossValidation:
    true: Floats
    estimate: Floats
    n: int
    unestimated: int
    mean_true: float
    mean_estimate: float
    mean_error: float
    mse: float
    correlation: float
    slope: float  # regression of true on estimate (1 = conditionally unbiased)


def cross_validate(  # noqa: PLR0913 - options are keyword-only
    samples: SampleSet,
    *,
    method: Method,
    search: Search,
    variogram: VariogramModel | None = None,
    power: float = 2.0,
    progress: Progress | None = None,
) -> CrossValidation:
    """Re-estimate every sample without the samples of its own hole."""
    result = estimate(
        samples.xyz,
        samples.values,
        samples.xyz,
        search,
        method,
        variogram=variogram,
        power=power,
        hole_ids=samples.hole_index,
        exclude_hole=samples.hole_index,
        progress=progress,
    )
    ok = np.isfinite(result.value)
    t, e = samples.values[ok], result.value[ok]
    if len(t) >= 2 and np.std(e) > 0 and np.std(t) > 0:
        corr = float(np.corrcoef(t, e)[0, 1])
        slope = float(np.cov(t, e)[0, 1] / np.var(e, ddof=1))
    else:
        corr = slope = math.nan
    err = e - t
    return CrossValidation(
        samples.values,
        result.value,
        int(ok.sum()),
        int((~ok).sum()),
        float(t.mean()) if len(t) else math.nan,
        float(e.mean()) if len(e) else math.nan,
        float(err.mean()) if len(err) else math.nan,
        float((err**2).mean()) if len(err) else math.nan,
        corr,
        slope,
    )


@dataclass(frozen=True)
class SwathRow:
    centre: float
    samples: int
    sample_mean: float
    blocks: int
    block_mean: float


def swath(
    samples: SampleSet, model: BlockModel, attribute: str, axis: int, width: float
) -> tuple[SwathRow, ...]:
    """Mean sample grade and mean block estimate in slices along ``axis`` (0=X, 1=Y, 2=Z)."""
    if axis not in (0, 1, 2) or not width > 0:
        raise BlockModelError("swath needs axis 0, 1 or 2 and width > 0")
    grid = model.grid
    blocks = grid.centroids()[:, axis]
    values = model.values(attribute)
    known = np.isfinite(values)
    lo = min(float(samples.xyz[:, axis].min()), float(blocks[known].min()) if known.any() else 0.0)
    hi = max(float(samples.xyz[:, axis].max()), float(blocks[known].max()) if known.any() else 0.0)
    start = math.floor(lo / width) * width
    edges = np.arange(start, hi + width, width)
    s_bin = np.digitize(samples.xyz[:, axis], edges) - 1
    b_bin = np.digitize(blocks, edges) - 1
    rows = []
    for i in range(len(edges) - 1):
        s_sel = s_bin == i
        b_sel = (b_bin == i) & known
        if not s_sel.any() and not b_sel.any():
            continue
        rows.append(
            SwathRow(
                float(edges[i] + width / 2),
                int(s_sel.sum()),
                float(samples.values[s_sel].mean()) if s_sel.any() else math.nan,
                int(b_sel.sum()),
                float(values[b_sel].mean()) if b_sel.any() else math.nan,
            )
        )
    return tuple(rows)
