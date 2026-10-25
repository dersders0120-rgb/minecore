"""Coordinate reference system definitions, backed by PROJ (ADR-0005).

A CRS is stored as its WKT2:2019 text, so a project keeps the exact definition
it was created with even if a later PROJ release changes the EPSG entry. The
authority code is kept for display and search only.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from functools import cache

import pyproj
from pyproj.exceptions import CRSError

from minecore._core import MineCoreError

# MineCore never downloads transformation grids at run time: results must not
# depend on network state. Missing grids are reported, never silently skipped.
pyproj.network.set_network_enabled(active=False)  # type: ignore[attr-defined]  # stub omits re-export

_METRE = "metre"


class CrsError(MineCoreError):
    """A CRS cannot be parsed or is not valid for its intended use."""


class CrsKind(enum.Enum):
    """Broad CRS category."""

    PROJECTED = "projected"
    GEOGRAPHIC = "geographic"
    GEOCENTRIC = "geocentric"
    VERTICAL = "vertical"
    COMPOUND = "compound"
    ENGINEERING = "engineering"
    OTHER = "other"


@cache
def _parse_wkt(wkt: str) -> pyproj.CRS:
    return pyproj.CRS.from_wkt(wkt)


def _kind(crs: pyproj.CRS) -> CrsKind:
    checks = (
        (crs.is_compound, CrsKind.COMPOUND),
        (crs.is_projected, CrsKind.PROJECTED),
        (crs.is_geographic, CrsKind.GEOGRAPHIC),
        (crs.is_geocentric, CrsKind.GEOCENTRIC),
        (crs.is_vertical, CrsKind.VERTICAL),
        (crs.is_engineering, CrsKind.ENGINEERING),
    )
    return next((kind for flag, kind in checks if flag), CrsKind.OTHER)


@dataclass(frozen=True)
class CrsDefinition:
    """An immutable CRS definition: name, WKT2:2019 text, optional authority code."""

    name: str
    wkt: str
    authority: str | None = None

    @classmethod
    def from_user_input(cls, text: str) -> CrsDefinition:
        """Parse ``EPSG:5254``, WKT, PROJJSON or a PROJ string."""
        try:
            crs = pyproj.CRS.from_user_input(text.strip())
        except CRSError as exc:
            raise CrsError(f"cannot parse CRS {text!r}: {exc}") from None
        return cls.from_pyproj(crs)

    @classmethod
    def from_pyproj(cls, crs: pyproj.CRS) -> CrsDefinition:
        """Wrap a pyproj CRS."""
        auth = crs.to_authority(min_confidence=100)
        wkt = crs.to_wkt(version="WKT2_2019")
        if wkt is None:
            raise CrsError(f"CRS {crs.name!r} cannot be written as WKT2")
        return cls(name=crs.name, wkt=wkt, authority=None if auth is None else ":".join(auth))

    def to_pyproj(self) -> pyproj.CRS:
        """The pyproj CRS for this definition."""
        try:
            return _parse_wkt(self.wkt)
        except CRSError as exc:
            raise CrsError(f"stored CRS {self.name!r} is no longer readable: {exc}") from None

    @property
    def kind(self) -> CrsKind:
        """Broad category of the CRS."""
        return _kind(self.to_pyproj())

    def same_as(self, other: CrsDefinition) -> bool:
        """True when both define the same CRS (ignoring names and metadata)."""
        return self.wkt == other.wkt or self.to_pyproj().equals(other.to_pyproj())

    def describe(self) -> str:
        """Name with authority code, e.g. ``TUREF / TM30 (EPSG:5254)``."""
        return self.name if self.authority is None else f"{self.name} ({self.authority})"

    def area_of_use(self) -> tuple[float, float, float, float] | None:
        """``(west, south, east, north)`` in degrees, if the CRS declares one."""
        area = self.to_pyproj().area_of_use
        return None if area is None else area.bounds


def _check_metric(crs: pyproj.CRS, label: str) -> None:
    for axis in crs.axis_info:
        if axis.unit_name != _METRE:
            raise CrsError(
                f"{label} {crs.name!r} uses {axis.unit_name!r} on axis {axis.name!r}; "
                "MineCore stores geometry in metres, so the working CRS must be metric"
            )


def require_working_crs(crs: CrsDefinition) -> None:
    """Raise unless ``crs`` can hold project geometry.

    Accepted: a projected CRS with metre axes, or a compound CRS of such a
    projected CRS and a vertical CRS in metres.
    """
    parsed = crs.to_pyproj()
    if parsed.is_compound:
        parts = parsed.sub_crs_list
        if len(parts) != 2 or not parts[0].is_projected or not parts[1].is_vertical:
            raise CrsError(f"compound CRS {crs.name!r} must be a projected CRS plus a vertical CRS")
        _check_metric(parts[0], "horizontal CRS")
        _check_metric(parts[1], "vertical CRS")
        return
    if not parsed.is_projected:
        raise CrsError(
            f"{crs.describe()} is a {_kind(parsed).value} CRS; the working CRS must be "
            "projected (eastings and northings in metres)"
        )
    _check_metric(parsed, "CRS")
