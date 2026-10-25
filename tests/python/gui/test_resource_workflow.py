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
    dialog.make_phases()
    assert dialog.sched is not None
    assert dialog.sched_table.rowCount() == len(dialog.sched.rows) + 1
    dialog.save()
    assert len(notes) == 2
    assert "pit_period" in BlockModel.open(project, "BM").attributes
    out = tmp_path / "pit.csv"
    write_pit_reports(out, dialog.pit, dialog.sched)
    assert out.read_text(encoding="utf-8-sig").startswith("shell;")
    project.close()


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
