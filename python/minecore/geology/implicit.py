"""Implicit domain modelling (radial basis functions) from drillhole intervals.

Every interval is classified inside or outside the domain (numeric value at or
above a threshold, or one of a set of category codes). Along each hole:

* a contact point (value 0) is placed at every boundary between an inside and
  an outside interval that touch (gaps in sampling give no contact);
* every interval midpoint gets the signed distance to the nearest contact
  point of any hole (positive inside, negative outside), capped at
  ``max_distance`` (an upper bound of the true distance to the surface).

These signed distances are interpolated with a polyharmonic radial basis
function (SciPy ``RBFInterpolator``, kernel ``r`` with a linear drift, the 3-D
biharmonic spline), optionally in an anisotropic space given by an ellipsoid,
and the zero iso-surface is extracted on a block grid with marching cubes.
The result is a closed solid; blocks with a positive value are inside.

This is a numerical interpolation of the drilled contacts, not a geological
interpretation; it does not honour faults or younging directions.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore._core import MineCoreError
from minecore.blockmodel import BlockModel, Grid
from minecore.drillholes import DrillholeDatabase, FieldType
from minecore.drillholes.validate import validate
from minecore.drillholes.workflow import hole_geometry
from minecore.estimation import Ellipsoid
from minecore.solids import Solid, iso_solid

Floats = npt.NDArray[np.float64]
Bools = npt.NDArray[np.bool_]


class ImplicitError(MineCoreError):
    """Invalid input for implicit modelling."""


@dataclass(frozen=True)
class ContactData:
    xyz: Floats  # n x 3
    value: Floats  # signed distance, + inside, - outside, 0 on contacts
    contacts: int
    inside: int
    outside: int


def contact_data(  # noqa: PLR0913 - one array per interval attribute
    holes: npt.NDArray[np.object_],
    depth_from: Floats,
    depth_to: Floats,
    inside: Bools,
    at: Callable[[str, Floats], Floats],
    *,
    max_distance: float = 10.0,
) -> ContactData:
    """Signed-distance points from classified intervals (see module docstring)."""
    if not max_distance > 0:
        raise ImplicitError("max_distance must be > 0")
    mids: list[Floats] = []
    signs: list[Bools] = []
    contact_xyz: list[Floats] = []
    for hole in sorted(set(holes.tolist())):
        sel = np.flatnonzero(holes == hole)
        sel = sel[np.argsort(depth_from[sel], kind="stable")]
        f, t, ins = depth_from[sel], depth_to[sel], inside[sel]
        touching = np.abs(f[1:] - t[:-1]) < 1e-3
        change = touching & (ins[1:] != ins[:-1])
        mids.append(at(hole, 0.5 * (f + t)))
        signs.append(ins)
        if change.any():
            contact_xyz.append(at(hole, t[:-1][change]))
    if not mids:
        raise ImplicitError("no intervals")
    mid_xyz, ins_all = np.vstack(mids), np.concatenate(signs)
    if contact_xyz:
        from scipy.spatial import cKDTree  # noqa: PLC0415

        cxyz = np.vstack(contact_xyz)
        dist, _ = cKDTree(cxyz).query(mid_xyz)
        dist = np.minimum(dist, max_distance)
    else:
        cxyz = np.empty((0, 3))
        dist = np.full(len(mid_xyz), max_distance)
    contacts = len(cxyz)
    pts = [mid_xyz, cxyz]
    vals = [np.where(ins_all, dist, -dist), np.zeros(contacts)]
    xyz = np.vstack(pts)
    value = np.concatenate(vals)
    ok = np.isfinite(xyz).all(axis=1)
    n_in = int((value > 0).sum())
    if n_in == 0 or n_in == int((value != 0).sum()):
        raise ImplicitError("the data must contain both inside and outside intervals")
    return ContactData(xyz[ok], value[ok], contacts, n_in, int((value < 0).sum()))


def interval_contacts(  # noqa: PLR0913 - selection options are keyword-only
    db: DrillholeDatabase,
    table: str,
    field: str,
    *,
    threshold: float | None = None,
    codes: tuple[str, ...] = (),
    max_distance: float = 10.0,
) -> ContactData:
    """Contact data from a numeric field (>= threshold) or category codes."""
    report = validate(db)
    info = db.table_info(table)
    spec = next((s for s in info.fields if s.name == field), None)
    if spec is None:
        raise ImplicitError(f"table {table!r} has no field {field!r}")
    data = db.table(table)
    usable = np.asarray(report.usable[table], bool)
    column = data.column(field)
    if spec.type is FieldType.NUMERIC:
        if threshold is None:
            raise ImplicitError("a numeric field needs a threshold")
        values = np.asarray(column.to_numpy(zero_copy_only=False), np.float64)
        known = np.isfinite(values)
        inside = values >= threshold
    else:
        if not codes:
            raise ImplicitError("a category field needs one or more codes")
        text = np.array([("" if v is None else str(v)) for v in column.to_pylist()], object)
        known = text != ""
        inside = np.isin(text, list(codes))
    holes = np.array(data.column("hole_id").to_pylist(), object)
    f = np.asarray(data.column("from").to_numpy(), np.float64)
    t = np.asarray(data.column("to").to_numpy(), np.float64)
    keep = usable & known
    geometry = hole_geometry(db, report)
    keep &= np.array([h in geometry for h in holes], bool)

    def at(hole: str, depths: Floats) -> Floats:
        return np.asarray(geometry[hole].at(depths), np.float64)

    return contact_data(holes[keep], f[keep], t[keep], inside[keep], at, max_distance=max_distance)


def _space(xyz: Floats, anisotropy: Ellipsoid | None) -> Floats:
    if anisotropy is None:
        return xyz
    scale = anisotropy.major / np.array([anisotropy.major, anisotropy.semi, anisotropy.minor])
    return np.asarray((xyz @ anisotropy.axes().T) * scale, np.float64)


def thin(data: ContactData, max_points: int) -> tuple[Floats, Floats]:
    """All contact points plus every k-th interval point, at most ``max_points``."""
    contact = data.value == 0
    if len(data.value) <= max_points:
        return data.xyz, data.value
    rest = np.flatnonzero(~contact)
    room = max(max_points - int(contact.sum()), 1)
    keep = rest[:: math.ceil(len(rest) / room)]
    sel = np.r_[np.flatnonzero(contact), keep]
    return data.xyz[sel], data.value[sel]


def implicit_values(
    data: ContactData,
    points: Floats,
    *,
    anisotropy: Ellipsoid | None = None,
    smoothing: float = 0.0,
    max_points: int = 4000,
) -> Floats:
    """Interpolated signed distance at ``points`` (n x 3).

    The RBF system is solved globally (dense, O(n^3)); larger data sets keep
    every contact and a regular subset of interval points (``max_points``).
    """
    from scipy.interpolate import RBFInterpolator  # noqa: PLC0415

    xyz, value = thin(data, max_points)
    # Work relative to the data centre: the linear drift is then well conditioned.
    centre = xyz.mean(axis=0)
    src = _space(xyz - centre, anisotropy)
    rbf = RBFInterpolator(src, value, kernel="linear", degree=1, smoothing=smoothing)
    dst = _space(np.asarray(points, np.float64) - centre, anisotropy)
    out = np.empty(len(dst))
    chunk = max(1, 20_000_000 // len(src))
    for start in range(0, len(dst), chunk):
        out[start : start + chunk] = rbf(dst[start : start + chunk])
    return out


@dataclass(frozen=True)
class ImplicitResult:
    solid: Solid
    values: Floats  # per block of the grid
    data: ContactData
    blocks_inside: int


def implicit_solid(
    data: ContactData,
    grid: Grid,
    *,
    anisotropy: Ellipsoid | None = None,
    smoothing: float = 0.0,
) -> ImplicitResult:
    values = implicit_values(data, grid.centroids(), anisotropy=anisotropy, smoothing=smoothing)
    solid = iso_solid(grid, values, 0.0)
    return ImplicitResult(solid, values, data, int((values > 0).sum()))


def model_domain(
    model: BlockModel,
    data: ContactData,
    name: str,
    *,
    anisotropy: Ellipsoid | None = None,
    smoothing: float = 0.0,
) -> ImplicitResult:
    """Implicit solid on the model's grid; stores ``dom_<name>`` (1/0) and ``imp_<name>``."""
    result = implicit_solid(data, model.grid, anisotropy=anisotropy, smoothing=smoothing)
    info = {
        "unit": None,
        "contacts": data.contacts,
        "smoothing": smoothing,
        "anisotropy": None if anisotropy is None else anisotropy.__dict__,
    }
    model.set_attributes(
        {
            f"dom_{name}": (result.values > 0).astype(np.float64),
            f"imp_{name}": result.values,
        },
        {f"dom_{name}": info, f"imp_{name}": {**info, "unit": "m"}},
        f"Implicit domain {name!r}",
    )
    return result
