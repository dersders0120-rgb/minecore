"""Python bindings of the local mine grid, control-point files, project and CLI."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest

from minecore import cli
from minecore.coords import (
    CoordinateError,
    CrsDefinition,
    CrsError,
    LocalGrid,
    LocalGridSystem,
    ProjectedSystem,
    UnreferencedSystem,
    fit_control_points,
    fit_local_grid,
    read_control_points,
)
from minecore.project import Project, ProjectError

E0, N0 = 512345.678, 4423456.789


def make_grid(rotation: float = 23.5, scale: float = 0.9996) -> LocalGrid:
    return LocalGrid(
        base_origin=(E0, N0),
        local_origin=(10000.0, 50000.0),
        rotation_deg=rotation,
        scale=scale,
        z_offset=1000.0,
    )


def test_properties_repr_and_equality() -> None:
    g = make_grid()
    assert g.base_origin == (E0, N0)
    assert (g.rotation_deg, g.scale, g.z_offset) == (23.5, 0.9996, 1000.0)
    assert g == make_grid()
    assert g != make_grid(rotation=23.6)
    assert repr(g).startswith("LocalGrid(base_origin=(512345.678, 4423456.789)")


def test_accepts_lists_ints_and_strided_arrays() -> None:
    g = make_grid(rotation=90.0, scale=1.0)
    x, y, z = g.to_local([int(E0) + 10], [N0])
    assert z is None
    assert x.dtype == np.float64
    assert x[0] == pytest.approx(10000.0, abs=1e-9)
    assert y[0] == pytest.approx(50010.0 - (E0 - int(E0)), abs=1e-9)
    strided = np.array([[E0, 0.0], [E0 + 1.0, 0.0]])[:, 0]
    sx, _, _ = g.to_local(strided, [N0, N0])
    assert sx.shape == (2,)


def test_round_trip_and_inputs_untouched() -> None:
    g = make_grid()
    rng = np.random.default_rng(7)
    e = E0 + rng.uniform(-5000, 5000, 10_000)
    n = N0 + rng.uniform(-5000, 5000, 10_000)
    z = rng.uniform(500, 1500, 10_000)
    original = e.copy()
    x, y, lz = g.to_local(e, n, z)
    be, bn, bz = g.to_base(x, y, lz)
    np.testing.assert_array_equal(e, original)
    np.testing.assert_allclose(be, e, rtol=0, atol=1e-7)
    np.testing.assert_allclose(bn, n, rtol=0, atol=1e-7)
    assert bz is not None
    np.testing.assert_allclose(bz, z, rtol=0, atol=1e-9)


def test_invalid_input_raises_coordinate_error() -> None:
    g = make_grid()
    with pytest.raises(CoordinateError, match="differ in length"):
        g.to_local([1.0, 2.0], [1.0])
    with pytest.raises(CoordinateError, match="scale"):
        LocalGrid(base_origin=(0, 0), local_origin=(0, 0), rotation_deg=0, scale=0)
    assert issubclass(CoordinateError, Exception)


def test_fit_recovers_grid_and_reports_quality() -> None:
    truth = make_grid(rotation=-41.25, scale=1.0002)
    base = np.array([[E0, N0], [E0 + 900, N0 + 50], [E0 + 300, N0 - 1200], [E0 - 700, N0 + 400]])
    x, y, _ = truth.to_local(base[:, 0], base[:, 1])
    fit = fit_local_grid(base, np.column_stack([x, y]))
    assert fit.max_horizontal < 1e-8
    assert fit.grid.rotation_deg == pytest.approx(-41.25, abs=1e-9)
    assert fit.grid.scale == pytest.approx(1.0002, abs=1e-12)
    assert fit.sigma0_horizontal is not None
    assert fit.vertical_points == 0
    assert fit.grid.z_offset == 0.0
    rigid = fit_local_grid(base, np.column_stack([x, y]), scale=1.0)
    assert rigid.grid.scale == 1.0
    assert rigid.max_horizontal > 0.05


def test_fit_argument_errors() -> None:
    pts = [[0.0, 0.0], [100.0, 0.0]]
    with pytest.raises(CoordinateError, match=r"shape \(n, 2\)"):
        fit_local_grid([[0.0, 0.0, 0.0]], pts)
    with pytest.raises(CoordinateError, match="2 points but"):
        fit_local_grid(pts, [[0.0, 0.0]])
    with pytest.raises(CoordinateError, match="both base_z and local_z"):
        fit_local_grid(pts, pts, base_z=[1.0, 2.0])
    with pytest.raises(CoordinateError, match="at least 2"):
        fit_local_grid([[0.0, 0.0]], [[0.0, 0.0]])


CONTROL = """name,base_e,base_n,base_z,local_x,local_y,local_z
KN-1,512345.678,4423456.789,1210.00,10000.000,50000.000,2210.01
KN-2,513245.678,4423506.789,1250.50,10872.870,50404.700,2250.49
KN-3,512645.678,4422256.789,1190.25,10797.411,48813.700,2190.26
"""


def write_control(tmp_path: Path, text: str = CONTROL) -> Path:
    path = tmp_path / "control.csv"
    path.write_text(text, encoding="utf-8-sig")
    return path


def test_control_point_file(tmp_path: Path) -> None:
    points = read_control_points(write_control(tmp_path))
    assert points.names == ["KN-1", "KN-2", "KN-3"]
    assert points.base_z == [1210.0, 1250.5, 1190.25]
    fit = fit_control_points(points)
    assert fit.vertical_points == 3
    assert fit.grid.z_offset == pytest.approx(1000.0, abs=0.01)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("name,base_e,base_n,local_x\nA,1,2,3\n", "missing columns local_y"),
        ("name,base_e,base_n,local_x,local_y,base_z\nA,1,2,3,4,5\n", "both base_z"),
        ("name,base_e,base_n,local_x,local_y\nA,1,x,3,4\n", "line 2: base_n 'x'"),
        ("name,base_e,base_n,local_x,local_y\nA,1,2,3,4\nA,5,6,7,8\n", "duplicate"),
        ("name,base_e,base_n,local_x,local_y\n,1,2,3,4\n", "no name"),
    ],
)
def test_control_point_file_errors(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(CrsError, match=message):
        read_control_points(write_control(tmp_path, text))


# -- project integration ---------------------------------------------------------


def test_project_stores_and_reloads_the_working_system(tmp_path: Path) -> None:
    pkg = tmp_path / "Ocak.mcproj"
    tm33 = CrsDefinition.from_user_input("EPSG:5255")
    system = LocalGridSystem("Ocak ızgarası", tm33, make_grid())
    with Project.create(pkg, "Ocak") as project:
        assert project.coordinate_system() is None
        project.set_coordinate_system(ProjectedSystem(tm33))
        project.set_coordinate_system(system)  # free to change while empty
    with Project.open(pkg, read_only=True) as project:
        assert project.coordinate_system() == system


def test_coordinate_system_is_frozen_once_objects_exist(tmp_path: Path) -> None:
    pkg = tmp_path / "Ocak.mcproj"
    tm33 = ProjectedSystem(CrsDefinition.from_user_input("EPSG:5255"))
    with Project.create(pkg, "Ocak") as project:
        project.set_coordinate_system(tm33)
        project.register_object("drillhole_set", "Sondajlar")
        project.set_coordinate_system(tm33)  # unchanged: allowed
        with pytest.raises(ProjectError, match="cannot change once the project holds objects"):
            project.set_coordinate_system(UnreferencedSystem("local"))


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    code = cli.main(argv, out)
    return code, out.getvalue()


def test_cli_crs_info() -> None:
    code, text = run(["crs", "info", "EPSG:5254", "--json"])
    assert code == 0
    info = json.loads(text)
    assert info["name"] == "TUREF / TM30"
    assert info["working_crs"] == "yes"
    code, text = run(["crs", "info", "EPSG:4326"])
    assert code == 0
    assert "working_crs : no" in text


def test_cli_grid_fit_report(tmp_path: Path) -> None:
    code, text = run(["grid", "fit", str(write_control(tmp_path)), "--json"])
    assert code == 0
    report = json.loads(text)
    assert report["points"] == 3
    assert [r["name"] for r in report["residuals"]] == ["KN-1", "KN-2", "KN-3"]
    code, text = run(["grid", "fit", str(write_control(tmp_path)), "--scale", "1"])
    assert code == 0
    assert "rotation" in text
    assert "KN-3" in text


def test_cli_sets_coordinate_systems(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pkg = str(tmp_path / "Ocak.mcproj")
    assert run(["project", "create", pkg, "--name", "Ocak"])[0] == 0
    code, text = run(["project", "set-crs", pkg, "--crs", "EPSG:5255"])
    assert (code, text) == (0, "coordinate system: TUREF / TM33 (EPSG:5255)\n")
    code, text = run(
        [
            "project",
            "set-local-grid",
            pkg,
            "--name",
            "Ocak",
            "--base",
            "EPSG:5255",
            "--control",
            str(write_control(tmp_path)),
        ]
    )
    assert code == 0
    assert "local grid 'Ocak' on TUREF / TM33" in text
    code, text = run(
        [
            "project",
            "set-local-grid",
            pkg,
            "--name",
            "P",
            "--base",
            "EPSG:5255",
            "--params",
            "512000",
            "4423000",
            "0",
            "0",
            "15.5",
            "--z-offset",
            "-1000",
        ]
    )
    assert code == 0
    assert "rotation 15.500000 deg" in text
    code, text = run(["project", "info", pkg, "--json"])
    assert json.loads(text)["coordinates"].startswith("local grid 'P'")
    code, _ = run(["project", "set-crs", pkg, "--crs", "EPSG:4326"])
    assert code == 1
    assert "geographic" in capsys.readouterr().err
