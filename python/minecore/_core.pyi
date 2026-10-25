"""Type stubs for the native extension built from crates/mc-py."""

from typing import ClassVar, final

import numpy as np
import numpy.typing as npt

def core_version() -> str:
    """Return the version of the Rust core engine."""

def core_build_profile() -> str:
    """Return the compilation profile of the Rust core engine ("debug" or "release")."""

class MineCoreError(Exception):
    """Base class of all MineCore errors."""

class UnitError(MineCoreError):
    """Invalid unit, unit syntax or unit conversion."""

@final
class Unit:
    """A unit of measure, parsed from text such as "t/m3", "g/t" or "deg"."""

    def __init__(self, expression: str) -> None: ...
    @property
    def symbol(self) -> str: ...
    @property
    def dimension(self) -> str: ...
    @property
    def si_factor(self) -> float: ...
    @property
    def exact_si_factor(self) -> tuple[int, int, int]: ...
    def is_equivalent(self, other: Unit | str) -> bool: ...
    def exact_factor_to(self, target: Unit | str) -> tuple[int, int, int]: ...
    def factor_to(self, target: Unit | str) -> float: ...
    def convert(self, value: float, target: Unit | str) -> float: ...
    def __eq__(self, other: object) -> bool: ...
    def __hash__(self) -> int: ...

@final
class QuantityKind:
    """Engineering meaning of a numeric attribute."""

    LENGTH: ClassVar[QuantityKind]
    AREA: ClassVar[QuantityKind]
    VOLUME: ClassVar[QuantityKind]
    MASS: ClassVar[QuantityKind]
    DENSITY: ClassVar[QuantityKind]
    GRADE: ClassVar[QuantityKind]
    RATIO: ClassVar[QuantityKind]
    ANGLE: ClassVar[QuantityKind]
    DURATION: ClassVar[QuantityKind]

    @staticmethod
    def from_name(name: str) -> QuantityKind: ...
    @property
    def name(self) -> str: ...
    @property
    def dimension(self) -> str: ...
    @property
    def default_unit(self) -> Unit | None: ...
    def accepts(self, unit: Unit | str) -> bool: ...
    def check(self, unit: Unit | str) -> None: ...
    def __eq__(self, other: object) -> bool: ...
    def __hash__(self) -> int: ...

class CoordinateError(MineCoreError):
    """Invalid local grid, control points or coordinate arrays."""

_Floats = npt.NDArray[np.float64]

@final
class LocalGrid:
    """Local mine grid: 2D similarity plus vertical offset relative to a base projected CRS."""

    def __init__(
        self,
        *,
        base_origin: tuple[float, float],
        local_origin: tuple[float, float],
        rotation_deg: float,
        scale: float = 1.0,
        z_offset: float = 0.0,
    ) -> None: ...
    @property
    def base_origin(self) -> tuple[float, float]: ...
    @property
    def local_origin(self) -> tuple[float, float]: ...
    @property
    def rotation_deg(self) -> float: ...
    @property
    def scale(self) -> float: ...
    @property
    def z_offset(self) -> float: ...
    def to_local(
        self, x: npt.ArrayLike, y: npt.ArrayLike, z: npt.ArrayLike | None = None
    ) -> tuple[_Floats, _Floats, _Floats | None]: ...
    def to_base(
        self, x: npt.ArrayLike, y: npt.ArrayLike, z: npt.ArrayLike | None = None
    ) -> tuple[_Floats, _Floats, _Floats | None]: ...
    def __eq__(self, other: object) -> bool: ...

@final
class GridFit:
    """Result of fit_local_grid: the fitted grid and its quality figures."""

    @property
    def grid(self) -> LocalGrid: ...
    @property
    def residuals(self) -> list[tuple[float, float, float | None]]: ...
    @property
    def rms_horizontal(self) -> float: ...
    @property
    def max_horizontal(self) -> float: ...
    @property
    def sigma0_horizontal(self) -> float | None: ...
    @property
    def vertical_points(self) -> int: ...
    @property
    def rms_vertical(self) -> float | None: ...

def fit_local_grid(
    base_xy: npt.ArrayLike,
    local_xy: npt.ArrayLike,
    *,
    base_z: npt.ArrayLike | None = None,
    local_z: npt.ArrayLike | None = None,
    scale: float | None = None,
    z_offset: float | None = None,
) -> GridFit:
    """Fit a local grid to control points by least squares."""

class OptimisationError(MineCoreError):
    """Invalid input to an optimisation solver."""

def max_closure(values: npt.ArrayLike, precedence: npt.ArrayLike) -> npt.NDArray[np.bool_]:
    """Smallest maximum-weight closure: True for nodes inside the ultimate pit."""
