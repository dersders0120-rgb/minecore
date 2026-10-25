"""Strings: DXF polylines and linking section outlines into solids."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from minecore.io.dxf import Polyline, write_dxf
from minecore.project import Project
from minecore.solids import Solid
from minecore.strings import (
    String,
    StringError,
    link_sections,
    load_strings,
    read_dxf_strings,
    save_strings,
    section_order,
)


def _consistent(solid: Solid) -> bool:
    """Every directed edge appears once and its reverse once (closed, oriented)."""
    t = solid.triangles
    edges: Counter[tuple[int, int]] = Counter()
    for a, b, c in t.tolist():
        edges.update([(a, b), (b, c), (c, a)])
    return all(n == 1 and edges.get((b, a)) == 1 for (a, b), n in edges.items())


def _circle(n: int, r: float, z: float, cx: float = 0.0) -> np.ndarray:
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(t), r * np.sin(t), np.full(n, z)])


def test_box_from_two_squares() -> None:
    sq = np.array([[0, 0], [10, 0], [10, 20], [0, 20]], float)
    lower = np.column_stack([sq, np.zeros(4)])
    upper = np.column_stack([sq[::-1], np.full(4, 5.0)])  # opposite direction on purpose
    solid = link_sections([lower, upper])
    assert solid.open_edges() == 0
    assert _consistent(solid)
    assert solid.volume() == pytest.approx(1000.0)


@settings(max_examples=25, deadline=None)
@given(st.integers(6, 40), st.integers(6, 40), st.integers(2, 5))
def test_cylinder_any_point_counts(n1: int, n2: int, sections: int) -> None:
    rings = [_circle(n1 if k % 2 else n2, 10.0, 5.0 * k) for k in range(sections)]
    solid = link_sections(rings)
    assert solid.open_edges() == 0
    assert _consistent(solid)
    h = 5.0 * (sections - 1)
    assert solid.volume() == pytest.approx(np.pi * 100 * h, rel=0.2)
    assert solid.inside(np.array([[0.0, 0.0, h / 2]])).all()


def test_vertical_sections_concave_outline() -> None:
    # L-shaped outlines on north-south sections (x constant).
    shape = np.array([[0, 0], [30, 0], [30, 10], [10, 10], [10, 30], [0, 30]], float)
    rings = [np.column_stack([np.full(6, x), shape]) for x in (0.0, 20.0, 40.0)]
    solid = link_sections(rings)
    assert solid.open_edges() == 0
    assert _consistent(solid)
    assert solid.volume() == pytest.approx(500.0 * 40.0)


def test_dxf_round_trip_and_storage(tmp_path: Path) -> None:
    path = tmp_path / "s.dxf"
    write_dxf(path, [Polyline("ORE", _circle(12, 5.0, z)) for z in (0.0, 10.0, 20.0)])
    strings = read_dxf_strings(path)
    assert len(strings) == 3
    assert all(s.closed and s.layer == "ORE" for s in strings)
    project = Project.create(tmp_path / "p.mcproj", "p")
    save_strings(project, "zon", strings)
    back = load_strings(project, "zon")
    assert np.allclose(back[1].points, strings[1].points)
    ordered = section_order(list(reversed(back)))
    assert [float(s.points[0, 2]) for s in ordered] == [0.0, 10.0, 20.0]
    project.close()


def test_lwpolyline_and_errors(tmp_path: Path) -> None:
    text = "\n".join(
        [
            "0",
            "SECTION",
            "2",
            "ENTITIES",
            "0",
            "LWPOLYLINE",
            "8",
            "A",
            "70",
            "1",
            "38",
            "100",
            "10",
            "0",
            "20",
            "0",
            "10",
            "5",
            "20",
            "0",
            "10",
            "5",
            "20",
            "5",
            "0",
            "ENDSEC",
            "0",
            "EOF",
        ]
    )
    path = tmp_path / "lw.dxf"
    path.write_text(text)
    (s,) = read_dxf_strings(path)
    assert s.closed
    assert s.points[:, 2].tolist() == [100.0, 100.0, 100.0]
    with pytest.raises(StringError):
        link_sections([s.points])
    with pytest.raises(StringError):
        section_order([String(s.points, False)])


def test_cli_import_and_link(tmp_path: Path) -> None:
    import io  # noqa: PLC0415

    from minecore.cli import main  # noqa: PLC0415

    path = tmp_path / "z.dxf"
    write_dxf(path, [Polyline("ORE", _circle(16, 5.0, z)) for z in (0.0, 10.0)])
    p = str(tmp_path / "c.mcproj")
    out = io.StringIO()
    assert main(["project", "create", p, "--name", "c"], out) == 0
    assert main(["strings", "import", p, "zon", str(path)], out) == 0
    assert main(["strings", "link", p, "zon", "zon_kati"], out) == 0
    assert "open edges 0" in out.getvalue()
