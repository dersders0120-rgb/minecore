"""Co-products and by-products: block revenue as the sum over metals, checked by hand
and against independent formulations (NSR attribute, equivalent grade)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.pitopt import (
    Economics,
    PriceUnit,
    Product,
    SlopeProfile,
    block_values,
    optimise_pit,
    parse_products,
)
from minecore.pitopt.cutoff import cutoffs_from_model
from minecore.pitopt.economics import PitOptError, Route
from minecore.project import Project

OZT_G = 31.1034768

AG = Product("ag", 25.0, PriceUnit.TROY_OUNCE, 0.7, payable=0.95, selling_cost=1.0)


def _gold(
    royalty: float = 0.0, routes: tuple[Route, ...] = (), coproducts: tuple[Product, ...] = ()
) -> Economics:
    return Economics(
        2000.0,
        PriceUnit.TROY_OUNCE,
        0.9,
        3.0,
        20.0,
        royalty=royalty,
        routes=routes,
        coproducts=coproducts,
    )


def test_revenue_is_the_sum_over_metals_by_hand() -> None:
    econ = _gold(royalty=0.03, coproducts=(AG,))
    out = block_values(
        np.array([2.0]),
        "g/t",
        np.array([1000.0]),
        np.zeros(1, np.int64),
        econ,
        coproduct_grades=[(np.array([50.0]), "g/t")],
    )
    au_oz = 1000 * 2.0 / OZT_G * 0.9
    ag_oz = 1000 * 50.0 / OZT_G * 0.7
    revenue = (au_oz * 2000.0 + ag_oz * 0.95 * (25.0 - 1.0)) * 0.97
    assert out.metal[0] == pytest.approx(au_oz)
    assert out.coproduct_metal[0, 0] == pytest.approx(ag_oz)
    assert out.revenue[0] == pytest.approx(revenue)
    assert out.value[0] == pytest.approx(revenue - 1000 * 20.0 - 1000 * 3.0)


def test_a_by_product_can_turn_waste_into_ore() -> None:
    tonnes, bench = np.array([1000.0]), np.zeros(1, np.int64)
    gold_only = block_values(np.array([0.2]), "g/t", tonnes, bench, _gold())
    assert not gold_only.ore[0]  # 0.2 g/t Au pays 11.6 $/t < 20 $/t processing
    both = block_values(
        np.array([0.2]),
        "g/t",
        tonnes,
        bench,
        _gold(coproducts=(AG,)),
        coproduct_grades=[(np.array([40.0]), "g/t")],
    )
    assert both.ore[0]
    assert both.value[0] > gold_only.value[0]


def test_each_route_has_its_own_co_product_recovery() -> None:
    # Leach: gold 60 %, silver 10 %, processing 4 $/t; mill: 90 % / 70 %, 20 $/t.
    leach = Route("leach", 0.6, 4.0)
    ag = Product("ag", 25.0, PriceUnit.TROY_OUNCE, 0.7, route_recoveries=(0.1,))
    econ = _gold(routes=(leach,), coproducts=(ag,))
    grade, silver = np.array([0.5, 0.5]), np.array([0.0, 200.0])
    out = block_values(
        grade,
        "g/t",
        np.full(2, 1000.0),
        np.zeros(2, np.int64),
        econ,
        coproduct_grades=[(silver, "g/t")],
    )
    au = 1000 * 0.5 / OZT_G * 2000.0
    ag_value = 1000 * 200.0 / OZT_G * 25.0
    mill = (au * 0.9 + ag_value * 0.7 - 20_000.0, au * 0.9 - 20_000.0)
    heap = (au * 0.6 + ag_value * 0.1 - 4000.0, au * 0.6 - 4000.0)
    # Block 0 (no silver): leach margin is higher; block 1 (silver-rich): the mill wins.
    assert heap[1] > mill[1]
    assert mill[0] > heap[0]
    assert list(out.route) == [2, 1]
    assert out.coproduct_metal[0, 0] == 0.0
    assert out.coproduct_metal[1, 0] == pytest.approx(1000 * 200.0 / OZT_G * 0.7)
    assert out.value[1] == pytest.approx(mill[0] - 3000.0)
    with pytest.raises(PitOptError, match="one recovery for each"):
        _gold(routes=(leach,), coproducts=(AG,))


def _model(tmp_path: Path) -> tuple[Project, BlockModel]:
    project = Project.create(tmp_path / "co.mcproj", "co")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (14, 10, 7))
    model = BlockModel.create(project, "m", grid)
    c = grid.centroids()
    rng = np.random.default_rng(7)
    near = np.hypot(c[:, 0] - 70, c[:, 1] - 50) < 35
    au = np.where(near, rng.uniform(0.1, 2.5, grid.blocks), 0.0)
    ag = np.where(near, rng.uniform(5.0, 120.0, grid.blocks), 0.0)
    model.set_attributes({"au": au, "ag": ag}, {"au": {"unit": "g/t"}, "ag": {"unit": "g/t"}}, "g")
    return project, model


def test_pit_equals_the_pit_on_a_hand_made_nsr_attribute(tmp_path: Path) -> None:
    project, model = _model(tmp_path)
    ag = Product("ag", 25.0, PriceUnit.TROY_OUNCE, 0.7, payable=0.95)
    econ = Economics(
        1800.0, PriceUnit.TROY_OUNCE, 0.9, 2.5, 18.0, payable=0.99, royalty=0.04, coproducts=(ag,)
    )
    # NSR per tonne of rock, before royalty (the NSR mode applies the royalty itself).
    nsr = (
        model.values("au") / OZT_G * 0.9 * 0.99 * 1800.0
        + model.values("ag") / OZT_G * 0.7 * 0.95 * 25.0
    )
    model.set_attributes({"nsr": nsr}, {"nsr": {"unit": None}}, "nsr")
    as_nsr = Economics(1.0, PriceUnit.TONNE, 1.0, 2.5, 18.0, nsr=True, royalty=0.04)
    factors = [0.6, 0.8, 1.0]
    a = optimise_pit(model, "au", econ, SlopeProfile(45.0), factors)
    b = optimise_pit(model, "nsr", as_nsr, SlopeProfile(45.0), factors)
    assert (a.shell == b.shell).all()
    assert a.value == pytest.approx(b.value)
    assert a.revenue == pytest.approx(b.revenue)
    assert a.shell.max() > 0
    inside = a.ore & (a.shell > 0)
    assert a.rows[-1].coproduct_metal[0] == pytest.approx(
        float((model.values("ag")[inside] * a.tonnes[inside]).sum() / OZT_G * 0.7)
    )
    project.close()


def test_lane_uses_the_exact_equivalent_grade(tmp_path: Path) -> None:
    project, model = _model(tmp_path)
    model.set_attributes({"pit_shell": np.ones(model.grid.blocks)}, {"pit_shell": {}}, "pit")
    ag = Product("ag", 25.0, PriceUnit.TROY_OUNCE, 0.7, payable=0.95, selling_cost=1.0)
    econ = Economics(1800.0, PriceUnit.TROY_OUNCE, 0.9, 2.5, 18.0, 5.0, coproducts=(ag,))
    ratio = (0.7 * 0.95 * 24.0) / (0.9 * 1795.0)
    eq = model.values("au") + model.values("ag") * ratio
    model.set_attributes({"aueq": eq}, {"aueq": {"unit": "g/t"}}, "eq")
    plain = Economics(1800.0, PriceUnit.TROY_OUNCE, 0.9, 2.5, 18.0, 5.0)
    a = cutoffs_from_model(model, "au", econ, mine_capacity=2e5, mill_capacity=5e4)
    b = cutoffs_from_model(model, "aueq", plain, mine_capacity=2e5, mill_capacity=5e4)
    assert a.npv == pytest.approx(b.npv)
    assert [y.cutoff for y in a.years] == pytest.approx([y.cutoff for y in b.years])
    project.close()


def test_parse_json_and_validation() -> None:
    assert parse_products("ag:24:ozt:70:95; cu:9000:t:0.85") == (
        Product("ag", 24.0, PriceUnit.TROY_OUNCE, 0.7, 0.95),
        Product("cu", 9000.0, PriceUnit.TONNE, 0.85),
    )
    assert parse_products("ag:24:ozt:70::0.5:20/0")[0].route_recoveries == (0.2, 0.0)
    for bad in ("ag:24:ozt", "ag:x:ozt:70", "ag:24:kg:70", "ag:24:ozt:70:95:0:1:2"):
        with pytest.raises(PitOptError):
            parse_products(bad)
    econ = _gold(coproducts=(AG,))
    assert Economics.from_json(econ.to_json()) == econ
    old = _gold().to_json()
    del old["coproducts"]  # saved before co-products existed
    assert Economics.from_json(old) == _gold()
    with pytest.raises(PitOptError):
        Economics(1.0, PriceUnit.TONNE, 1.0, 1.0, 1.0, nsr=True, coproducts=(AG,))
    with pytest.raises(PitOptError):
        Product("ag", 25.0, PriceUnit.TROY_OUNCE, 0.0)
    with pytest.raises(PitOptError):
        Product("ag", 25.0, PriceUnit.TROY_OUNCE, 0.7, selling_cost=25.0)
    with pytest.raises(PitOptError, match="one grade array"):
        block_values(np.ones(1), "g/t", np.ones(1), np.zeros(1, np.int64), econ)
