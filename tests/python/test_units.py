"""Python-side contract tests for minecore.units.

Reference values are legal unit definitions, independent of the implementation.
"""

from __future__ import annotations

import math
from fractions import Fraction

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from minecore import MineCoreError
from minecore.units import QuantityKind, Unit, UnitError, convert

LENGTHS = ["m", "mm", "cm", "km", "in", "ft", "usft"]
GRADES = ["%", "ppm", "ppb", "g/t", "opt", "ozt/t", "frac", "mg/kg"]


def exact(from_unit: str, to_unit: str) -> Fraction:
    num, den, pi_exp = Unit(from_unit).exact_factor_to(to_unit)
    assert pi_exp == 0
    return Fraction(num, den)


def test_exact_factors_match_legal_definitions() -> None:
    assert exact("ft", "m") == Fraction("0.3048")
    assert exact("usft", "m") == Fraction(1200, 3937)
    assert exact("opt", "ppm") == Fraction(480, 14_000_000) * 1_000_000  # grains
    assert exact("ozt/t", "g/t") == Fraction("31.1034768")
    assert exact("lb", "kg") == Fraction("0.45359237")


@pytest.mark.parametrize(
    ("value", "src", "dst", "expected"),
    [
        (1.0, "ft", "m", 0.3048),
        (2.65, "g/cm3", "t/m3", 2.65),
        (1.5, "%", "ppm", 15_000.0),
        (90.0, "deg", "gon", 100.0),
        (1.0, "Mt", "t", 1_000_000.0),
    ],
)
def test_scalar_conversion(value: float, src: str, dst: str, expected: float) -> None:
    assert convert(value, src, dst) == pytest.approx(expected, rel=4e-16, abs=0.0)


def test_equivalent_units_convert_with_factor_exactly_one() -> None:
    assert Unit("g/t").factor_to("ppm") == 1.0
    assert Unit("t/m3").factor_to(Unit("kg/L")) == 1.0
    assert Unit("ppm").is_equivalent("mg/kg")


def test_array_conversion_returns_new_float_array_and_keeps_nan() -> None:
    source = np.array([0.5, 1.0, np.nan])
    result = convert(source, "%", "ppm")
    assert result.dtype == np.float64
    assert result is not source
    np.testing.assert_array_equal(result[:2], [5_000.0, 10_000.0])
    assert math.isnan(result[2])
    np.testing.assert_array_equal(source[:2], [0.5, 1.0])  # input untouched


def test_identity_conversion_still_copies() -> None:
    source = np.array([1.0, 2.0])
    result = convert(source, "g/t", "ppm")
    result[0] = 99.0
    assert source[0] == 1.0


def test_symbols_are_canonical() -> None:
    assert Unit("t/m³").symbol == "t/m3"
    assert Unit("°").symbol == "deg"
    assert Unit("GPT").symbol == "g/t"
    assert repr(Unit("kg/m^3")) == "Unit('kg/m3')"
    assert Unit("t/m3").dimension == "L^-3 M"
    assert Unit("%").dimension == "1"


@pytest.mark.parametrize("text", ["ton", "oz", "ppt", "furlong", "", "m^", "kg m"])
def test_invalid_units_raise_unit_error(text: str) -> None:
    with pytest.raises(UnitError):
        Unit(text)


def test_ambiguous_ton_explains_alternatives() -> None:
    with pytest.raises(UnitError, match="short ton"):
        Unit("ton")


def test_dimension_mismatch_raises() -> None:
    with pytest.raises(UnitError, match="cannot convert"):
        convert(1.0, "m", "t")
    with pytest.raises(UnitError):
        Unit("deg").factor_to("frac")


def test_unit_error_is_a_minecore_error() -> None:
    assert issubclass(UnitError, MineCoreError)


def test_units_are_hashable_values() -> None:
    assert Unit("t/m3") == Unit("t/m³")
    assert len({Unit("t/m3"), Unit("t/m³"), Unit("g/cm3")}) == 2


def test_quantity_kinds() -> None:
    assert QuantityKind.from_name("density") == QuantityKind.DENSITY
    assert QuantityKind.DENSITY.default_unit == Unit("t/m3")
    assert QuantityKind.GRADE.default_unit is None
    assert QuantityKind.GRADE.accepts("g/t")
    assert not QuantityKind.DENSITY.accepts("g/t")
    with pytest.raises(UnitError, match="not valid for kind 'density'"):
        QuantityKind.DENSITY.check("%")
    with pytest.raises(UnitError):
        QuantityKind.from_name("tonnage")


@given(
    value=st.floats(min_value=-1e9, max_value=1e9, allow_nan=False),
    a=st.sampled_from(LENGTHS),
    b=st.sampled_from(LENGTHS),
)
def test_length_round_trip(value: float, a: str, b: str) -> None:
    back = convert(convert(value, a, b), b, a)
    assert back == pytest.approx(value, rel=4.5e-16, abs=1e-300)


@given(a=st.sampled_from(GRADES), b=st.sampled_from(GRADES), c=st.sampled_from(GRADES))
def test_grade_factors_compose_exactly(a: str, b: str, c: str) -> None:
    assert exact(a, b) * exact(b, c) == exact(a, c)
