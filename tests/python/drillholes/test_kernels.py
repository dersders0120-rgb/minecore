"""Desurvey and compositing kernels against analytic and independent references."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pytest

from minecore.drillholes.compositing import LastComposite, composite_by_domain, composite_fixed
from minecore.drillholes.desurvey import desurvey
from minecore.drillholes.model import DrillholeError

REF = Path(__file__).resolve().parents[2] / "reference" / "compositing" / "cases.csv"


def test_vertical_and_straight_holes() -> None:
    xyz = desurvey(
        (100.0, 200.0, 50.0),
        np.array([0.0]),
        np.array([0.0]),
        np.array([-90.0]),
        np.array([0.0, 10.0]),
    )
    np.testing.assert_allclose(xyz, [[100, 200, 50], [100, 200, 40]], atol=1e-12)
    xyz = desurvey(
        (0.0, 0.0, 0.0),
        np.array([0.0, 50.0]),
        np.array([90.0, 90.0]),
        np.array([-30.0, -30.0]),
        np.array([100.0]),
    )
    np.testing.assert_allclose(xyz, [[100 * math.cos(math.radians(30)), 0, -50]], atol=1e-9)


def test_circular_arc_is_exact() -> None:
    # Hole building from -90 to -30 dip at a constant rate in the north-up plane:
    # a circle of radius R; minimum curvature is exact for circular arcs.
    radius = 200.0
    md = np.linspace(0.0, radius * math.radians(60), 7)
    theta = md / radius  # angle turned from vertical
    dip = -90.0 + np.degrees(theta)
    xyz = desurvey((0.0, 0.0, 0.0), md, np.zeros_like(md), dip, md + 0.37 * (md[1] - md[0]))
    t = (md + 0.37 * (md[1] - md[0])) / radius
    expected = np.column_stack([np.zeros_like(t), radius * (1 - np.cos(t)), -radius * np.sin(t)])
    inside = t <= theta[-1]
    np.testing.assert_allclose(xyz[inside], expected[inside], atol=1e-9)


def test_desurvey_rejects_bad_stations() -> None:
    with pytest.raises(DrillholeError, match="strictly increasing"):
        desurvey((0, 0, 0), np.array([0.0, 0.0]), np.zeros(2), np.full(2, -90.0), np.zeros(1))


def _cases() -> dict[str, list[dict[str, str]]]:
    cases: dict[str, list[dict[str, str]]] = {}
    with REF.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cases.setdefault(row["case_id"], []).append(row)
    return cases


CASES = _cases()


@pytest.mark.parametrize("case_id", [c for c, r in CASES.items() if r[0]["mode"] != "bench"])
def test_compositing_matches_independent_reference(case_id: str) -> None:
    rows = CASES[case_id]
    params = json.loads(rows[0]["params_json"])
    samples = json.loads(rows[0]["samples_json"])
    start = np.array([s["from"] for s in samples], float)
    end = np.array([s["to"] for s in samples], float)
    grade = np.array([math.nan if s["grade"] is None else s["grade"] for s in samples], float)
    if rows[0]["comp_from"] == "":
        with pytest.raises(DrillholeError, match="overlap"):
            composite_fixed(start, end, grade, params["L"])
        return
    if rows[0]["mode"] == "domain":
        got = composite_by_domain(
            start,
            end,
            grade,
            [s["domain"] for s in samples],
            params["L"],
            min_coverage=params["min_coverage"],
        )
    else:
        got = composite_fixed(
            start,
            end,
            grade,
            params["L"],
            origin=params["origin"],
            min_coverage=params["min_coverage"],
            last=LastComposite(params["last_mode"]),
        )
    assert len(got) == len(rows)
    for c, r in zip(got, rows, strict=True):
        assert c.start == pytest.approx(float(r["comp_from"]), abs=1e-9)
        assert c.end == pytest.approx(float(r["comp_to"]), abs=1e-9)
        assert c.sampled_length == pytest.approx(float(r["sampled_length"]), abs=1e-9)
        assert c.coverage == pytest.approx(float(r["coverage"]), abs=1e-9)
        if r["grade"] == "":
            assert math.isnan(c.grade)
        else:
            assert c.grade == pytest.approx(float(r["grade"]), abs=1e-9)


@pytest.mark.parametrize(("dialect", "headers"), [("std", "short"), ("tr1254", "turkish")])
def test_import_validate_composite_end_to_end(tmp_path: Path, dialect: str, headers: str) -> None:
    from minecore.drillholes import DrillholeDatabase, Role, validate  # noqa: PLC0415
    from minecore.drillholes.importer import import_folder  # noqa: PLC0415
    from minecore.drillholes.workflow import composite_table, hole_geometry  # noqa: PLC0415
    from minecore.project import Project  # noqa: PLC0415
    from synthdata import Config, generate, write  # noqa: PLC0415

    data = tmp_path / "data"
    write(generate(Config(seed=3, holes=5)), data, dialect=dialect, header_style=headers)
    project = Project.create(tmp_path / "P.mcproj", "P")
    db = DrillholeDatabase.create(project, "dh")
    results = import_folder(db, data)
    roles = {r.table.name: r.table.role for r in results}
    assert roles["collar"] is Role.COLLAR
    assert roles["survey"] is Role.SURVEY
    assert roles["assay"] is Role.INTERVAL
    report = validate(db)
    assert len(report.holes) == 5
    geometry = hole_geometry(db, report)
    truth = list(csv.DictReader((data / "samples_truth.csv").open(encoding="utf-8")))
    worst = 0.0
    for row in truth:
        mid = (float(row["from_m"]) + float(row["to_m"])) / 2
        xyz = geometry[row["hole_id"]].at(np.array([mid]))[0]
        expected = [float(row["mid_x"]), float(row["mid_y"]), float(row["mid_z"])]
        worst = max(worst, float(np.abs(xyz - expected).max()))
    assert worst < 0.01
    grade_field = next(f.name for f in db.table_info("assay").fields if f.unit == "ppm")
    result = composite_table(db, "assay", [grade_field], 2.0, report=report)
    assert result.table.num_rows > 0


def test_excel_workbook_imports_like_csv(tmp_path: Path) -> None:
    """Each sheet of an .xlsx is a table; values match the CSV import exactly."""
    from openpyxl import Workbook  # noqa: PLC0415

    from minecore.drillholes import DrillholeDatabase  # noqa: PLC0415
    from minecore.drillholes.importer import import_folder  # noqa: PLC0415
    from minecore.project import Project  # noqa: PLC0415

    book = Workbook()
    collar = book.active
    assert collar is not None
    collar.title = "collar"
    collar.append(["KUYU_NO", "DOĞU", "KUZEY", "KOT", "DERİNLİK"])
    collar.append(["H1", 500000.5, 4400000.25, 1200.0, 50.0])
    collar.append(["H2", 500050.0, 4400010.0, 1201.5, 40.0])
    survey = book.create_sheet("survey")
    survey.append(["KUYU_NO", "DERİNLİK", "AZİMUT", "EĞİM"])
    survey.append(["H1", 0, 90, -60])
    survey.append(["H2", 0, 0, -90])
    assay = book.create_sheet("assay")
    assay.append(["KUYU_NO", "BAŞLANGIÇ", "BİTİŞ", "AU_PPM"])
    assay.append(["H1", 0, 2, 0.35])
    assay.append(["H1", 2, 4.5, 1.2])
    assay.append(["H2", 0, 3, 0.01])
    folder = tmp_path / "data"
    folder.mkdir()
    book.save(folder / "sondaj.xlsx")
    (folder / "~$sondaj.xlsx").write_bytes(b"lock")
    project = Project.create(tmp_path / "x.mcproj", "x")
    db = DrillholeDatabase.create(project, "db")
    results = import_folder(db, folder)
    assert [r.table.name for r in results] == ["collar", "survey", "assay"]
    a = db.table("assay")
    assert a.column("to").to_pylist() == [2.0, 4.5, 3.0]
    assert a.column("AU_PPM").to_pylist() == [0.35, 1.2, 0.01]
    c = db.table("collar")
    assert c.column("x").to_pylist() == [500000.5, 500050.0]
    project.close()
