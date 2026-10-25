"""Operation log, undo/redo and cancellation (ADR-0007)."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from minecore import cli
from minecore.coords import CrsDefinition, ProjectedSystem
from minecore.jobs import CancellationToken, CancelledError, Progress, ProgressEvent
from minecore.project import Project, ProjectError, ReadOnlyProjectError


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.create(tmp_path / "Ocak.mcproj", "Ocak")


def snapshot(p: Project) -> tuple[object, ...]:
    db = p._db()
    return (
        db.execute("SELECT * FROM settings ORDER BY key").fetchall(),
        db.execute("SELECT * FROM objects ORDER BY object_id").fetchall(),
    )


def test_every_change_is_logged_with_provenance(project: Project) -> None:
    project.register_object("surface", "topo")
    project.set_setting("cutoff_g_t", 0.5)
    ops = project.history()
    assert [o.kind for o in ops] == ["register_object", "set_setting"]
    assert all(o.state == "done" and o.changes == 1 for o in ops)
    assert ops[0].description == "Add surface 'topo'"
    assert ops[1].parameters == {"key": "cutoff_g_t"}
    assert ops[0].user_name
    assert ops[0].minecore_version


def test_undo_redo_restore_exact_states(project: Project) -> None:
    states = [snapshot(project)]
    parent = project.register_object("surface", "topo")
    states.append(snapshot(project))
    project.register_object("solid", "ore", parent_id=parent.object_id, metadata={"a": 1})
    states.append(snapshot(project))
    project.set_setting("x", 1)
    states.append(snapshot(project))
    project.set_setting("x", {"nested": [1, 2]})
    states.append(snapshot(project))

    for expected in reversed(states[:-1]):
        assert project.undo() is not None
        assert snapshot(project) == expected
    assert project.undo() is None
    assert not project.can_undo()
    for expected in states[1:]:
        assert project.redo() is not None
        assert snapshot(project) == expected
    assert project.redo() is None


def test_new_operation_discards_the_redo_branch(project: Project) -> None:
    project.set_setting("a", 1)
    project.set_setting("b", 2)
    project.undo()
    assert project.can_redo()
    project.set_setting("c", 3)
    assert not project.can_redo()
    assert [(o.parameters["key"], o.state) for o in project.history()] == [
        ("a", "done"),
        ("c", "done"),
    ]


def test_grouped_operation_is_one_undo_step(project: Project) -> None:
    before = snapshot(project)
    with project.operation("import_surfaces", "Import 2 surfaces", {"files": 2}):
        project.register_object("surface", "topo")
        project.register_object("surface", "pit")
        project.set_setting("last_import", "x")
    ops = project.history()
    assert [(o.kind, o.changes) for o in ops] == [("import_surfaces", 3)]
    project.undo()
    assert snapshot(project) == before


def test_failed_operation_changes_and_logs_nothing(project: Project) -> None:
    project.set_setting("keep", True)
    before = snapshot(project)

    def half_done() -> None:
        with project.operation("bulk", "Half done"):
            project.register_object("surface", "topo")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        half_done()
    assert snapshot(project) == before
    assert len(project.history()) == 1


def test_empty_operation_is_not_logged_and_keeps_redo(project: Project) -> None:
    project.set_setting("a", 1)
    project.undo()
    with project.operation("noop", "Nothing"):
        pass
    assert project.can_redo()


def test_writes_outside_operations_are_refused(project: Project) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="outside an operation"):
        project._writable().execute("INSERT INTO settings VALUES ('z', '1')")


def test_cancelled_job_leaves_project_untouched(project: Project) -> None:
    token = CancellationToken()
    progress = Progress(token=token)
    before = snapshot(project)

    def job() -> None:
        with project.operation("import", "Import"):
            for i in range(10):
                project.register_object("surface", f"s{i}")
                if i == 4:
                    token.cancel()
                progress.update(i / 10)

    with pytest.raises(CancelledError):
        job()
    assert snapshot(project) == before
    assert project.history() == []


def test_history_survives_reopen_and_read_only_cannot_undo(tmp_path: Path) -> None:
    pkg = tmp_path / "A.mcproj"
    with Project.create(pkg, "A") as p:
        p.set_coordinate_system(ProjectedSystem(CrsDefinition.from_user_input("EPSG:5255")))
    with Project.open(pkg, read_only=True) as p:
        kinds = [o.kind for o in p.history()]
        assert kinds == ["set_coordinate_system"]
        with pytest.raises(ReadOnlyProjectError):
            p.undo()
    with Project.open(pkg) as p:
        p.undo()
        assert p.coordinate_system() is None


def test_invalid_kind_is_rejected(project: Project) -> None:
    with pytest.raises(ProjectError, match="lower_snake_case"), project.operation("Bad Kind", "x"):
        pass


def test_progress_is_throttled_mapped_and_final_always_reported() -> None:
    events: list[ProgressEvent] = []
    root = Progress(events.append, min_interval_s=3600.0)
    root.update(0.0, "start")
    root.update(0.5, "throttled")
    step = root.span(0.5, 1.0)
    step.update(1.0, "done")
    assert [(e.fraction, e.message) for e in events] == [(0.0, "start"), (1.0, "done")]
    half = Progress(events.append, min_interval_s=0.0).span(0.2, 0.6).span(0.5, 1.0)
    half.update(0.5)
    assert events[-1].fraction == pytest.approx(0.5)
    with pytest.raises(ValueError, match="invalid progress span"):
        root.span(0.8, 0.2)


def run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(argv, out), out.getvalue()


def test_cli_history_undo_redo(tmp_path: Path) -> None:
    pkg = str(tmp_path / "C.mcproj")
    run(["project", "create", pkg, "--name", "C"])
    run(["project", "set-crs", pkg, "--crs", "EPSG:5255"])
    code, text = run(["project", "history", pkg, "--json"])
    assert code == 0
    assert json.loads(text)[0]["kind"] == "set_coordinate_system"
    assert run(["project", "undo", pkg]) == (
        0,
        "undo: #1 Set coordinate system: TUREF / TM33 (EPSG:5255)\n",
    )
    assert run(["project", "undo", pkg]) == (0, "nothing to undo\n")
    code, text = run(["project", "history", pkg])
    assert "(undone)" in text
    assert run(["project", "redo", pkg])[0] == 0


def test_tracked_columns_match_schema_and_triggers(project: Project) -> None:
    from minecore.project.history import TRACKED  # noqa: PLC0415

    db = project._db()
    for table, (key, columns) in TRACKED.items():
        assert [r[1] for r in db.execute(f"PRAGMA table_info({table})")] == list(columns)
        assert key in columns
        triggers = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (table,)
        ).fetchall()
        assert len(triggers) == 3
        for (sql,) in triggers:
            for column in columns:
                assert f"'{column}'" in sql, (table, column)
