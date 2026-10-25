"""End-to-end resource workflow through the workbench dialogs (offscreen Qt)."""

from __future__ import annotations

import csv
import itertools
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from minecore.blockmodel import BlockModel
from minecore.blockmodel.workflow import load_variograms, save_variogram
from minecore.drillholes import DrillholeDatabase, validate
from minecore.drillholes.importer import import_folder
from minecore.drillholes.workflow import composite_table, save_composites
from minecore.estimation import Method
from minecore.gui import resource
from minecore.gui.app import SAMPLE_DIR
from minecore.project import Project


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


def _project(tmp_path: Path) -> tuple[Project, DrillholeDatabase, str]:
    project = Project.create(tmp_path / "t.mcproj", "t")
    db = DrillholeDatabase.create(project, "Sondajlar")
    import_folder(db, SAMPLE_DIR)
    assay = next(n for n in db.tables() if "assay" in n.lower())
    field = next(f.name for f in db.table_info(assay).fields if f.type.name == "NUMERIC")
    result = composite_table(db, assay, [field], 2.0, report=validate(db))
    save_composites(db, "komp", result, assay, [field])
    return project, db, field


def test_block_model_variogram_estimate_report(  # noqa: PLR0915 - one end-to-end flow
    qapp: QApplication, tmp_path: Path
) -> None:
    project, db, field = _project(tmp_path)
    parent = QWidget()
    choices = resource.numeric_interval_fields(db)
    assert choices[0] == f"komp / {field}"

    bm = resource.BlockModelDialog(db, parent)
    bm.source.setCurrentText(f"komp / {field}")
    bm.sx.setValue(20.0)
    bm.sy.setValue(20.0)
    bm.sz.setValue(10.0)
    grid = bm.grid()
    samples = resource._samples(db, f"komp / {field}")
    lo = np.asarray(grid.origin)
    hi = lo + np.asarray(grid.size) * np.asarray(grid.count)
    assert (samples.xyz >= lo).all()
    assert (samples.xyz <= hi).all()
    model = BlockModel.create(project, "BM", grid)

    vg = resource.VariogramDialog(db, project, parent)
    vg.source.setCurrentText(f"komp / {field}")
    vg.compute()
    assert vg.exp is not None
    assert int(vg.exp.pairs.sum()) > 0
    vg.fit()
    fitted = vg.model()
    assert fitted.total_sill > 0
    save_variogram(project, "vg", fitted, {"data": vg.source.currentText()})
    assert "vg" in load_variograms(project)

    est = resource.EstimationDialog(db, project, parent)
    est.source.setCurrentText(f"komp / {field}")
    est.variogram.setCurrentText("vg")
    est.method.setCurrentIndex(list(Method).index(Method.ORDINARY_KRIGING))
    est.search.set_value(est.search.value().__class__(500.0, 500.0, 200.0))
    message = est.run()
    attr = est.output.text()
    assert attr == f"{field}_ok"
    assert "blok" in message
    values = model.values(attr)
    finite = values[np.isfinite(values)]
    assert finite.size > 0
    # Kriging is a weighted average with weights summing to one: estimates stay
    # close to the sample range (small negative weights allow slight overshoot).
    span = samples.values.max() - samples.values.min()
    assert finite.min() >= samples.values.min() - 0.5 * span
    assert finite.max() <= samples.values.max() + 0.5 * span

    gt = resource.GradeTonnageDialog(project, parent)
    gt.model.setCurrentText("BM")
    gt.attribute.setCurrentText(attr)
    gt.c0.setValue(0.0)
    gt.step.setValue(0.5)
    gt.compute()
    report = gt.report
    assert report is not None
    first = report.rows[0]
    known = int(np.isfinite(values).sum()) if first.cutoff <= finite.min() else None
    if known is not None:
        assert first.blocks == known
    tonnes = [r.tonnes for r in report.rows]
    assert all(a >= b for a, b in itertools.pairwise(tonnes))
    assert first.tonnes == pytest.approx(first.blocks * grid.block_volume * gt.density.value())

    out = tmp_path / "gt.csv"
    resource.write_grade_tonnage_csv(out, report)
    rows = list(csv.reader(out.open(encoding="utf-8-sig"), delimiter=";"))
    assert len(rows) == 2 + len(report.rows)

    bm_csv = tmp_path / "bm.csv"
    assert resource.export_block_model_csv(bm_csv, model) == grid.blocks
    with bm_csv.open(encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh, delimiter=";"))
    assert header[:6] == ["x", "y", "z", "i", "j", "k"]
    assert attr in header
    project.close()


