"""Block economic values for pit optimisation.

For a block of ``t`` tonnes at grade ``g``:

* metal mass (kg) = ``t * 1000 * g * f`` where ``f`` is the grade unit's mass
  fraction factor (``%`` = 0.01, ``g/t`` = ``ppm`` = 1e-6);
* recovered metal in price units = metal mass * recovery / unit mass;
* revenue = recovered metal * payable * (price * revenue_factor - selling cost)
  * (1 - royalty): ``payable`` is the fraction of recovered metal the buyer
  pays for (smelter terms), ``royalty`` a share of the sales revenue paid to
  the state or a landowner (net smelter return royalty);
* mining recovery ``MR`` and dilution ``D`` (fractions) apply to ore: the
  plant receives ``feed = t * MR * (1 + D)`` tonnes at grade ``g / (1 + D)``
  (dilution is zero-grade rock from the surrounding waste, which is mined and
  paid for anyway), and recovered metal is ``MR`` times the in-situ metal;
* the block is *ore* when revenue exceeds the processing cost
  ``feed * processing_cost``; then value = revenue - processing - mining,
  otherwise value = -mining (waste);
* mining cost per tonne grows by ``mining_cost_per_bench`` for each bench
  below the top of the model (haulage depth increment).

NSR mode (``nsr=True``): the attribute is a net smelter return per tonne of
in-situ rock (currency/t, after metallurgical recovery, payables and selling
costs, e.g. computed for several elements with the block calculator). Then
revenue = ``t * MR * nsr * revenue_factor * (1 - royalty)``; price, price
unit, recovery, payable and selling cost are not used (the NSR contains
them), and "metal" is the recovered value at factor 1.

Process routes (optional ``routes``): alternative destinations, e.g. a heap
leach next to the mill, each with its own metallurgical recovery and
processing cost per feed tonne. The main recovery and processing cost are
route 1; extra routes are 2, 3, ... Each block goes to the route with the
largest margin ``revenue_r - processing_r`` and is ore when that margin is
positive (``route`` = 0 marks waste). Routes are not available in NSR mode
(the NSR already contains a recovery).

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
class Route:
    name: str
    recovery: float
    processing_cost: float  # per feed tonne

    def __post_init__(self) -> None:
        if not 0 < self.recovery <= 1:
            raise PitOptError(f"route {self.name!r}: recovery must be in (0, 1]")
        if self.processing_cost < 0:
            raise PitOptError(f"route {self.name!r}: processing cost must be >= 0")


def parse_routes(text: str) -> tuple[Route, ...]:
    """Routes from ``"name:recovery:cost"`` items separated by ``;``.

    Recovery may be a fraction (0.6) or a percentage (60).
    """
    out = []
    for item in text.split(";"):
        if not item.strip():
            continue
        parts = item.split(":")
        try:
            name, rec, cost = parts[0].strip(), float(parts[1]), float(parts[2])
        except (IndexError, ValueError):
            raise PitOptError(f"route {item.strip()!r} is not name:recovery:cost") from None
        if len(parts) != 3 or not name:
            raise PitOptError(f"route {item.strip()!r} is not name:recovery:cost")
        out.append(Route(name, rec / 100.0 if rec > 1 else rec, cost))
    return tuple(out)


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
    nsr: bool = False
    routes: tuple[Route, ...] = ()  # extra process routes (route 1 is the main one)
    payable: float = 1.0  # fraction of recovered metal paid for
    royalty: float = 0.0  # fraction of sales revenue

    def __post_init__(self) -> None:
        if not 0 < self.payable <= 1:
            raise PitOptError("payable fraction must be in (0, 1]")
        if not 0 <= self.royalty < 1:
            raise PitOptError("royalty must be in [0, 1)")
        if self.routes and self.nsr:
            raise PitOptError("process routes are not available in NSR mode")
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
        data["routes"] = [asdict(r) for r in self.routes]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Economics:
        routes = tuple(Route(**r) for r in data.get("routes", ()))
        return cls(**{**data, "price_unit": PriceUnit(data["price_unit"]), "routes": routes})


@dataclass(frozen=True)
class BlockEconomics:
    tonnes: Floats
    metal: Floats  # recovered metal in price units
    value: Floats  # at the requested revenue factor
    ore: npt.NDArray[np.bool_]
    mining: Floats  # mining cost of each block (always paid)
    feed: Floats  # plant feed tonnes if the block is ore (0 for waste)
    feed_grade: Floats  # diluted grade of the feed
    route: npt.NDArray[np.int64]  # 0 waste, 1 main route, 2.. extra routes
    revenue: Floats  # sales revenue after payables and royalty if ore (0 for waste)


def block_values(  # noqa: PLR0913, PLR0917 - one value per input array
    grade: Floats,
    grade_unit: str | None,
    tonnes: Floats,
    bench_from_top: npt.NDArray[np.int64],
    econ: Economics,
    revenue_factor: float = 1.0,
) -> BlockEconomics:
    """Economic value of each block (missing grade counts as waste rock)."""
    g = np.where(np.isfinite(grade), np.maximum(grade, 0.0), 0.0)
    mr, dil = econ.mining_recovery, econ.dilution
    keep = 1.0 - econ.royalty
    if econ.nsr:
        metal = tonnes * mr * g
        revenue = metal * revenue_factor * keep
        per_metal = 0.0
    else:
        if grade_unit is None:
            raise PitOptError("the grade attribute has no unit; set % or g/t to value metal")
        fraction = Unit(grade_unit).si_factor
        contained = tonnes * 1000.0 * g * fraction * mr / econ.price_unit.kilograms
        metal = contained * econ.recovery
        per_metal = econ.payable * (econ.price * revenue_factor - econ.selling_cost) * keep
        revenue = metal * per_metal
    mining = tonnes * (econ.mining_cost + econ.mining_cost_per_bench * bench_from_top)
    feed = tonnes * mr * (1.0 + dil)
    processing = feed * econ.processing_cost
    margin = revenue - processing
    route = np.ones(np.shape(margin), np.int64)
    for k, r in enumerate(econ.routes, start=2):
        metal_r = contained * r.recovery
        revenue_r = metal_r * per_metal
        margin_r = revenue_r - feed * r.processing_cost
        better = margin_r > margin
        margin = np.where(better, margin_r, margin)
        metal = np.where(better, metal_r, metal)
        revenue = np.where(better, revenue_r, revenue)
        route[better] = k
    ore = margin > 0
    route[~ore] = 0
    value = np.where(ore, margin - mining, -mining)
    return BlockEconomics(
        tonnes,
        metal,
        value.astype(np.float64),
        ore,
        mining,
        np.where(ore, feed, 0.0),
        g / (1.0 + dil),
        route,
        np.where(ore, revenue, 0.0).astype(np.float64),
    )
