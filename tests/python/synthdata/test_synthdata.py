from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from synthdata import Config, generate, write
from synthdata.config import Collar, Dataset, Plane, Station
from synthdata.desurvey import position_at

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def _write_clean(tmp_path: Path, holes: int = 20) -> tuple[Path, Dataset]:
    dataset = generate(Config(seed=123, holes=holes, errors="none"))
    out = tmp_path / "clean"
    write(dataset, out)
    return out, dataset


def test_desurvey_reference_cases() -> None:
    path = Path(__file__).resolve().parents[2] / "reference" / "desurvey" / "cases.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            depths = [float(v) for v in row["station_depths"].split("|")]
            azimuths = [float(v) for v in row["station_azimuths"].split("|")]
            dips = [float(v) for v in row["station_dips"].split("|")]
            stations = [
                Station(depth=d, azimuth=a, dip=p)
                for d, a, p in zip(depths, azimuths, dips, strict=True)
            ]
            collar = Collar(
                "X",
                float(row["collar_x"]),
                float(row["collar_y"]),
                float(row["collar_z"]),
                max(depths),
                "DD",
            )
            x, y, z = position_at(stations, collar, float(row["depth"]))
            assert abs(x - float(row["x"])) <= 1e-6
            assert abs(y - float(row["y"])) <= 1e-6
            assert abs(z - float(row["z"])) <= 1e-6


def test_determinism(tmp_path: Path) -> None:
    a = generate(Config(seed=7, holes=8, errors="none"))
    b = generate(Config(seed=7, holes=8, errors="none"))
    c = generate(Config(seed=8, holes=8, errors="none"))
    pa = write(a, tmp_path / "a")
    pb = write(b, tmp_path / "b")
    pc = write(c, tmp_path / "c")
    for one, two in zip(pa, pb, strict=False):
        assert one.read_bytes() == two.read_bytes()
    assert any(one.read_bytes() != two.read_bytes() for one, two in zip(pa, pc, strict=False))


