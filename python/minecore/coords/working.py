"""The working coordinate system: the system all project geometry is stored in.

Exactly one of:

- :class:`ProjectedSystem` — a projected CRS in metres (optionally compound with
  a vertical CRS), e.g. TUREF / TM30;
- :class:`LocalGridSystem` — a local mine grid defined relative to a base
  projected CRS by a 2D similarity transformation and a vertical offset;
- :class:`UnreferencedSystem` — local coordinates with no known relation to
  any CRS. Nothing is ever transformed into or out of it.

The JSON form is part of the project format (ADR-0005, format version 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minecore._core import LocalGrid
from minecore.coords.crs import CrsDefinition, CrsError, require_working_crs

FORMAT_VERSION = 1


@dataclass(frozen=True)
class ProjectedSystem:
    """Geometry is stored directly in a projected CRS."""

    crs: CrsDefinition

    def __post_init__(self) -> None:
        require_working_crs(self.crs)

    def describe(self) -> str:
        """One-line description."""
        return self.crs.describe()


@dataclass(frozen=True)
class LocalGridSystem:
    """Geometry is stored in a local mine grid tied to a base projected CRS."""

    name: str
    base: CrsDefinition
    grid: LocalGrid

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise CrsError("a local grid needs a name")
        require_working_crs(self.base)

    def describe(self) -> str:
        """One-line description."""
        g = self.grid
        return (
            f"local grid {self.name!r} on {self.base.describe()}: rotation "
            f"{g.rotation_deg:.6f} deg, scale {g.scale:.9f}, z offset {g.z_offset:.3f} m"
        )


@dataclass(frozen=True)
class UnreferencedSystem:
    """Local coordinates with no georeference."""

    description: str

    def describe(self) -> str:
        """One-line description."""
        return f"unreferenced ({self.description})" if self.description else "unreferenced"


WorkingSystem = ProjectedSystem | LocalGridSystem | UnreferencedSystem


def _crs_json(crs: CrsDefinition) -> dict[str, Any]:
    return {"name": crs.name, "authority": crs.authority, "wkt": crs.wkt}


def _crs_from_json(data: dict[str, Any]) -> CrsDefinition:
    return CrsDefinition(name=data["name"], wkt=data["wkt"], authority=data.get("authority"))


def to_json(system: WorkingSystem) -> dict[str, Any]:
    """Serialise a working system for the project settings table."""
    match system:
        case ProjectedSystem(crs=crs):
            return {"format": FORMAT_VERSION, "type": "projected", "crs": _crs_json(crs)}
        case LocalGridSystem(name=name, base=base, grid=g):
            return {
                "format": FORMAT_VERSION,
                "type": "local_grid",
                "name": name,
                "base": _crs_json(base),
                "transform": {
                    "base_origin": list(g.base_origin),
                    "local_origin": list(g.local_origin),
                    "rotation_deg": g.rotation_deg,
                    "scale": g.scale,
                    "z_offset": g.z_offset,
                },
            }
        case UnreferencedSystem(description=description):
            return {"format": FORMAT_VERSION, "type": "unreferenced", "description": description}


def from_json(data: dict[str, Any]) -> WorkingSystem:
    """Read a working system written by :func:`to_json`."""
    version = data.get("format")
    if version != FORMAT_VERSION:
        raise CrsError(f"unsupported coordinate system format {version!r}")
    try:
        kind = data["type"]
        if kind == "projected":
            return ProjectedSystem(_crs_from_json(data["crs"]))
        if kind == "local_grid":
            t = data["transform"]
            grid = LocalGrid(
                base_origin=(t["base_origin"][0], t["base_origin"][1]),
                local_origin=(t["local_origin"][0], t["local_origin"][1]),
                rotation_deg=t["rotation_deg"],
                scale=t["scale"],
                z_offset=t["z_offset"],
            )
            return LocalGridSystem(data["name"], _crs_from_json(data["base"]), grid)
        if kind == "unreferenced":
            return UnreferencedSystem(data["description"])
    except (KeyError, IndexError, TypeError) as exc:
        raise CrsError(f"damaged coordinate system record: {exc!r}") from None
    raise CrsError(f"unknown coordinate system type {kind!r}")
