"""Cost adjustment factors, treatment charge and concentrate penalties, by hand."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.pitopt.economics import Adjustments, Economics, Penalty, PriceUnit, block_values


def _econ(**kw: object) -> Economics:
    base: dict[str, object] = {
        "price": 9000.0,
        "price_unit": PriceUnit.TONNE,
        "recovery": 0.9,
        "mining_cost": 2.0,
        "processing_cost": 10.0,
    }
    return Economics(**{**base, **kw})  # type: ignore[arg-type]


def test_treatment_charge_and_penalty_by_hand() -> None:
    # 1000 t at 1 % Cu: 9 t recovered -> 36 t of 25 % concentrate.
    # TC 80 $/t conc = 2 880. Arsenic 500 ppm, 40 % to the concentrate:
    # 1000 * 500 * 0.4 / 36 = 5 555.6 ppm; free 2 000, steps of 1 000 at 3 $/t conc:
    # 3.5556 steps * 36 * 3 = 384. Revenue 9 * 9000 = 81 000.
    t = np.array([1000.0])
    adj = Adjustments(penalty_grades=[(np.array([500.0]), "ppm")])
    econ = _econ(
        concentrate_grade=0.25,
        treatment_charge=80.0,
        penalties=(Penalty("as", 0.4, 2000.0, 1000.0, 3.0),),
    )
    v = block_values(np.array([1.0]), "%", t, np.zeros(1, np.int64), econ, adjust=adj)
    expected = 81_000 - 2_880 - (1000 * 500 * 0.4 / 36 - 2000) / 1000 * 36 * 3
    assert v.revenue[0] == pytest.approx(expected)


def test_cost_factors_multiply_block_costs() -> None:
    t = np.array([1000.0, 1000.0])
    adj = Adjustments(np.array([1.5, np.nan]), np.array([2.0, 1.0]))
    v = block_values(np.array([1.0, 1.0]), "%", t, np.zeros(2, np.int64), _econ(), adjust=adj)
    assert v.mining == pytest.approx([3000.0, 2000.0])  # NaN factor counts as 1
    assert v.value[0] == pytest.approx(81_000 - 20_000 - 3_000)
