"""CRS parsing, working-system rules and the transformation policy (ADR-0005)."""

from __future__ import annotations

import numpy as np
import pytest

from minecore.coords import (
    CoordinateTransformer,
    CrsDefinition,
    CrsError,
    CrsKind,
    GeographicBounds,
    LocalGrid,
    LocalGridSystem,
    ProjectedSystem,
    TransformationError,
    UnreferencedSystem,
    bounds_of_points,
    from_json,
    require_working_crs,
    to_json,
)

TM33 = CrsDefinition.from_user_input("EPSG:5255")
ED50_UTM36 = CrsDefinition.from_user_input("EPSG:23036")
WGS84 = CrsDefinition.from_user_input("EPSG:4326")
CENTRAL_TURKEY = GeographicBounds(32.5, 38.5, 33.5, 39.5)


def test_parse_keeps_name_authority_and_wkt() -> None:
    assert TM33.name == "TUREF / TM33"
    assert TM33.authority == "EPSG:5255"
    assert TM33.wkt.startswith("PROJCRS[")
    assert TM33.kind is CrsKind.PROJECTED
    assert TM33.describe() == "TUREF / TM33 (EPSG:5255)"
    again = CrsDefinition.from_user_input(TM33.wkt)
    assert again.same_as(TM33)
    assert again.wkt == TM33.wkt, "WKT2 output is stable across a round trip"


def test_unparseable_crs_is_rejected() -> None:
    with pytest.raises(CrsError, match="cannot parse CRS"):
        CrsDefinition.from_user_input("EPSG:99999999")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("EPSG:4326", "geographic CRS"),
        ("EPSG:2263", "US survey foot"),
        ("EPSG:4978", "geocentric CRS"),
    ],
)
def test_working_crs_must_be_projected_and_metric(text: str, message: str) -> None:
    with pytest.raises(CrsError, match=message):
        require_working_crs(CrsDefinition.from_user_input(text))


def test_compound_projected_plus_vertical_is_a_valid_working_crs() -> None:
    compound = CrsDefinition.from_user_input("EPSG:32636+5773")
    assert compound.kind is CrsKind.COMPOUND
    require_working_crs(compound)


def test_identity_needs_no_proj_operation() -> None:
    t = CoordinateTransformer(TM33, ProjectedSystem(TM33))
    assert t.operation.exact
    assert t.operation.pipeline == ""
    x, y, z = t.forward([500000.0], [4300000.0], [1000.0])
    assert (x[0], y[0], z is not None and z[0]) == (500000.0, 4300000.0, 1000.0)


def test_datum_change_uses_one_reported_operation() -> None:
    t = CoordinateTransformer(ED50_UTM36, ProjectedSystem(TM33), area=CENTRAL_TURKEY)
    op = t.operation
    assert not op.exact
    assert op.accuracy_m is not None
    assert 0.0 < op.accuracy_m <= 10.0
    assert "ED50" in op.description
    assert "helmert" in op.pipeline
    # ED50 and TUREF differ by ~100 m; the shift must be visible (datum not ignored).
    e, n = np.array([440000.0, 450000.0]), np.array([4290000.0, 4300000.0])
    # The same numbers read as WGS 84 / UTM 36N (a datum ~1 m from TUREF).
    plain = CoordinateTransformer(
        CrsDefinition.from_user_input("EPSG:32636"), ProjectedSystem(TM33), area=CENTRAL_TURKEY
    )
    x1, y1, _ = t.forward(e, n)
    x2, y2, _ = plain.forward(e, n)
    shift = np.hypot(x1 - x2, y1 - y2)
    assert np.all((shift > 50.0) & (shift < 300.0)), shift
    # Inverse reproduces the input.
    back_e, back_n, _ = t.inverse(x1, y1)
    np.testing.assert_allclose(back_e, e, atol=1e-6)
    np.testing.assert_allclose(back_n, n, atol=1e-6)


def test_elevations_pass_through_2d_crs_unchanged() -> None:
    t = CoordinateTransformer(ED50_UTM36, ProjectedSystem(TM33), area=CENTRAL_TURKEY)
    _, _, z = t.forward([450000.0], [4300000.0], [1234.5])
    assert z is not None
    assert z[0] == 1234.5


