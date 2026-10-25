"""Bench blast design: pattern, charge, powder factor and a hole layout.

Geometry follows the usual bench-blasting rules of thumb, all overridable:
burden ``B = k_B x D`` (``k_B`` 25-40 for typical rock, D the hole
diameter), spacing ``S = s/b x B``, subdrill ``J = j x B`` and stemming
``T = t x B``. Charge length = bench height + subdrill - stemming; charge per
hole = column length x pi/4 D^2 x explosive density (bulk explosive filling
the hole). Each hole breaks ``B x S x H``; the powder factor is the charge
per broken volume (kg/m3) or tonne. Drilling per hole = (H + J) / cos(angle).

Layout: holes on a staggered (or square) pattern inside a closed polygon at
a given azimuth, rows parallel to the free face. Holes are written as
collar-toe lines for drill navigation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore._core import MineCoreError

Floats = npt.NDArray[np.float64]


class BlastError(MineCoreError):
    """Invalid blast design input."""


@dataclass(frozen=True)
class BlastDesign:
    diameter_mm: float = 165.0
    bench_height: float = 10.0
    burden_factor: float = 30.0  # burden / diameter
    spacing_ratio: float = 1.15  # spacing / burden
    subdrill_ratio: float = 0.3  # subdrill / burden
    stemming_ratio: float = 0.7  # stemming / burden
    explosive_density: float = 0.85  # g/cm3 (ANFO about 0.8-0.85, emulsion 1.1-1.25)
    rock_density: float = 2.7  # t/m3
    hole_angle: float = 0.0  # degrees from vertical
    burden: float | None = None  # metres, overrides the factor
    spacing: float | None = None  # metres, overrides the ratio
    explosive_cost_kg: float = 1.0  # $/kg
    drilling_cost_m: float = 25.0  # $/m drilled
    accessory_cost_hole: float = 20.0  # $/hole (detonator, booster)

    def __post_init__(self) -> None:
        if min(self.diameter_mm, self.bench_height, self.explosive_density, self.rock_density) <= 0:
            raise BlastError("diameter, bench height and densities must be > 0")
        if not 0 <= self.hole_angle < 45:
            raise BlastError("hole angle must be 0-45 degrees from vertical")
        if self.charge_length <= 0:
            raise BlastError("stemming is longer than the hole: no room for the charge")

    @property
    def b(self) -> float:
        return self.burden if self.burden else self.burden_factor * self.diameter_mm / 1000.0

    @property
    def s(self) -> float:
        return self.spacing if self.spacing else self.spacing_ratio * self.b

    @property
    def subdrill(self) -> float:
        return self.subdrill_ratio * self.b

    @property
    def stemming(self) -> float:
        return self.stemming_ratio * self.b

    @property
    def hole_length(self) -> float:
        return (self.bench_height + self.subdrill) / math.cos(math.radians(self.hole_angle))

    @property
    def charge_length(self) -> float:
        return self.hole_length - self.stemming

    @property
    def linear_charge(self) -> float:
        """kg of explosive per metre of hole."""
        d = self.diameter_mm / 1000.0
        return math.pi / 4.0 * d * d * self.explosive_density * 1000.0

    @property
    def charge_kg(self) -> float:
        return self.charge_length * self.linear_charge

    @property
    def volume_per_hole(self) -> float:
        return self.b * self.s * self.bench_height

    @property
    def tonnes_per_hole(self) -> float:
        return self.volume_per_hole * self.rock_density

    @property
    def powder_factor(self) -> float:
        """kg/m3."""
        return self.charge_kg / self.volume_per_hole

    @property
    def cost_per_hole(self) -> float:
        return (
            self.charge_kg * self.explosive_cost_kg
            + self.hole_length * self.drilling_cost_m
            + self.accessory_cost_hole
        )

    @property
    def cost_per_t(self) -> float:
        return self.cost_per_hole / self.tonnes_per_hole


@dataclass(frozen=True)
class AnnualBlasting:
    tonnes: float
    holes: int
    drill_m: float
    explosive_t: float
    cost: float


def annual(design: BlastDesign, tonnes: float) -> AnnualBlasting:
    holes = math.ceil(tonnes / design.tonnes_per_hole) if tonnes > 0 else 0
    return AnnualBlasting(
        tonnes,
        holes,
        holes * design.hole_length,
        holes * design.charge_kg / 1000.0,
        holes * design.cost_per_hole,
    )


def _inside(poly: Floats, pts: Floats) -> npt.NDArray[np.bool_]:
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cross = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cross & (x < xs)
    return inside


def layout(
    design: BlastDesign,
    polygon: Floats,
    *,
    azimuth: float = 0.0,
    staggered: bool = True,
    collar_z: float | None = None,
) -> tuple[Floats, Floats]:
    """Collar and toe coordinates (n x 3 each) of holes inside a closed polygon.

    ``azimuth``: direction of the rows (degrees from north, clockwise); burden
    is measured perpendicular to it. ``collar_z`` defaults to the polygon's
    mean elevation. Holes lean (``hole_angle``) towards the burden direction.
    """
    poly = np.asarray(polygon, np.float64)
    if len(poly) < 3:
        raise BlastError("the blast outline needs at least three points")
    z0 = float(poly[:, 2].mean()) if collar_z is None and poly.shape[1] > 2 else (collar_z or 0.0)
    xy = poly[:, :2]
    a = math.radians(azimuth)
    along = np.array([math.sin(a), math.cos(a)])  # row direction
    across = np.array([along[1], -along[0]])  # burden direction
    rel = xy - xy.mean(axis=0)
    u, v = rel @ along, rel @ across
    b, s = design.b, design.s
    rows = np.arange(v.min() + b / 2, v.max(), b)
    pts = []
    for n, vv in enumerate(rows):
        shift = s / 2 if staggered and n % 2 else 0.0
        for uu in np.arange(u.min() + s / 2 + shift, u.max(), s):
            pts.append(xy.mean(axis=0) + uu * along + vv * across)
    if not pts:
        raise BlastError("the outline is smaller than one burden x spacing")
    grid = np.asarray(pts)
    grid = grid[_inside(xy, grid)]
    collars = np.column_stack([grid, np.full(len(grid), z0)])
    lean = math.tan(math.radians(design.hole_angle)) * (design.bench_height + design.subdrill)
    toes = collars.copy()
    toes[:, :2] += lean * across
    toes[:, 2] -= design.bench_height + design.subdrill
    return collars, toes
