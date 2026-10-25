"""Block economic values for pit optimisation.

For a block of ``t`` tonnes at grade ``g``:

* metal mass (kg) = ``t * 1000 * g * f`` where ``f`` is the grade unit's mass
  fraction factor (``%`` = 0.01, ``g/t`` = ``ppm`` = 1e-6);
* recovered payable metal in price units = metal mass * recovery / unit mass;
* revenue = payable metal * (price * revenue_factor - selling cost);
* mining recovery ``MR`` and dilution ``D`` (fractions) apply to ore: the
  plant receives ``feed = t * MR * (1 + D)`` tonnes at grade ``g / (1 + D)``
  (dilution is zero-grade rock from the surrounding waste, which is mined and
  paid for anyway), and recovered metal is ``MR`` times the in-situ metal;
* the block is *ore* when revenue exceeds the processing cost
  ``feed * processing_cost``; then value = revenue - processing - mining,
  otherwise value = -mining (waste);
* mining cost per tonne grows by ``mining_cost_per_bench`` for each bench
  below the top of the model (haulage depth increment).

Currency is whatever the user enters (e.g. USD); all values are in that unit.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import numpy.typing as npt

from minecore._core import MineCoreError, Unit

Floats = npt.NDArray[np.float64]


class PitOptError(MineCoreError):
    """Invalid pit optimisation input."""


class PriceUnit(StrEnum):
    """Mass unit the metal price refers to."""

    TROY_OUNCE = "ozt"
    GRAM = "g"
    POUND = "lb"
    TONNE = "t"

    @property
    def kilograms(self) -> float:
        return {"ozt": 0.0311034768, "g": 0.001, "lb": 0.45359237, "t": 1000.0}[self.value]


@dataclass(frozen=True)
class Economics:
    price: float
    price_unit: PriceUnit
    recovery: float
    mining_cost: float
    processing_cost: float
    selling_cost: float = 0.0
    mining_cost_per_bench: float = 0.0
    density: float | str = 2.7
    mining_recovery: float = 1.0
    dilution: float = 0.0

    def __post_init__(self) -> None:
        if not (self.price > 0 and math.isfinite(self.price)):
            raise PitOptError("metal price must be > 0")
        if not 0 < self.recovery <= 1:
            raise PitOptError("recovery must be in (0, 1]")
        for name in ("mining_cost", "processing_cost", "selling_cost", "mining_cost_per_bench"):
            if getattr(self, name) < 0:
                raise PitOptError(f"{name} must be >= 0")
        if not 0 < self.mining_recovery <= 1:
            raise PitOptError("mining recovery must be in (0, 1]")
        if not 0 <= self.dilution <= 1:
            raise PitOptError("dilution must be in [0, 1]")
        if self.selling_cost >= self.price:
            raise PitOptError("selling cost must be below the metal price")

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["price_unit"] = self.price_unit.value
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Economics:
        return cls(**{**data, "price_unit": PriceUnit(data["price_unit"])})


@dataclass(frozen=True)
class BlockEconomics:
    tonnes: Floats
    metal: Floats  # recovered metal in price units
    value: Floats  # at the requested revenue factor
    ore: npt.NDArray[np.bool_]
    mining: Floats  # mining cost of each block (always paid)
    feed: Floats  # plant feed tonnes if the block is ore (0 for waste)
    feed_grade: Floats  # diluted grade of the feed


def block_values(  # noqa: PLR0913, PLR0917 - one value per input array
    grade: Floats,
    grade_unit: str | None,
    tonnes: Floats,
    bench_from_top: npt.NDArray[np.int64],
    econ: Economics,
    revenue_factor: float = 1.0,
) -> BlockEconomics:
    """Economic value of each block (missing grade counts as waste rock)."""
    if grade_unit is None:
        raise PitOptError("the grade attribute has no unit; set % or g/t to value metal")
    fraction = Unit(grade_unit).si_factor
    g = np.where(np.isfinite(grade), np.maximum(grade, 0.0), 0.0)
    mr, dil = econ.mining_recovery, econ.dilution
    metal = tonnes * 1000.0 * g * fraction * mr * econ.recovery / econ.price_unit.kilograms
    revenue = metal * (econ.price * revenue_factor - econ.selling_cost)
    mining = tonnes * (econ.mining_cost + econ.mining_cost_per_bench * bench_from_top)
    feed = tonnes * mr * (1.0 + dil)
    processing = feed * econ.processing_cost
    ore = revenue > processing
    value = np.where(ore, revenue - processing - mining, -mining)
    return BlockEconomics(
        tonnes,
        metal,
        value.astype(np.float64),
        ore,
        mining,
        np.where(ore, feed, 0.0),
        g / (1.0 + dil),
    )
