"""Block model calculator."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from minecore.blockmodel import BlockModel, BlockModelError, Grid
from minecore.blockmodel.calc import calculate, evaluate
from minecore.project import Project

A = np.array([1.0, 2.0, np.nan, 4.0])
B = np.array([0.5, 0.0, 1.0, 3.0])


def test_arithmetic_functions_and_conditions() -> None:
    names = {"a": A, "b": B}
    assert np.allclose(evaluate("a * 2 + b ** 2", names, 4), A * 2 + B**2, equal_nan=True)
    out = evaluate("a / b", names, 4)
    assert np.isnan(out[1])  # division by zero becomes missing, not inf
    assert evaluate("where(a >= 2, 1, 0)", names, 4).tolist() == [0.0, 1.0, 0.0, 1.0]
    assert evaluate("1 if 1 < a <= 2 else 0", names, 4).tolist() == [0.0, 1.0, 0.0, 0.0]
    assert evaluate("max(a, b, 3)", names, 4)[[0, 3]].tolist() == [3.0, 4.0]
    assert evaluate("isnan(a) or b > 2", names, 4).tolist() == [0.0, 0.0, 1.0, 1.0]
    assert evaluate("7", names, 4).tolist() == [7.0] * 4


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os')",
        "a.real",
        "open('x')",
        "[1, 2]",
        "lambda: 1",
        "c + 1",
        "'text'",
        "where(a, x=1)",
        "a +",
    ],
)
def test_rejected_expressions(expr: str) -> None:
    with pytest.raises(BlockModelError):
        evaluate(expr, {"a": A}, 4)


@given(st.floats(-1e6, 1e6), st.floats(-1e6, 1e6))
def test_matches_python_arithmetic(x: float, y: float) -> None:
    out = evaluate("(a - b) * 3 + a / 2", {"a": np.array([x]), "b": np.array([y])}, 1)
    assert out[0] == pytest.approx((x - y) * 3 + x / 2)


def test_calculate_on_model(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "c.mcproj", "c")
    model = BlockModel.create(project, "BM", Grid((0.0, 0.0, 100.0), (10.0, 10.0, 5.0), (2, 2, 1)))
    model.set_attributes({"cu": np.array([0.5, 1.0, 0.2, np.nan])}, {"cu": {"unit": "%"}}, "cu")
    values = calculate(model, "cu_ppm", "cu * 10000", unit="ppm")
    assert values[:3].tolist() == [5000.0, 10000.0, 2000.0]
    assert BlockModel.open(project, "BM").attributes["cu_ppm"]["expression"] == "cu * 10000"
    calculate(model, "deep", "z < 105")
    assert model.values("deep").tolist() == [1.0, 1.0, 1.0, 1.0]
    with pytest.raises(BlockModelError):
        calculate(model, "x", "1")
    with pytest.raises(BlockModelError):
        calculate(model, "bad name", "1")
    project.close()
