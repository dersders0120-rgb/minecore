"""Drillhole database contract and validation (ADR-0008)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from minecore.drillholes import (
    DrillholeDatabase,
    DrillholeError,
    FieldSpec,
    FieldType,
    Flag,
    Role,
    validate,
)
from minecore.project import Project

NAN = math.nan


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "D.mcproj", "D")


def collars() -> pa.Table:
    return pa.table(
        {
            "hole_id": ["A", "B", "B", "C", "a", "E", "F"],
            "x": [0.0, 10, 11, NAN, 5, 1, 2],
            "y": [0.0, 0, 0, 0, 5, 1, 2],
            "z": [100.0, 100, 100, 100, 100, 100, 100],
            "max_depth": [50.0, 40, 40, 30, NAN, -1, NAN],
            "_src_line": [2, 3, 4, 5, 6, 7, 8],
        }
    )


def surveys() -> pa.Table:
    return pa.table(
        {
            "hole_id": ["A", "A", "A", "Z", "E"],
            "depth": [0.0, 30, 60, 0, 0],
            "azimuth": [10.0, 90, 10, 0, 0],
            "dip": [-60.0, -60, -95, -90, -90],
            "_src_line": [2, 3, 4, 5, 6],
        }
    )


def assays() -> pa.Table:
    return pa.table(
        {
            "hole_id": ["A", "A", "A", "A", "A", "a", None],
            "from": [0.0, 1, 1.5, 4, 9, 0, 0],
            "to": [1.0, 2, 3, 3, 10, 1, 1],
            "Au": [0.5, 0.005, NAN, 2.0, 1.2e6, 1.0, 1.0],
            "Au__flag": np.array([0, 1, 3, 0, 0, 0, 0], dtype=np.uint8),
            "LİTO": ["AND", "AND", "SST", "SST", "AND", "AND", "AND"],
            "_src_line": [2, 3, 4, 5, 6, 7, 8],
        }
    )


AU = FieldSpec("Au", FieldType.NUMERIC, unit="ppm", quantity="grade")
LITO = FieldSpec("LİTO", FieldType.CATEGORY)


def build(project: Project) -> DrillholeDatabase:
    db = DrillholeDatabase.create(project, "Saha")
    db.put_table("collar", Role.COLLAR, collars())
    db.put_table("survey", Role.SURVEY, surveys())
    db.put_table("assay", Role.INTERVAL, assays(), (AU, LITO))
    return db


def test_tables_round_trip_with_canonical_types(project: Project) -> None:
    db = build(project)
    assay = db.table("assay")
    assert assay.column("Au").to_pylist()[:2] == [0.5, 0.005]
    assert pa.types.is_dictionary(assay.column("LİTO").type)
    assert db.table_info("assay").fields == (AU, LITO)
    assert sorted(db.tables()) == ["assay", "collar", "survey"]
    assert project.integrity_errors() == []


def test_flags_default_from_values_and_disagreement_is_refused(project: Project) -> None:
    db = DrillholeDatabase.create(project, "X")
    table = assays().drop(["Au__flag"])
    db.put_table("assay", Role.INTERVAL, table, (AU, LITO))
    assert db.table("assay").column("Au__flag").to_pylist()[:3] == [0, 0, Flag.MISSING]
    bad = assays().set_column(4, "Au__flag", pa.array(np.full(7, 3, np.uint8)))
    with pytest.raises(DrillholeError, match="value and flag disagree"):
        db.put_table("assay2", Role.INTERVAL, bad, (AU, LITO))


@pytest.mark.parametrize(
    ("table", "fields", "match"),
    [
        (pa.table({"hole_id": ["A"], "from": [0.0]}), (), "lacks required columns: to"),
        (assays(), (AU,), "undeclared columns: LİTO"),
        (assays().drop(["Au"]), (AU, LITO), "has no column"),
    ],
)
def test_structural_errors(
    project: Project, table: pa.Table, fields: tuple[FieldSpec, ...], match: str
) -> None:
    db = DrillholeDatabase.create(project, "X")
    with pytest.raises(DrillholeError, match=match):
        db.put_table("t", Role.INTERVAL, table, fields)


def test_field_names_and_units_are_checked() -> None:
    with pytest.raises(DrillholeError, match="reserved"):
        FieldSpec("FROM", FieldType.TEXT)
    with pytest.raises(DrillholeError, match="only numeric"):
        FieldSpec("c", FieldType.CATEGORY, unit="m")
    with pytest.raises(Exception, match="grade"):
        FieldSpec("d", FieldType.NUMERIC, unit="m", quantity="grade")


def test_one_collar_table_and_case_clashes(project: Project) -> None:
    db = build(project)
    with pytest.raises(DrillholeError, match="already has a collar"):
        db.put_table("collar2", Role.COLLAR, collars())
    with pytest.raises(DrillholeError, match="only by case"):
        db.put_table("ASSAY", Role.INTERVAL, assays(), (AU, LITO))


def test_put_is_undoable_and_files_are_collected(project: Project) -> None:
    db = build(project)
    first = db.table_info("assay").file
    db.put_table("assay", Role.INTERVAL, assays().slice(0, 2), (AU, LITO))
    assert db.table("assay").num_rows == 2
    project.undo()
    assert db.table_info("assay").file == first
    assert db.table("assay").num_rows == 7
    # The replaced file is still referenced by the redo branch.
    assert project.collect_garbage() == []
    db.remove_table("survey")
    assert "survey" not in db.tables()
    project.undo()
    assert db.table("survey").num_rows == 5


def test_garbage_collection_removes_unreferenced_and_temp_files(project: Project) -> None:
    build(project)
    stray = project.path / "tables" / "x" / "orphan.parquet"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"x")
    temp = project.path / "tables" / ".tmp-abc-file.parquet"
    temp.write_bytes(b"x")
    removed = project.collect_garbage()
    assert sorted(removed) == ["tables/.tmp-abc-file.parquet", "tables/x/orphan.parquet"]
    assert not stray.parent.exists()
    assert project.integrity_errors() == []


def test_source_files_are_archived_once(project: Project, tmp_path: Path) -> None:
    src = tmp_path / "collar.csv"
    src.write_text("hole_id,x,y,z\n", encoding="utf-8")
    db = DrillholeDatabase.create(project, "X")
    db.put_table("collar", Role.COLLAR, collars(), source=src, source_info={"delimiter": ","})
    info = db.table_info("collar")
    entry = db.manifest["sources"][info.source]
    assert entry["delimiter"] == ","
    assert (project.path / entry["file"]).read_bytes() == src.read_bytes()


def test_corruption_is_reported(project: Project) -> None:
    db = build(project)
    path = project.path / db.table_info("assay").file.file
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    path.write_bytes(bytes(data))
    assert any("SHA-256" in e for e in project.integrity_errors())


def codes(report: object, code: str) -> list[tuple[str | None, int | None]]:
    issues = report.issues  # type: ignore[attr-defined]
    return [(i.hole_id, i.src_line) for i in issues if i.code == code]


def test_validation_report(project: Project) -> None:
    report = validate(build(project))
    assert codes(report, "COLLAR_DUPLICATE") == [("B", 3), ("B", 4)]
    assert codes(report, "COLLAR_COORD_INVALID") == [("C", 5)]
    assert codes(report, "COLLAR_MAX_DEPTH_INVALID") == [("E", 7)]
    assert codes(report, "HOLE_ID_CASE") == [("A", None)]
    assert codes(report, "HOLE_ID_MISSING") == [(None, 8)]
    assert codes(report, "ORPHAN_ROW") == [("Z", 5)]
    assert codes(report, "SURVEY_DIP_INVALID") == [("A", 4)]
    assert codes(report, "SURVEY_DOGLEG") == [("A", 3)]
    assert sorted(codes(report, "INTERVAL_OVERLAP")) == [("A", 3), ("A", 4)]
    assert codes(report, "INTERVAL_REVERSED") == [("A", 5)]
    assert codes(report, "INTERVAL_GAP") == [("A", 6)]
    assert codes(report, "VALUE_OUT_OF_RANGE") == [("A", 6)]
    assert report.count("VALUE_QUALIFIED") == 0  # flagged rows overlap, so are excluded
    assert codes(report, "SURVEY_NONE") == [("F", None), ("a", None)]
    assert codes(report, "HOLE_NO_DATA") == [("F", None)]
    assert codes(report, "HOLE_DEPTH_DERIVED") == [("a", None)]

    assert report.holes == ("A", "F", "a")
    assert report.hole_depth["a"] == 1.0
    assert math.isnan(report.hole_depth["F"])
    # A: rows 0 (ok), 1 and 2 overlap, 3 reversed, 4 ok; a: ok; None: missing id.
    assert report.usable["assay"].tolist() == [True, False, False, False, True, True, False]
    assert report.usable["survey"].tolist() == [True, True, False, False, False]
    assert report.usable["collar"].tolist() == [True, False, False, False, True, False, True]
    assert report.to_json()["usable_holes"] == 3


def test_validation_requires_a_collar_table(project: Project) -> None:
    db = DrillholeDatabase.create(project, "X")
    with pytest.raises(DrillholeError, match="no collar table"):
        validate(db)
