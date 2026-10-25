"""Mineable inventory inside the pit by pushback and confidence category.

Ore is plant feed after mining recovery and dilution (as in the schedule);
waste is everything else mined. Categories come from a block attribute
(e.g. ``kat_au`` from Blok Modeli > Güven kategorileri: 1 = highest
confidence, 0 = none, empty = unestimated). The table is a mineable
inventory, not a reserve statement under JORC, NI 43-101, PERC or similar
codes; that needs a Competent/Qualified Person.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore.pitopt.optimise import PitResult


@dataclass(frozen=True)
class InventoryLine:
    phase: int  # 0 = all pushbacks
    category: str  # "1", "2", "0", "-" (unestimated) or "Toplam"
    ore_t: float
    grade: float
    metal: float
    waste_t: float


def _label(value: float) -> str:
    return "-" if not math.isfinite(value) else str(round(value))


def inventory_by_category(
    result: PitResult, phase: npt.NDArray[np.int64], category: npt.NDArray[np.float64] | None
) -> list[InventoryLine]:
    """Lines per pushback and category, then all-pushback totals per category."""
    mined = phase > 0
    cats = np.full(len(phase), np.nan) if category is None else np.asarray(category, np.float64)
    labels = np.array([_label(c) for c in cats])
    order = sorted(set(labels[mined]), key=lambda s: (s == "-", s == "0", s))
    lines = []

    def line(p: int, label: str, sel: npt.NDArray[np.bool_]) -> InventoryLine:
        ore = sel & result.ore
        feed = float(result.feed[ore].sum())
        gt = float((result.feed[ore] * result.feed_grade[ore]).sum())
        return InventoryLine(
            p,
            label,
            feed,
            gt / feed if feed > 0 else math.nan,
            float(result.metal[ore].sum()),
            float(result.tonnes[sel].sum()) - feed,
        )

    for p in range(1, int(phase.max()) + 1 if mined.any() else 1):
        in_phase = phase == p
        for label in order:
            sel = in_phase & (labels == label)
            if sel.any():
                lines.append(line(p, label, sel))
        if len(order) > 1:
            lines.append(line(p, "Toplam", in_phase))
    for label in order:
        lines.append(line(0, label, mined & (labels == label)))
    lines.append(line(0, "Toplam", mined))
    return lines
