"""Solids: readers, closure, volume and inside test against analytic shapes."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from minecore.blockmodel import BlockModel, Grid
from minecore.project import Project
from minecore.solids import (
    Solid,
    SolidError,
    flag_blocks,
    load_solid,
    read_dxf_3dfaces,
    read_obj,
    read_solid,
    read_stl,
    save_solid,
)

BOX_V = [
    (0, 0, 0),
    (10, 0, 0),
    (10, 20, 0),
    (0, 20, 0),
    (0, 0, 5),
    (10, 0, 5),
    (10, 20, 5),
    (0, 20, 5),
]
BOX_QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def _box_obj(path: Path) -> Path:
    lines = [f"v {x} {y} {z}" for x, y, z in BOX_V]
    lines += ["f " + " ".join(str(i + 1) for i in q) for q in BOX_QUADS]
    path.write_text("\n".join(lines))
    return path


def _icosphere(radius: float, levels: int = 3) -> Solid:
    t = (1 + 5**0.5) / 2
    v = [
        (-1, t, 0),
        (1, t, 0),
        (-1, -t, 0),
        (1, -t, 0),
        (0, -1, t),
        (0, 1, t),
        (0, -1, -t),
        (0, 1, -t),
        (t, 0, -1),
        (t, 0, 1),
        (-t, 0, -1),
        (-t, 0, 1),
    ]
    f = [
        (0, 11, 5),
        (0, 5, 1),
        (0, 1, 7),
        (0, 7, 10),
        (0, 10, 11),
        (1, 5, 9),
        (5, 11, 4),
        (11, 10, 2),
        (10, 7, 6),
        (7, 1, 8),
        (3, 9, 4),
        (3, 4, 2),
        (3, 2, 6),
        (3, 6, 8),
        (3, 8, 9),
        (4, 9, 5),
        (2, 4, 11),
        (6, 2, 10),
        (8, 6, 7),
        (9, 8, 1),
    ]
    verts = [np.array(p, float) / np.linalg.norm(p) for p in v]
    for _ in range(levels):
        cache: dict[tuple[int, int], int] = {}

        def mid(a: int, b: int, cache: dict[tuple[int, int], int] = cache) -> int:
            key = (min(a, b), max(a, b))
            if key not in cache:
                m = verts[a] + verts[b]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        new = []
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            new += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = new
    return Solid(np.array(verts) * radius, np.array(f, np.int64))


def test_obj_box_closed_volume_and_inside(tmp_path: Path) -> None:
    box = read_obj(_box_obj(tmp_path / "box.obj"))
    assert box.open_edges() == 0
    assert box.volume() == pytest.approx(1000.0)
    rng = np.random.default_rng(0)
    pts = rng.uniform(-5, 25, (5000, 3))
    expected = (
        (pts[:, 0] > 0)
        & (pts[:, 0] < 10)
        & (pts[:, 1] > 0)
        & (pts[:, 1] < 20)
        & (pts[:, 2] > 0)
        & (pts[:, 2] < 5)
    )
    np.testing.assert_array_equal(box.inside(pts), expected)


def test_grid_aligned_points_on_shared_edges(tmp_path: Path) -> None:
    box = read_obj(_box_obj(tmp_path / "box.obj"))
    # Points exactly above the box's diagonal edges and vertices.
    pts = np.array(
        [[5.0, 10.0, 2.5], [0.0 + 1e-9, 0.0 + 1e-9, 2.5], [10.0, 20.0, 9.0], [2.5, 5.0, 2.5]]
    )
    assert box.inside(pts).tolist() == [True, True, False, True]


def test_stl_binary_and_ascii_sphere(tmp_path: Path) -> None:
    sphere = _icosphere(10.0)
    tri = sphere.vertices[sphere.triangles]
    binary = tmp_path / "s.stl"
    with binary.open("wb") as fh:
        fh.write(b"\0" * 80 + struct.pack("<I", len(tri)))
        for t in tri:
            fh.write(struct.pack("<3f", 0, 0, 0) + t.astype("<f4").tobytes() + b"\0\0")
    ascii_path = tmp_path / "a.stl"
    body = ["solid s"]
    for t in tri:
        body += ["facet normal 0 0 0", "outer loop"] + [f"vertex {x} {y} {z}" for x, y, z in t]
        body += ["endloop", "endfacet"]
    ascii_path.write_text("\n".join([*body, "endsolid s"]))
    for s in (read_stl(binary), read_stl(ascii_path)):
        assert s.open_edges() == 0
        # Inscribed polyhedron: volume a bit below the sphere's.
        assert 0.95 * (4 / 3 * np.pi * 1000) < s.volume() < 4 / 3 * np.pi * 1000
        rng = np.random.default_rng(1)
        pts = rng.uniform(-12, 12, (4000, 3))
        r = np.linalg.norm(pts, axis=1)
        clear = (r < 9.5) | (r > 10.01)  # skip the thin shell between facets and sphere
        np.testing.assert_array_equal(s.inside(pts)[clear], (r < 9.5)[clear])


def test_dxf_3dface_box(tmp_path: Path) -> None:
    out = ["0", "SECTION", "2", "ENTITIES"]
    for q in BOX_QUADS:
        out += ["0", "3DFACE", "8", "0"]
        for k, idx in enumerate(q):
            x, y, z = BOX_V[idx]
            out += [str(10 + k), str(x), str(20 + k), str(y), str(30 + k), str(z)]
    out += ["0", "ENDSEC", "0", "EOF"]
    path = tmp_path / "box.dxf"
    path.write_text("\n".join(out))
    box = read_dxf_3dfaces(path)
    assert box.open_edges() == 0
    assert box.volume() == pytest.approx(1000.0)
    with pytest.raises(SolidError):
        read_solid(tmp_path / "x.dwg")


def test_store_and_flag_blocks(tmp_path: Path) -> None:
    project = Project.create(tmp_path / "p.mcproj", "p")
    box = read_obj(_box_obj(tmp_path / "box.obj"))
    save_solid(project, "box", box)
    loaded = load_solid(project, "box")
    np.testing.assert_allclose(loaded.vertices, box.vertices)
    grid = Grid((-10.0, -10.0, -5.0), (2.5, 2.5, 2.5), (16, 20, 6))
    model = BlockModel.create(project, "bm", grid)
    n = flag_blocks(model, loaded, "dom_box")
    # Block centroids inside [0,10]x[0,20]x[0,5]: 4 x 8 x 2.
    assert n == 4 * 8 * 2
    project.close()