def test_pit_dialog_shells_pushbacks_and_save(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.pit import PitDialog, parse_sectors, write_pit_reports  # noqa: PLC0415

    assert parse_sectors("0:50; 90:38,180:50") == ((0.0, 50.0), (90.0, 38.0), (180.0, 50.0))
    project = Project.create(tmp_path / "pit.mcproj", "pit")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (20, 12, 8))
    model = BlockModel.create(project, "BM", grid)
    c = grid.centroids()
    grade = np.clip(3.0 - np.hypot(c[:, 0] - 100, (c[:, 2] - 40) * 2) / 20, 0, None)
    model.set_attributes({"au": grade}, {"au": {"unit": "g/t"}}, "grades")
    parent = QWidget()
    dialog = PitDialog(project, parent)
    notes: list[str] = []
    dialog.inform = lambda title, text: notes.append(text)  # type: ignore[method-assign]
    assert dialog.grade.currentText() == "au"
    dialog.price.setValue(1800.0)
    dialog.rate.setValue(1.0)
    dialog.run()
    assert dialog.pit is not None
    assert dialog.shells.rowCount() == len(dialog.pit.rows)
    dialog.min_ore.setValue(0.05)
    dialog.min_width.setValue(30.0)
    dialog.make_phases()
    assert dialog.sched is not None
    assert dialog.sched_table.rowCount() == len(dialog.sched.rows) + 1
    dialog.save()
    assert len(notes) == 2
    assert "pit_period" in BlockModel.open(project, "BM").attributes
    out = tmp_path / "pit.csv"
    write_pit_reports(out, dialog.pit, dialog.sched)
    assert out.read_text(encoding="utf-8-sig").startswith("shell;")
    from minecore.gui.pit import write_pit_reports_xlsx  # noqa: PLC0415

    write_pit_reports_xlsx(tmp_path / "pit.xlsx", dialog.pit, dialog.sched)
    assert (tmp_path / "pit.xlsx").stat().st_size > 0
    project.close()
    _export_pit_from_main_window(tmp_path / "pit.mcproj", tmp_path / "ocak.dxf")


def _export_pit_from_main_window(path: Path, dxf: Path) -> None:
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox  # noqa: PLC0415

    from minecore.gui.app import MainWindow  # noqa: PLC0415
    from minecore.surfaces import surface_names  # noqa: PLC0415

    patch = pytest.MonkeyPatch()
    notes: list[str] = []
    patch.setattr(QInputDialog, "getItem", lambda *a, **k: (a[3][0], True))
    patch.setattr(QInputDialog, "getInt", lambda *a, **k: (a[3], True))
    patch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dxf), ""))
    patch.setattr(QMessageBox, "information", lambda *a, **k: notes.append(a[2]))
    try:
        window = MainWindow()
        window._set_project(Project.open(path))
        window.export_pit()
        assert notes, "the export reported nothing"
        assert "PIT_SOLID" in dxf.read_text()
        assert window.project is not None
        assert any(n.startswith("ocak_") for n in surface_names(window.project))
        window.project.close()
    finally:
        patch.undo()


def test_validation_dialog(qapp: QApplication, tmp_path: Path) -> None:
    project, db, field = _project(tmp_path)
    samples = resource._samples(db, f"komp / {field}")
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.blockmodel.workflow import estimate_blocks  # noqa: PLC0415
    from minecore.estimation import Ellipsoid, Search  # noqa: PLC0415

    model = BlockModel.create(project, "BM", Grid.covering(samples.xyz, (25.0, 25.0, 10.0)))
    estimate_blocks(
        model,
        samples,
        "au",
        method=Method.INVERSE_DISTANCE,
        search=Search(Ellipsoid.isotropic(150)),
    )
    parent = QWidget()
    dialog = resource.ValidationDialog(db, project, parent)
    dialog.source.setCurrentText(f"komp / {field}")
    dialog.attribute.setCurrentText("au")
    dialog.run_swath()
    assert "dilim" in dialog.summary.text()
    dialog.method.setCurrentIndex(list(Method).index(Method.INVERSE_DISTANCE))
    dialog.run_cv()
    assert "Korelasyon" in dialog.summary.text()
    project.close()


def test_estimation_restricted_to_a_solid(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.solids import Solid, save_solid  # noqa: PLC0415

    project, db, field = _project(tmp_path)
    samples = resource._samples(db, f"komp / {field}")
    grid = Grid.covering(samples.xyz, (25.0, 25.0, 10.0))
    BlockModel.create(project, "BM", grid)
    lo = samples.xyz.min(axis=0)
    hi = (samples.xyz.min(axis=0) + samples.xyz.max(axis=0)) / 2
    corners = np.array(
        [[x, y, z] for z in (lo[2], hi[2]) for y in (lo[1], hi[1]) for x in (lo[0], hi[0])]
    )
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (1, 3, 7, 5), (3, 2, 6, 7), (2, 0, 4, 6)]
    tris = [t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))]
    box = Solid(corners, np.array(tris, np.int64))
    assert box.open_edges() == 0
    save_solid(project, "kutu", box)
    parent = QWidget()
    est = resource.EstimationDialog(db, project, parent)
    est.source.setCurrentText(f"komp / {field}")
    est.method.setCurrentIndex(list(Method).index(Method.INVERSE_DISTANCE))
    est.domain.setCurrentText("kutu")
    est.run()
    values = BlockModel.open(project, "BM").values(est.output.text())
    inside = box.inside(grid.centroids())
    assert np.isnan(values[~inside]).all()
    assert np.isfinite(values[inside]).any()
    project.close()


def test_excel_reports(tmp_path: Path) -> None:
    from openpyxl import load_workbook  # noqa: PLC0415

    from minecore.blockmodel import GradeTonnage, GradeTonnageRow  # noqa: PLC0415

    report = GradeTonnage(
        "au",
        "g/t",
        2.7,
        "g",
        (
            GradeTonnageRow(0.0, 10, 1000.0, 2700.0, 1.5, 4050.0),
            GradeTonnageRow(5.0, 0, 0.0, 0.0, float("nan"), 0.0),
        ),
        3,
    )
    path = tmp_path / "gt.xlsx"
    resource.write_grade_tonnage_xlsx(path, report)
    sheet = load_workbook(path)["Tenor-tonaj"]
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[1] == (0.0, 10, 1000.0, 2700.0, 1.5, 4050.0)
    assert rows[2][4] is None
