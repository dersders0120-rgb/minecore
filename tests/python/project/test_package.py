"""Tests for the .mcproj project package: create, save, reopen, lock, migrate."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from minecore import cli
from minecore.project import (
    APPLICATION_ID,
    MigrationError,
    Project,
    ProjectError,
    ProjectFormatError,
    ProjectLockedError,
    ProjectVersionError,
    ReadOnlyProjectError,
)
from minecore.project.migrations import Migration, load_migrations


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def pkg(tmp_path: Path) -> Path:
    return tmp_path / "Kisladag.mcproj"


def test_create_builds_a_valid_package(pkg: Path) -> None:
    with Project.create(pkg, "  Kışladağ Altın  ") as project:
        info = project.info
        assert info.name == "Kışladağ Altın"
        assert info.schema_version == len(load_migrations())
        assert project.integrity_errors() == []
    for sub in ("tables", "blocks", "geometry"):
        assert (pkg / sub).is_dir()
    with sqlite3.connect(pkg / "project.sqlite") as raw:
        assert raw.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        history = raw.execute("SELECT version, name FROM schema_migrations").fetchall()
    assert history == [(1, "initial")]


def test_reopen_returns_saved_state(pkg: Path) -> None:
    with Project.create(pkg, "Demo") as project:
        project_id = project.info.project_id
        project.set_setting("display.length_unit", "m")
        project.set_setting("grid", {"origin": [500000.0, 4500000.0], "rotation_deg": 12.5})
        hole_set = project.register_object("drillhole_set", "Phase 1", metadata={"holes": 3})
        project.register_object(
            "table", "collars", parent_id=hole_set.object_id, storage_path="tables/collars.parquet"
        )
    with Project.open(pkg) as project:
        assert project.info.project_id == project_id
        assert project.get_setting("grid") == {
            "origin": [500000.0, 4500000.0],
            "rotation_deg": 12.5,
        }
        assert project.get_setting("missing", default=7) == 7
        records = project.objects()
        assert [(r.kind, r.name) for r in records] == [
            ("drillhole_set", "Phase 1"),
            ("table", "collars"),
        ]
        assert records[0].metadata == {"holes": 3}
        assert records[1].parent_id == records[0].object_id
        assert project.objects("table")[0].storage_path == "tables/collars.parquet"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"kind": "Drillholes", "name": "x"}, "invalid object kind"),
        ({"kind": "table", "name": "  "}, "name must not be empty"),
        ({"kind": "table", "name": "x", "storage_path": "../escape"}, "relative inside"),
        ({"kind": "table", "name": "x", "storage_path": "/abs/path"}, "relative inside"),
        ({"kind": "table", "name": "x", "storage_path": "tables\\win"}, "relative inside"),
        ({"kind": "table", "name": "x", "parent_id": "no-such-id"}, "cannot register"),
    ],
)
def test_catalogue_rejects_invalid_objects(pkg: Path, kwargs: dict[str, Any], message: str) -> None:
    with Project.create(pkg, "Demo") as project, pytest.raises(ProjectError, match=message):
        project.register_object(**kwargs)


def test_duplicate_object_names_are_rejected_per_kind(pkg: Path) -> None:
    with Project.create(pkg, "Demo") as project:
        project.register_object("surface", "topo")
        project.register_object("solid", "topo")  # same name, other kind: fine
        with pytest.raises(ProjectError, match="cannot register"):
            project.register_object("surface", "topo")


def test_create_refuses_bad_targets(tmp_path: Path) -> None:
    with pytest.raises(ProjectError, match=r"\.mcproj"):
        Project.create(tmp_path / "noext", "Demo")
    occupied = tmp_path / "occupied.mcproj"
    occupied.mkdir()
    (occupied / "file.txt").write_text("x")
    with pytest.raises(ProjectError, match="not an empty directory"):
        Project.create(occupied, "Demo")
    with pytest.raises(ProjectError, match="name must not be empty"):
        Project.create(tmp_path / "blank.mcproj", " ")


def test_failed_create_leaves_nothing_behind(tmp_path: Path) -> None:
    target = tmp_path / "broken.mcproj"
    bad = (Migration(1, "broken", "CREATE TABLE ;"),)
    with pytest.raises(MigrationError):
        Project.create(target, "Demo", migrations=bad)
    assert not target.exists()


def test_single_writer_lock(pkg: Path) -> None:
    writer = Project.create(pkg, "Demo")
    try:
        with pytest.raises(ProjectLockedError, match="open for writing by process"):
            Project.open(pkg)
        with Project.open(pkg, read_only=True) as reader:
            assert reader.info.name == "Demo"
            with pytest.raises(ReadOnlyProjectError):
                reader.set_setting("k", 1)
            with pytest.raises(ReadOnlyProjectError):
                reader.register_object("table", "t")
    finally:
        writer.close()
    with Project.open(pkg) as again:
        assert again.info.name == "Demo"


def test_lock_is_released_when_holder_process_dies(pkg: Path) -> None:
    Project.create(pkg, "Demo").close()
    script = (
        "import os, sys; from minecore.project import Project; "
        "p = Project.open(sys.argv[1]); os._exit(0)"  # exit without close()
    )
    subprocess.run([sys.executable, "-c", script, str(pkg)], check=True)
    with Project.open(pkg) as project:  # the OS dropped the dead process's lock
        assert project.info.name == "Demo"


def test_closed_project_refuses_use(pkg: Path) -> None:
    project = Project.create(pkg, "Demo")
    project.close()
    project.close()  # idempotent
    assert project.closed
    with pytest.raises(ProjectError, match="closed"):
        _ = project.info


def test_open_rejects_non_projects(tmp_path: Path) -> None:
    with pytest.raises(ProjectFormatError):
        Project.open(tmp_path / "missing.mcproj")
    foreign = tmp_path / "foreign.mcproj"
    foreign.mkdir()
    with sqlite3.connect(foreign / "project.sqlite") as raw:
        raw.execute("CREATE TABLE t (x)")
    before = sha256(foreign / "project.sqlite")
    with pytest.raises(ProjectFormatError, match="not a MineCore project database"):
        Project.open(foreign)
    assert sha256(foreign / "project.sqlite") == before
    assert not (foreign / "project.lock").exists()
    garbage = tmp_path / "garbage.mcproj"
    garbage.mkdir()
    (garbage / "project.sqlite").write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(ProjectFormatError):
        Project.open(garbage)


def test_newer_format_is_refused_without_modification(pkg: Path) -> None:
    Project.create(pkg, "Demo").close()
    with sqlite3.connect(pkg / "project.sqlite") as raw:
        raw.execute("PRAGMA user_version = 99")
    raw.close()
    before = sha256(pkg / "project.sqlite")
    for read_only in (False, True):
        with pytest.raises(ProjectVersionError, match="newer than this MineCore"):
            Project.open(pkg, read_only=read_only)
    assert sha256(pkg / "project.sqlite") == before


V2 = Migration(
    2,
    "object_tags",
    "ALTER TABLE objects ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]';",
)


def test_migration_upgrades_and_keeps_a_backup(pkg: Path) -> None:
    with Project.create(pkg, "Demo") as project:
        project.register_object("surface", "topo")
    v1_hash = sha256(pkg / "project.sqlite")
    steps = (*load_migrations(), V2)

    with pytest.raises(ProjectVersionError, match="open it read-write once"):
        Project.open(pkg, read_only=True, migrations=steps)

    with Project.open(pkg, migrations=steps) as project:
        assert project.schema_version == 2
        assert project.objects()[0].name == "topo"  # data survived
        backup = project.migration_backup
    assert backup is not None
    assert backup.parent.name == "backups"
    with sqlite3.connect(backup) as raw:
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 1
        assert raw.execute("SELECT count(*) FROM objects").fetchone()[0] == 1
    raw.close()
    with sqlite3.connect(pkg / "project.sqlite") as raw:
        history = raw.execute("SELECT version FROM schema_migrations ORDER BY 1").fetchall()
        columns = [r[1] for r in raw.execute("PRAGMA table_info(objects)")]
    raw.close()
    assert history == [(1,), (2,)]
    assert "tags_json" in columns
    assert sha256(pkg / "project.sqlite") != v1_hash


def test_failed_migration_rolls_back_completely(pkg: Path) -> None:
    Project.create(pkg, "Demo").close()
    broken = Migration(
        2,
        "half_done",
        "CREATE TABLE extra (x INTEGER);\nINSERT INTO no_such_table VALUES (1);",
    )
    with pytest.raises(MigrationError, match="0002_half_done"):
        Project.open(pkg, migrations=(*load_migrations(), broken))
    with Project.open(pkg) as project:  # still a healthy v1 project, and unlocked
        assert project.schema_version == 1
        tables = {
            r[0]
            for r in project._db().execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "extra" not in tables


def test_bundled_migrations_are_contiguous_and_transaction_free() -> None:
    steps = load_migrations()
    assert [m.version for m in steps] == list(range(1, len(steps) + 1))
    for m in steps:
        assert "COMMIT" not in m.sql.upper()


def test_cli_create_and_info(pkg: Path) -> None:
    out = io.StringIO()
    assert cli.main(["project", "create", str(pkg), "--name", "Çöpler"], out=out) == 0
    assert "created" in out.getvalue()
    out = io.StringIO()
    assert cli.main(["project", "info", str(pkg), "--json"], out=out) == 0
    data = json.loads(out.getvalue())
    assert data["name"] == "Çöpler"
    assert data["objects"] == "0"


def test_cli_reports_errors_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["project", "info", str(tmp_path / "none.mcproj")], out=io.StringIO()) == 1
    assert "error: not a MineCore project" in capsys.readouterr().err
