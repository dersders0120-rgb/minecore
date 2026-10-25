"""Independent check of PROJ's Transverse Mercator for the zones used in Turkey.

The reference is Krüger's series to sixth order in the third flattening n
(Karney 2011, "Transverse Mercator with an accuracy of a few nanometers"),
written here in pure ``math`` with no PROJ code. Within a few degrees of the
central meridian the series is accurate to well below a micrometre, so any
disagreement with PROJ above 1 mm means MineCore is calling PROJ wrongly
(axis order, parameters, datum) or PROJ is wrong.
"""

from __future__ import annotations

import math

import pyproj
import pytest

from minecore.coords import CoordinateTransformer, CrsDefinition, ProjectedSystem

GRS80 = (6378137.0, 298.257222101)
WGS84 = (6378137.0, 298.257223563)
INTL1924 = (6378388.0, 297.0)


def kruger(
    lon: float, lat: float, *, ellipsoid: tuple[float, float], lon0: float, k0: float
) -> tuple[float, float]:
    """Easting, northing (false easting 500 km, false northing 0)."""
    a, inv_f = ellipsoid
    f = 1.0 / inv_f
    n = f / (2.0 - f)
    e = math.sqrt(f * (2.0 - f))
    big_a = a / (1.0 + n) * (1.0 + n**2 / 4.0 + n**4 / 64.0 + n**6 / 256.0)
    alpha = (
        n / 2
        - 2 * n**2 / 3
        + 5 * n**3 / 16
        + 41 * n**4 / 180
        - 127 * n**5 / 288
        + 7891 * n**6 / 37800,
        13 * n**2 / 48
        - 3 * n**3 / 5
        + 557 * n**4 / 1440
        + 281 * n**5 / 630
        - 1983433 * n**6 / 1935360,
        61 * n**3 / 240 - 103 * n**4 / 140 + 15061 * n**5 / 26880 + 167603 * n**6 / 181440,
        49561 * n**4 / 161280 - 179 * n**5 / 168 + 6601661 * n**6 / 7257600,
        34729 * n**5 / 80640 - 3418889 * n**6 / 1995840,
        212378941 * n**6 / 319334400,
    )
    phi, lam = math.radians(lat), math.radians(lon - lon0)
    t = math.sinh(math.atanh(math.sin(phi)) - e * math.atanh(e * math.sin(phi)))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.asinh(math.sin(lam) / math.hypot(t, math.cos(lam)))
    xi, eta = xi_p, eta_p
    for j, aj in enumerate(alpha, start=1):
        xi += aj * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += aj * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
    return 500000.0 + k0 * big_a * eta, k0 * big_a * xi


# (EPSG code, geographic CRS of the same datum, ellipsoid, central meridian, k0, half-width)
ZONES = [
    *[(5253 + i, "EPSG:5252", GRS80, 27.0 + 3 * i, 1.0, 1.5) for i in range(7)],  # TUREF TM
    *[(32635 + i, "EPSG:4326", WGS84, 27.0 + 6 * i, 0.9996, 3.0) for i in range(4)],  # WGS84 UTM
    *[(23035 + i, "EPSG:4230", INTL1924, 27.0 + 6 * i, 0.9996, 3.0) for i in range(4)],  # ED50
]


@pytest.mark.parametrize(("code", "geographic", "ellipsoid", "lon0", "k0", "half"), ZONES)
def test_proj_matches_independent_kruger_series(  # noqa: PLR0917 (parametrised)
    code: int,
    geographic: str,
    ellipsoid: tuple[float, float],
    lon0: float,
    k0: float,
    half: float,
) -> None:
    target = CrsDefinition.from_user_input(f"EPSG:{code}")
    # The CRS really has the parameters the reference assumes.
    operation = target.to_pyproj().coordinate_operation
    assert operation is not None
    params = {p.name: float(p.value) for p in operation.params}
    assert params["Longitude of natural origin"] == lon0
    assert params["Scale factor at natural origin"] == k0
    assert params["False easting"] == 500000.0
    assert params["False northing"] == 0.0
    ell = target.to_pyproj().ellipsoid
    assert ell is not None
    assert (ell.semi_major_metre, ell.inverse_flattening) == pytest.approx(ellipsoid, rel=1e-12)

    source = CrsDefinition.from_user_input(geographic)
    transformer = CoordinateTransformer(source, ProjectedSystem(target))
    assert transformer.operation.exact, "same datum: a pure conversion"
    lons = [lon0 + half * s for s in (-1.0, -0.5, 0.0, 0.37, 1.0)]
    lats = [36.0, 37.9, 39.25, 41.0, 42.1]
    pairs = [(lo, la) for lo in lons for la in lats]
    x, y, _ = transformer.forward([p[0] for p in pairs], [p[1] for p in pairs])
    for (lon, lat), xe, yn in zip(pairs, x, y, strict=True):
        ref_e, ref_n = kruger(lon, lat, ellipsoid=ellipsoid, lon0=lon0, k0=k0)
        assert math.hypot(xe - ref_e, yn - ref_n) < 1e-3, (code, lon, lat)


def test_reference_series_is_self_consistent() -> None:
    # On the central meridian the northing is the meridian arc length; at the
    # equator it is zero and the easting is the false easting.
    assert kruger(33.0, 0.0, ellipsoid=GRS80, lon0=33.0, k0=1.0) == pytest.approx((500000.0, 0.0))
    # Symmetry about the central meridian.
    e1, n1 = kruger(31.5, 39.0, ellipsoid=GRS80, lon0=33.0, k0=1.0)
    e2, n2 = kruger(34.5, 39.0, ellipsoid=GRS80, lon0=33.0, k0=1.0)
    assert e1 + e2 == pytest.approx(1_000_000.0, abs=1e-9)
    assert n1 == pytest.approx(n2, abs=1e-9)


def test_turef_axis_order_is_normalised() -> None:
    # EPSG defines TUREF TM zones with northing first; MineCore always uses
    # x = easting, y = northing.
    axes = [a.direction for a in pyproj.CRS("EPSG:5254").axis_info]
    assert axes == ["north", "east"]
    t = CoordinateTransformer(
        CrsDefinition.from_user_input("EPSG:5252"),
        ProjectedSystem(CrsDefinition.from_user_input("EPSG:5254")),
    )
    x, y, _ = t.forward([30.0], [39.0])
    assert x[0] == pytest.approx(500000.0, abs=1e-6)
    assert y[0] > 4_000_000.0