def test_ballpark_only_transformation_is_refused() -> None:
    unknown_datum = CrsDefinition.from_user_input(
        "+proj=tmerc +lon_0=33 +k=1 +x_0=500000 +ellps=bessel +units=m +no_defs"
    )
    with pytest.raises(TransformationError, match="without ballpark"):
        CoordinateTransformer(unknown_datum, ProjectedSystem(TM33))


def test_missing_grid_is_named_not_silently_skipped() -> None:
    nad27 = CrsDefinition.from_user_input("EPSG:4267")
    utm14 = CrsDefinition.from_user_input("EPSG:26914")  # NAD83 / UTM 14N
    with pytest.raises(TransformationError, match=r"grid files that are not installed: .*\.tif"):
        CoordinateTransformer(
            nad27, ProjectedSystem(utm14), area=GeographicBounds(-100.0, 40.0, -99.0, 41.0)
        )


def test_unreferenced_system_never_transforms() -> None:
    with pytest.raises(TransformationError, match="unreferenced"):
        CoordinateTransformer(WGS84, UnreferencedSystem("exploration grid, origin unknown"))


def test_local_grid_system_composes_proj_and_grid() -> None:
    grid = LocalGrid(
        base_origin=(500000.0, 4300000.0),
        local_origin=(10000.0, 20000.0),
        rotation_deg=-12.5,
        scale=1.0,
        z_offset=-500.0,
    )
    system = LocalGridSystem("Pit grid", TM33, grid)
    t = CoordinateTransformer.for_points(WGS84, system, [33.0, 33.1], [38.8, 38.9])
    lon, lat, h = np.array([33.0, 33.1]), np.array([38.8, 38.9]), np.array([1100.0, 1200.0])
    x, y, z = t.forward(lon, lat, h)
    base = CoordinateTransformer(WGS84, ProjectedSystem(TM33))
    e, n, _ = base.forward(lon, lat)
    ex, ey, ez = grid.to_local(e, n, h)
    np.testing.assert_allclose(x, ex, atol=1e-9)
    np.testing.assert_allclose(y, ey, atol=1e-9)
    assert z is not None
    assert ez is not None
    np.testing.assert_allclose(z, ez)
    lon2, lat2, h2 = t.inverse(x, y, z)
    np.testing.assert_allclose(lon2, lon, atol=1e-11)
    np.testing.assert_allclose(lat2, lat, atol=1e-11)
    assert h2 is not None
    np.testing.assert_allclose(h2, h)


def test_bounds_of_points_uses_the_source_datum() -> None:
    b = bounds_of_points(TM33, [500000.0, 510000.0], [4300000.0, 4310000.0], margin_deg=0.0)
    assert b.west == pytest.approx(33.0, abs=1e-9)
    assert 38.8 < b.south < b.north < 39.0
    with pytest.raises(TransformationError):
        bounds_of_points(TM33, [np.nan], [4300000.0])


def test_bad_coordinates_are_rejected() -> None:
    t = CoordinateTransformer(WGS84, ProjectedSystem(TM33))
    with pytest.raises(TransformationError, match="same length"):
        t.forward([33.0, 33.1], [39.0])
    with pytest.raises(TransformationError, match="NaN"):
        t.forward([np.inf], [39.0])


def test_working_system_json_round_trip() -> None:
    grid = LocalGrid(
        base_origin=(512345.678, 4423456.789),
        local_origin=(10000.0, 50000.0),
        rotation_deg=17.123456789,
        scale=0.9996,
        z_offset=1000.0,
    )
    for system in (
        ProjectedSystem(TM33),
        LocalGridSystem("Kışladağ Ocak Izgarası", TM33, grid),
        UnreferencedSystem("arama ızgarası"),
    ):
        data = to_json(system)
        assert data["format"] == 1
        assert from_json(data) == system


def test_damaged_json_is_rejected() -> None:
    with pytest.raises(CrsError, match="unsupported coordinate system format"):
        from_json({"format": 2, "type": "projected"})
    with pytest.raises(CrsError, match="damaged"):
        from_json({"format": 1, "type": "local_grid", "name": "x"})
    with pytest.raises(CrsError, match="unknown coordinate system type"):
        from_json({"format": 1, "type": "polar"})


def test_geographic_base_for_local_grid_is_rejected() -> None:
    grid = LocalGrid(base_origin=(0.0, 0.0), local_origin=(0.0, 0.0), rotation_deg=0.0)
    with pytest.raises(CrsError, match="geographic"):
        LocalGridSystem("bad", WGS84, grid)