def test_clean_invariants(tmp_path: Path) -> None:
    out, _dataset = _write_clean(tmp_path, holes=30)
    collars: dict[str, float] = {}
    with (out / "collar.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            collars[row["hole_id"]] = float(row["total_depth"])
    survey_depths: dict[str, list[float]] = {}
    with (out / "survey.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            hole = row["hole_id"]
            assert hole in collars
            survey_depths.setdefault(hole, []).append(float(row["depth"]))
    for hole, depths in survey_depths.items():
        assert depths[0] == 0.0
        assert depths == sorted(depths)
        assert depths[-1] <= collars[hole] + 1e-9

    lith_by_hole: dict[str, list[tuple[float, float]]] = {}
    with (out / "lithology.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            hole = row["hole_id"]
            assert hole in collars
            f = float(row["from_m"])
            t = float(row["to_m"])
            assert f < t
            assert t <= collars[hole] + 1e-9
            lith_by_hole.setdefault(hole, []).append((f, t))

    with (out / "assay.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    last_by_hole: dict[str, tuple[float, float]] = {}
    for row in rows:
        hole = row["hole_id"]
        assert hole in collars
        f = float(row["from_m"])
        t = float(row["to_m"])
        assert f < t
        assert t <= collars[hole] + 1e-9
        assert t - f >= 0.3 - 1e-9
        if hole in last_by_hole:
            _pf, pt = last_by_hole[hole]
            assert f >= pt - 1e-9
        last_by_hole[hole] = (f, t)
        mid = (f + t) / 2.0
        assert any(a <= mid <= b for a, b in lith_by_hole[hole])

    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    topo = truth["topography"]
    with (out / "collar.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            x = float(row["x"])
            y = float(row["y"])
            z = float(row["z"])
            expected = (
                topo["z0"]
                + topo["gx"] * (x - topo["xc"])
                + topo["gy"] * (y - topo["yc"])
                + topo["amplitude"]
                * __import__("math").sin(2.0 * 3.141592653589793 * (x - topo["xc"]) / topo["lx"])
                * __import__("math").cos(2.0 * 3.141592653589793 * (y - topo["yc"]) / topo["ly"])
            )
            assert abs(z - expected) < 0.02


def test_geometry(tmp_path: Path) -> None:
    out, dataset = _write_clean(tmp_path, holes=25)
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    ore = truth["ore_plane"]
    plane = Plane.create(
        float(ore["dip_dir"]),
        float(ore["dip"]),
        (float(ore["ref"][0]), float(ore["ref"][1]), float(ore["ref"][2])),
        float(ore["thickness"]),
    )
    with (out / "samples_truth.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            inside = row["inside"] == "1"
            s = plane.signed(float(row["mid_x"]), float(row["mid_y"]), float(row["mid_z"]))
            assert (0.0 <= s <= plane.thickness) == inside

    survey: dict[str, list[Station]] = {}
    with (out / "survey.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            survey.setdefault(row["hole_id"], []).append(
                Station(float(row["depth"]), float(row["azimuth"]), float(row["dip"]))
            )
    collar = {c.hole_id: c for c in dataset.collars}
    with (out / "lithology.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            f = float(row["from_m"])
            t = float(row["to_m"])
            for depth in (f, t):
                if depth <= 0.0 or depth >= collar[row["hole_id"]].total_depth:
                    continue
                pos = position_at(survey[row["hole_id"]], collar[row["hole_id"]], depth)
                s = plane.signed(*pos)
                if min(abs(s), abs(s - plane.thickness)) < 0.0011:
                    break
            else:
                if row["lith_code"] == "QV":
                    pytest.fail("QV contact not on plane")


def test_dialect_and_header_readback(tmp_path: Path) -> None:
    dataset = generate(Config(seed=5, holes=10, errors="none"))
    base = tmp_path / "base"
    write(dataset, base)
    with (base / "collar.csv").open(newline="", encoding="utf-8") as handle:
        canonical = list(csv.DictReader(handle))
    for dialect, encoding, delimiter, decimal in (
        ("std", "utf-8", ",", "."),
        ("tr", "utf-8-sig", ";", ","),
        ("tr1254", "cp1254", ";", ","),
    ):
        for headers in ("canonical", "short", "turkish"):
            out = tmp_path / f"{dialect}_{headers}"
            write(dataset, out, dialect=dialect, header_style=headers)
            with (out / "collar.csv").open(newline="", encoding=encoding) as handle:
                rows = list(csv.reader(handle, delimiter=delimiter))
            assert len(rows) == len(canonical) + 1
            for a, b in zip(rows[1:], canonical, strict=False):
                for index in (1, 2, 3, 4):
                    assert a[index].replace(decimal, ".") == b[list(b)[index]]


def test_issues_all_codes(tmp_path: Path) -> None:
    dataset = generate(Config(seed=44, holes=30, errors="all"))
    out = tmp_path / "issues"
    write(dataset, out)
    issues = json.loads((out / "issues.json").read_text(encoding="utf-8"))
    codes = {issue["code"] for issue in issues}
    required = {
        "OVERLAP",
        "FROM_GE_TO",
        "BEYOND_DEPTH",
        "DUPLICATE_COLLAR",
        "NO_COLLAR",
        "SURVEY_BEYOND_DEPTH",
        "AZIMUTH_RANGE",
        "DIP_RANGE",
        "DIP_SIGN",
        "DUPLICATE_SURVEY_DEPTH",
        "NON_NUMERIC",
        "UNKNOWN_LITH",
        "HOLE_ID_VARIANT",
        "NS",
        "IS",
        "LNR",
        "<0.005",
        "-0.005",
    }
    assert required <= codes
    for issue in issues:
        path = out / issue["file"]
        lines = path.read_text(encoding="utf-8").splitlines()
        line = lines[issue["line"] - 1]
        if issue["code"] == "AZIMUTH_RANGE":
            assert "365" in line or "-10" in line
        if issue["code"] == "DIP_RANGE":
            assert "95" in line
        if issue["code"] == "NON_NUMERIC":
            assert "abc" in line


def test_cli(tmp_path: Path) -> None:
    script = ROOT / "tools" / "synth_drillholes.py"
    out = tmp_path / "cli"
    result = subprocess.run(
        [sys.executable, str(script), "--seed", "1", "--holes", "5", "--out", str(out)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert (out / "collar.csv").exists()
    bad = subprocess.run(
        [sys.executable, str(script), "--seed", "1", "--holes", "0", "--out", str(out)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2


def test_performance_500_holes(tmp_path: Path) -> None:
    start = time.perf_counter()
    dataset = generate(Config(seed=2, holes=500, errors="none"))
    write(dataset, tmp_path / "perf")
    elapsed = time.perf_counter() - start
    assert elapsed < 30.0
