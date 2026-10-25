from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin
from typing import Literal

HeaderStyle = Literal["canonical", "short", "turkish"]
DialectName = Literal["std", "tr", "tr1254"]
ErrorsMode = Literal["none", "standard", "all"]

LITH_DESCRIPTIONS: dict[str, str] = {
    "OB": "Örtü (alüvyon)",
    "AND": "Andezit, altere",
    "QV": "Kuvars damarı, piritli",
    "SCH": "Şist, kloritli",
}


@dataclass(frozen=True)
class Config:
    seed: int = 42
    holes: int = 200
    errors: ErrorsMode = "none"

    def __post_init__(self) -> None:
        if self.holes <= 0:
            msg = "holes must be positive"
            raise ValueError(msg)
        if self.errors not in ("none", "standard", "all"):
            msg = f"unknown errors mode: {self.errors}"
            raise ValueError(msg)


@dataclass(frozen=True)
class Topography:
    xc: float
    yc: float
    z0: float
    gx: float
    gy: float
    amplitude: float
    lx: float
    ly: float

    def z(self, x: float, y: float) -> float:
        return (
            self.z0
            + self.gx * (x - self.xc)
            + self.gy * (y - self.yc)
            + self.amplitude
            * sin(2.0 * 3.141592653589793 * (x - self.xc) / self.lx)
            * cos(2.0 * 3.141592653589793 * (y - self.yc) / self.ly)
        )


@dataclass(frozen=True)
class Plane:
    dip_dir: float
    dip: float
    ref: tuple[float, float, float]
    thickness: float
    normal: tuple[float, float, float]

    @classmethod
    def create(
        cls,
        dip_dir: float,
        dip: float,
        ref: tuple[float, float, float],
        thickness: float,
    ) -> Plane:
        d = radians(dip)
        a = radians(dip_dir)
        normal = (sin(d) * sin(a), sin(d) * cos(a), cos(d))
        return cls(dip_dir, dip, ref, thickness, normal)

    def signed(self, x: float, y: float, z: float) -> float:
        return (
            self.normal[0] * (x - self.ref[0])
            + self.normal[1] * (y - self.ref[1])
            + self.normal[2] * (z - self.ref[2])
        )

    def inside(self, x: float, y: float, z: float) -> bool:
        s = self.signed(x, y, z)
        return 0.0 <= s <= self.thickness


@dataclass(frozen=True)
class Collar:
    hole_id: str
    x: float
    y: float
    z: float
    total_depth: float
    hole_type: str


@dataclass(frozen=True)
class Station:
    depth: float
    azimuth: float
    dip: float


@dataclass(frozen=True)
class LithInterval:
    hole_id: str
    from_m: float
    to_m: float
    lith_code: str
    description: str


@dataclass(frozen=True)
class Sample:
    hole_id: str
    from_m: float
    to_m: float
    sample_id: str
    au_ppm: float
    cu_pct: float
    mid_x: float
    mid_y: float
    mid_z: float
    inside: bool


@dataclass
class Dataset:
    seed: int
    errors: ErrorsMode
    topo: Topography
    plane: Plane
    collars: list[Collar]
    surveys: dict[str, list[Station]]
    lithology: dict[str, list[LithInterval]]
    samples: list[Sample]
    truth: dict[str, object]
