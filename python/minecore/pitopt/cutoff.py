"""Cut-off grade optimisation over the life of mine (Lane 1964, 1988).

Each year the cut-off maximises Lane's value per tonne of material

    v(c) = (price - selling) * y * metal(c) - h * x(c) - m - (f + F) * T(c)

where ``x(c)`` is the ore fraction above the cut-off, ``metal(c)`` the
contained metal per tonne of material sent to the plant, ``T(c)`` the time
one tonne of material takes under the binding capacity

    T(c) = max(1 / M, x(c) / H, y * metal(c) / K)

and ``F = d * V`` the opportunity cost of the remaining value ``V``. The
maximum is found numerically over every distinct grade of the reserve (exact
for a discrete grade-tonnage distribution), not with the six limiting and
balancing cut-offs in closed form; for continuous distributions both give
the same answer. Depletion follows Lane: the remaining reserve keeps the
same grade distribution, only its tonnage falls. ``V`` per year is iterated
to convergence (backward NPV of the schedule).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore._core import Unit
from minecore.blockmodel import BlockModel
from minecore.pitopt.economics import Economics, PitOptError

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class LaneInputs:
    price: float  # per metal unit
    selling: float  # per metal unit
    recovery: float  # metallurgical recovery y
    mining: float  # m, per tonne of material
    processing: float  # h, per tonne of ore
    fixed: float  # f, per year
    mine_capacity: float  # M, t of material per year
    mill_capacity: float  # H, t of ore per year
    market_capacity: float = math.inf  # K, metal units per year
    discount: float = 0.10

    def __post_init__(self) -> None:
        if min(self.mine_capacity, self.mill_capacity, self.market_capacity) <= 0:
            raise PitOptError("capacities must be > 0")
        if not 0 < self.recovery <= 1 or self.discount < 0 or self.price <= self.selling:
            raise PitOptError("recovery in (0, 1], discount >= 0 and price > selling cost")


@dataclass(frozen=True)
class LaneYear:
    year: int
    cutoff: float
    material_t: float
    ore_t: float
    grade: float  # mean grade of the ore
    metal: float  # recovered metal units
    cash: float
    npv_remaining: float  # V at the start of the year
    limit: str  # "mine", "mill" or "market"


@dataclass(frozen=True)
class LaneResult:
    years: tuple[LaneYear, ...]
    npv: float
    iterations: int


class _Reserve:
    """Grade-tonnage curve of the reserve, per tonne of material."""

    def __init__(self, grade: Floats, tonnes: Floats, metal_per_grade: float) -> None:
        g = np.where(np.isfinite(grade), np.maximum(grade, 0.0), 0.0)  # missing = waste
        ok = np.asarray(tonnes) > 0
        g, t = g[ok], np.asarray(tonnes, np.float64)[ok]
        order = np.argsort(-g)
        self.g = g[order]
        share = t[order] / t.sum()
        self.x = np.cumsum(share)  # ore fraction when the cut-off is self.g[i]
        self.q = np.cumsum(share * self.g) * metal_per_grade  # metal per t of material
        self.total = float(t.sum())
        self.metal_per_grade = metal_per_grade
        if self.total <= 0:
            raise PitOptError("empty reserve")

    def candidates(self) -> tuple[Floats, Floats, Floats]:
        """(cut-off, ore fraction, metal per t material) incl. the all-waste option."""
        return (
            np.r_[np.inf, self.g],
            np.r_[0.0, self.x],
            np.r_[0.0, self.q],
        )


def _best(inp: LaneInputs, res: _Reserve, opportunity: float) -> tuple[int, Floats]:
    _, x, q = res.candidates()
    metal = inp.recovery * q
    time = np.maximum.reduce(
        [
            np.full_like(x, 1.0 / inp.mine_capacity),
            x / inp.mill_capacity,
            metal / inp.market_capacity,
        ]
    )
    v = (inp.price - inp.selling) * metal - inp.processing * x - inp.mining
    v = v - (inp.fixed + opportunity) * time
    return int(np.argmax(v)), time


@dataclass(frozen=True)
class _Year:
    cutoff: float
    material: float
    ore: float
    grade: float
    metal: float
    cash: float
    limit: str


def _year(inp: LaneInputs, res: _Reserve, remaining: float, value: float) -> _Year:
    c, x, q = res.candidates()
    k, time = _best(inp, res, inp.discount * value)
    material = min(remaining, 1.0 / time[k])
    ore = material * x[k]
    metal = inp.recovery * material * q[k]
    cash = (
        (inp.price - inp.selling) * metal
        - inp.processing * ore
        - inp.mining * material
        - inp.fixed * material * time[k]
    )
    limits = (
        1.0 / inp.mine_capacity,
        x[k] / inp.mill_capacity,
        inp.recovery * q[k] / inp.market_capacity,
    )
    grade = q[k] / x[k] / res.metal_per_grade if x[k] > 0 else math.nan
    return _Year(
        float(c[k]),
        material,
        ore,
        grade,
        metal,
        cash,
        ("mine", "mill", "market")[int(np.argmax(limits))],
    )


def optimise_cutoffs(  # noqa: PLR0913 - options are keyword-only
    inp: LaneInputs,
    grade: Floats,
    tonnes: Floats,
    *,
    metal_per_grade: float = 1.0,
    max_iterations: int = 100,
    max_years: int = 200,
) -> LaneResult:
    """Lane schedule for a reserve given as block grades and tonnes.

    ``metal_per_grade`` converts grade x tonnes into the metal unit the price
    refers to (e.g. g/t gold priced per ounce: 1 / 31.1035).
    """
    res = _Reserve(grade, tonnes, metal_per_grade)
    values: list[float] = []
    rows: list[_Year] = []
    iterations = 0
    while iterations < max_iterations:
        iterations += 1
        remaining, rows = res.total, []
        while remaining > 1e-9 * res.total and len(rows) < max_years:
            row = _year(inp, res, remaining, values[len(rows)] if len(rows) < len(values) else 0.0)
            rows.append(row)
            remaining -= row.material
        # Backward NPV, cash at the end of each year: V_t = (cash_t + V_t+1) / (1 + d).
        new = [0.0] * (len(rows) + 1)
        for t in range(len(rows) - 1, -1, -1):
            new[t] = (rows[t].cash + new[t + 1]) / (1.0 + inp.discount)
        done = len(new) == len(values) and np.allclose(new, values, rtol=1e-9, atol=1e-6)
        values = new
        if done:
            break
    years = tuple(
        LaneYear(t + 1, r.cutoff, r.material, r.ore, r.grade, r.metal, r.cash, values[t], r.limit)
        for t, r in enumerate(rows)
    )
    return LaneResult(years, values[0], iterations)


def constant_cutoff_npv(
    inp: LaneInputs, grade: Floats, tonnes: Floats, cutoff: float, *, metal_per_grade: float = 1.0
) -> float:
    """NPV when the same cut-off is used every year (for comparison)."""
    res = _Reserve(grade, tonnes, metal_per_grade)
    _, x, q = res.candidates()
    k = int(np.count_nonzero(res.g >= cutoff))  # candidate index: k richest grades are ore
    metal_t = inp.recovery * q[k]
    time = max(1.0 / inp.mine_capacity, x[k] / inp.mill_capacity, metal_t / inp.market_capacity)
    per_t = (inp.price - inp.selling) * metal_t - inp.processing * x[k] - inp.mining
    per_t -= inp.fixed * time
    remaining, npv, year = res.total, 0.0, 0
    while remaining > 1e-9 * res.total:
        year += 1
        material = min(remaining, 1.0 / time)
        npv += material * per_t / (1.0 + inp.discount) ** year
        remaining -= material
    return npv


def cutoffs_from_model(  # noqa: PLR0913 - options are keyword-only
    model: BlockModel,
    grade_attribute: str,
    econ: Economics,
    *,
    mine_capacity: float,
    mill_capacity: float,
    fixed: float = 0.0,
    market_capacity: float = math.inf,
    discount: float = 0.10,
    last_shell: int | None = None,
    rock_attribute: str | None = None,
) -> LaneResult:
    """Lane schedule for the blocks inside the optimised pit (``pit_shell``).

    Mining recovery, dilution and bench costs are not applied (Lane works on
    the in-situ grade distribution of the pit); NSR models use a price of 1
    per NSR unit. Payables and royalty scale the net metal price.
    Co-products enter as a main-metal equivalent grade,
    ``g_eq = g + sum_m g_m * v_m / v`` with ``v`` the net value per grade unit
    (contained metal per grade unit x recovery x payable x (price - selling));
    Lane's cash is linear in the recovered metal, so this is exact.
    """
    if "pit_shell" not in model.attributes:
        raise PitOptError(f"{model.name} has no pit_shell; run the pit optimisation first")
    shell = model.values("pit_shell")
    last = last_shell or int(np.nanmax(shell))
    inside = np.isfinite(shell) & (shell >= 1) & (shell <= last)
    if isinstance(econ.density, str):
        rho = np.nan_to_num(model.values(econ.density), nan=0.0)
    else:
        rho = np.full(model.grid.blocks, float(econ.density))
    if rock_attribute:
        rho = np.where(model.values(rock_attribute) > 0.5, rho, 0.0)
    tonnes = np.where(inside, rho * model.grid.block_volume, 0.0)
    if econ.nsr:
        per_grade, price, selling, recovery = 1.0, 1.0, 0.0, 1.0
    else:
        unit = model.attributes[grade_attribute].get("unit")
        if unit is None:
            raise PitOptError("the grade attribute has no unit; set % or g/t to value metal")
        per_grade = 1000.0 * Unit(unit).si_factor / econ.price_unit.kilograms
        price, selling, recovery = econ.price, econ.selling_cost, econ.recovery
    # Payables and royalty scale the net price per metal unit, as in the block value.
    share = (1.0 if econ.nsr else econ.payable) * (1.0 - econ.royalty)
    price, selling = price * share, selling * share
    inputs = LaneInputs(
        price,
        selling,
        recovery,
        econ.mining_cost,
        econ.processing_cost,
        fixed,
        mine_capacity,
        mill_capacity,
        market_capacity,
        discount,
    )
    grade = np.nan_to_num(model.values(grade_attribute), nan=0.0)
    if econ.coproducts:
        main = per_grade * econ.recovery * econ.payable * (econ.price - econ.selling_cost)
        for p in econ.coproducts:
            unit_m = model.attributes[p.attribute].get("unit")
            if unit_m is None:
                raise PitOptError(f"co-product attribute {p.attribute!r} has no unit")
            per_m = 1000.0 * Unit(unit_m).si_factor / p.price_unit.kilograms
            value_m = per_m * p.recovery * p.payable * (p.price - p.selling_cost)
            grade = grade + np.nan_to_num(model.values(p.attribute), nan=0.0) * value_m / main
    return optimise_cutoffs(inputs, grade, tonnes, metal_per_grade=per_grade)
