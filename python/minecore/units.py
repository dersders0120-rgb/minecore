"""Units of measure and quantity kinds.

The contract lives in the Rust core (``mc-core::units``); this module is its
Python face. Conversion factors come from exact rational scales, so equivalent
units (``ppm`` and ``g/t``) convert with a factor of exactly 1.

Example:
    >>> from minecore.units import Unit, convert
    >>> convert(1.0, "opt", "g/t")
    34.285714285714285
"""

from __future__ import annotations

from typing import overload

import numpy as np
from numpy.typing import ArrayLike, NDArray

from minecore._core import QuantityKind, Unit, UnitError

__all__ = ["QuantityKind", "Unit", "UnitError", "convert"]


@overload
def convert(values: float, from_unit: Unit | str, to_unit: Unit | str) -> float: ...
@overload
def convert(
    values: ArrayLike, from_unit: Unit | str, to_unit: Unit | str
) -> NDArray[np.float64]: ...
def convert(
    values: float | ArrayLike, from_unit: Unit | str, to_unit: Unit | str
) -> float | NDArray[np.float64]:
    """Convert a value or an array of values between units.

    A Python ``float`` or ``int`` returns a ``float``; anything else returns a
    new ``float64`` array (the input is never modified). NaN values stay NaN.

    Raises:
        UnitError: unknown units or different dimensions.
    """
    source = from_unit if isinstance(from_unit, Unit) else Unit(from_unit)
    factor = source.factor_to(to_unit)
    if isinstance(values, (int, float)) and not isinstance(values, bool):
        return float(values) * factor
    array = np.array(values, dtype=np.float64, copy=True)
    if factor != 1.0:
        array *= factor
    return array
