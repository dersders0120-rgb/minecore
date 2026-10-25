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

Co-products and by-products (optional ``coproducts``): further metals valued
with the main one, e.g. silver with gold or gold with copper. Each has its own
grade attribute, price, price unit, metallurgical recovery, payable fraction
and selling cost, and one recovery per extra route. A block's revenue is the
sum over all metals,

    revenue_r = sum_m  contained_m * recovery_m,r * payable_m
                       * (price_m * revenue_factor - selling_m) * (1 - royalty)

and ore, route and value follow from that total as above. ``metal`` stays the
recovered main metal; the recovered co-product metal is in ``coproduct_metal``.
Co-products are not available in NSR mode (the NSR already sums the metals).

Currency is whatever the user enters (e.g. USD); all values are in that unit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
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


def _fraction(value: float) -> float:
    """A fraction given as 0.7 or as a percentage 70."""
    return value / 100.0 if value > 1 else value


@dataclass(frozen=True)
class Product:
    """A co-product or by-product: a further metal with its own grade attribute."""

    attribute: str
    price: float
    price_unit: PriceUnit
    recovery: float
    payable: float = 1.0
    selling_cost: float = 0.0
    route_recoveries: tuple[float, ...] = ()  # one per extra route, in route order

    def __post_init__(self) -> None:
        name = self.attribute
        if not name:
            raise PitOptError("a co-product needs a grade attribute")
        if not (self.price > 0 and math.isfinite(self.price)):
            raise PitOptError(f"co-product {name!r}: price must be > 0")
        if not 0 < self.recovery <= 1:
            raise PitOptError(f"co-product {name!r}: recovery must be in (0, 1]")
        if not 0 < self.payable <= 1:
            raise PitOptError(f"co-product {name!r}: payable fraction must be in (0, 1]")
        if not 0 <= self.selling_cost < self.price:
            raise PitOptError(f"co-product {name!r}: selling cost must be in [0, price)")
        if not all(0 <= r <= 1 for r in self.route_recoveries):
            raise PitOptError(f"co-product {name!r}: route recoveries must be in [0, 1]")

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["price_unit"] = self.price_unit.value
        data["route_recoveries"] = list(self.route_recoveries)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Product:
        return cls(
            **{
                **data,
                "price_unit": PriceUnit(data["price_unit"]),
                "route_recoveries": tuple(data.get("route_recoveries", ())),
            }
        )


def parse_products(text: str) -> tuple[Product, ...]:
    """Co-products from ``attribute:price:unit:recovery[:payable[:selling[:r2/r3..]]]``
    items separated by ``;``, e.g. ``"ag:24:ozt:70:95"``.

    Recovery and payable may be fractions or percentages; the last field lists
    the recovery in each extra process route, separated by ``/``.
    """
    out = []
    for item in text.split(";"):
        if not item.strip():
            continue
        parts = [p.strip() for p in item.split(":")]
        bad = PitOptError(
            f"co-product {item.strip()!r} is not attribute:price:unit:recovery"
            "[:payable[:selling[:route recoveries]]]"
        )
        if not 4 <= len(parts) <= 7:
            raise bad
        try:
            unit = PriceUnit(parts[2])
            price, recovery = float(parts[1]), _fraction(float(parts[3]))
            payable = _fraction(float(parts[4])) if len(parts) > 4 and parts[4] else 1.0
            selling = float(parts[5]) if len(parts) > 5 and parts[5] else 0.0
            routes = (
                tuple(_fraction(float(r)) for r in parts[6].split("/")) if len(parts) > 6 else ()
            )
        except ValueError:
            raise bad from None
        out.append(Product(parts[0], price, unit, recovery, payable, selling, routes))
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
    coproducts: tuple[Product, ...] = ()

    def __post_init__(self) -> None:
        self._check_coproducts()
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

    def _check_coproducts(self) -> None:
        if self.coproducts and self.nsr:
            raise PitOptError("co-products are not available in NSR mode")
        for p in self.coproducts:
            if len(p.route_recoveries) != len(self.routes):
                raise PitOptError(
                    f"co-product {p.attribute!r} needs one recovery for each of the "
                    f"{len(self.routes)} extra process routes"
                )

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["price_unit"] = self.price_unit.value
        data["routes"] = [asdict(r) for r in self.routes]
        data["coproducts"] = [p.to_json() for p in self.coproducts]
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Economics:
        routes = tuple(Route(**r) for r in data.get("routes", ()))
        products = tuple(Product.from_json(p) for p in data.get("coproducts", ()))
        return cls(
            **{
                **data,
                "price_unit": PriceUnit(data["price_unit"]),
                "routes": routes,
                "coproducts": products,
            }
        )


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
    coproduct_metal: Floats  # (blocks, co-products) recovered metal in price units


def block_values(  # noqa: PLR0913, PLR0917 - one value per input array
    grade: Floats,
    grade_unit: str | None,
    tonnes: Floats,
    bench_from_top: npt.NDArray[np.int64],
    econ: Economics,
    revenue_factor: float = 1.0,
    coproduct_grades: Sequence[tuple[Floats, str | None]] = (),
) -> BlockEconomics:
    """Economic value of each block (missing grade counts as waste rock).

    ``coproduct_grades`` holds the (grade, unit) of each ``econ.coproducts``
    item, in the same order.
    """
    if len(coproduct_grades) != len(econ.coproducts):
        raise PitOptError("one grade array is needed for each co-product")
    mr, dil = econ.mining_recovery, econ.dilution
    keep = 1.0 - econ.royalty
    g = _clean(grade)
    n = np.shape(g)[0]
    if econ.nsr:
        metal = tonnes * mr * g
        revenue = metal * revenue_factor * keep
        per_metal = 0.0
    else:
        contained = _contained(g, grade_unit, tonnes * mr, econ.price_unit, "the grade attribute")
        metal = contained * econ.recovery
        per_metal = econ.payable * (econ.price * revenue_factor - econ.selling_cost) * keep
        revenue = metal * per_metal
    co_contained, co_per = [], []
    for p, (co_grade, co_unit) in zip(econ.coproducts, coproduct_grades, strict=True):
        name = f"co-product {p.attribute!r}"
        co_contained.append(_contained(_clean(co_grade), co_unit, tonnes * mr, p.price_unit, name))
        co_per.append(p.payable * (p.price * revenue_factor - p.selling_cost) * keep)
    co_metal = np.zeros((n, len(econ.coproducts)))
    for i, p in enumerate(econ.coproducts):
        co_metal[:, i] = co_contained[i] * p.recovery
        revenue = revenue + co_metal[:, i] * co_per[i]
    mining = tonnes * (econ.mining_cost + econ.mining_cost_per_bench * bench_from_top)
    feed = tonnes * mr * (1.0 + dil)
    processing = feed * econ.processing_cost
    margin = revenue - processing
    route = np.ones(np.shape(margin), np.int64)
    for k, r in enumerate(econ.routes, start=2):
        metal_r = contained * r.recovery
        revenue_r = metal_r * per_metal
        co_r = np.zeros_like(co_metal)
        for i, p in enumerate(econ.coproducts):
            co_r[:, i] = co_contained[i] * p.route_recoveries[k - 2]
            revenue_r = revenue_r + co_r[:, i] * co_per[i]
        margin_r = revenue_r - feed * r.processing_cost
        better = margin_r > margin
        margin = np.where(better, margin_r, margin)
        metal = np.where(better, metal_r, metal)
        revenue = np.where(better, revenue_r, revenue)
        co_metal[better] = co_r[better]
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
        co_metal,
    )


def _clean(grade: Floats) -> Floats:
    return np.where(np.isfinite(grade), np.maximum(grade, 0.0), 0.0)


def _contained(
    grade: Floats, unit: str | None, rock_t: Floats, price_unit: PriceUnit, what: str
) -> Floats:
    """Metal in ``rock_t`` tonnes at ``grade``, in price units."""
    if unit is None:
        raise PitOptError(f"{what} has no unit; set % or g/t to value metal")
    contained: Floats = rock_t * 1000.0 * grade * Unit(unit).si_factor / price_unit.kilograms
    return contained
