"""End-to-end resource workflow through the workbench dialogs (offscreen Qt)."""

from __future__ import annotations

import csv
import itertools
import os
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from minecore.gui.pit import PitDialog


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
    vg.second.setChecked(True)
    assert len(vg.model().structures) == 2
    assert vg.model().total_sill == pytest.approx(fitted.total_sill + vg.sill2.value())
    vg.second.setChecked(False)
    all_pairs = int(vg.exp.pairs.sum())
    vg.downhole.setChecked(True)
    vg.compute()
    assert 0 < int(vg.exp.pairs.sum()) < all_pairs
    vg.downhole.setChecked(False)
    stats = resource.StatisticsDialog(db, parent)
    stats.source.setCurrentText(f"komp / {field}")
    stats.cap.setValue(1.0)
    stats.compute()
    assert "Üst kesme" in stats.text.text()
    stats.decluster()
    assert "kümelenmesi giderilmiş" in stats.text.text()
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
    from minecore.gui.pit import PitDialog, parse_sectors  # noqa: PLC0415

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
    assert dialog.route_label.text() == ""
    dialog.routes.setText("liç:60:4")
    dialog.run()
    assert "liç" in dialog.route_label.text()
    assert (dialog.pit.route == 2).any()
    dialog.route_caps.setText("0.01")
    dialog.min_ore.setValue(0.05)
    dialog.min_width.setValue(30.0)
    dialog.royalty.setValue(4.0)
    dialog.capex.setText("0:1; 2:0.5")
    dialog.tax.setValue(20.0)
    dialog.closure.setValue(0.2)
    dialog.make_phases()
    assert dialog.sched is not None
    assert dialog.flow is not None
    assert dialog.cash_table.rowCount() == len(dialog.flow.rows)
    assert dialog.flow.rows[0].capital == 1e6
    assert dialog.flow.rows[-1].closure == 0.2e6
    assert any("Proje NPV" in n for n in notes)
    assert dialog.sched_table.rowCount() == len(dialog.sched.rows) + 1
    assert all(r.fed_by_route[1] <= 0.01e6 * (1 + 1e-9) for r in dialog.sched.rows)
    first_fed = dialog.sched_table.item(0, 3)
    assert first_fed is not None
    assert " + " in first_fed.text()
    assert dialog.inv_table.rowCount() == len(dialog.inventory) > 0
    dialog.save()
    assert len([n for n in notes if not n.startswith("Uyarı")]) == 2
    assert "pit_period" in BlockModel.open(project, "BM").attributes
    _write_pit_reports(dialog, tmp_path)
    project.close()
    _export_pit_from_main_window(tmp_path / "pit.mcproj", tmp_path / "ocak.dxf")


def _gold_silver_dialog(tmp_path: Path, notes: list[str]) -> tuple[Project, PitDialog, QWidget]:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.pit import PitDialog  # noqa: PLC0415

    project = Project.create(tmp_path / "co.mcproj", "co")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (12, 10, 6))
    c = grid.centroids()
    near = np.clip(2.0 - np.hypot(c[:, 0] - 60, c[:, 1] - 50) / 20, 0, None)
    BlockModel.create(project, "BM", grid).set_attributes(
        {"au": near * 0.3, "ag": near * 60}, {"au": {"unit": "g/t"}, "ag": {"unit": "g/t"}}, "g"
    )
    parent = QWidget()
    dialog = PitDialog(project, parent)
    dialog.inform = lambda title, text: notes.append(text)  # type: ignore[method-assign]
    dialog.grade.setCurrentText("au")
    return project, dialog, parent


def test_pit_dialog_stockpile_bins_and_capacity(qapp: QApplication, tmp_path: Path) -> None:
    notes: list[str] = []
    project, dialog, _parent = _gold_silver_dialog(tmp_path, notes)
    dialog.price.setValue(1800.0)
    dialog.coproducts.setText("ag:25:ozt:70:95")
    dialog.run()
    assert dialog.pit is not None
    dialog.plant.setValue(0.02)
    dialog.min_ore.setValue(0.02)
    dialog.bins.setText("0.3; 0.5")
    dialog.stock_caps.setText("0.01")
    dialog.rehandle.setValue(1.0)
    dialog.make_phases()
    assert dialog.sched is not None
    rows = dialog.sched.rows
    assert sum(r.reclaimed_t for r in rows) > 0
    assert sum(r.discarded_t for r in rows) > 0
    assert max(r.stockpile_t for r in rows) <= 0.01e6 * (1 + 1e-9)
    assert len(rows[0].stock_by_bin) == 3
    assert any("döküme" in n for n in notes)
    project.close()


def test_pit_dialog_sensitivity(qapp: QApplication, tmp_path: Path) -> None:
    project, dialog, _parent = _gold_silver_dialog(tmp_path, [])
    dialog.price.setValue(1800.0)
    dialog.coproducts.setText("ag:25:ozt:70:95")
    dialog.run()
    with pytest.raises(RuntimeError):
        dialog.run_sensitivity()  # needs the pushbacks and the schedule first
    dialog.plant.setValue(0.02)
    dialog.min_ore.setValue(0.02)
    dialog.capex.setText("0:0.5")
    dialog.make_phases()
    assert dialog.flow is not None
    dialog.sens_changes.setText("-10; 10")
    dialog.run_sensitivity()
    s = dialog.sens
    assert s is not None
    assert s.base.npv == pytest.approx(dialog.flow.npv)
    assert dialog.sens_table.rowCount() == 6  # price, costs, recovery, discount, capital
    assert dialog.sens_table.columnCount() == 4  # label, -10 %, base, +10 %
    assert len(dialog.sens_figure.axes[0].get_legend_handles_labels()[0]) == 6
    project.close()


def test_pit_dialog_milp_schedule(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.pitopt.milp import OptimisedSchedule  # noqa: PLC0415

    notes: list[str] = []
    project, dialog, _parent = _gold_silver_dialog(tmp_path, notes)
    dialog.price.setValue(1800.0)
    dialog.coproducts.setText("ag:25:ozt:70:95")
    dialog.run()
    dialog.plant.setValue(0.02)
    dialog.min_ore.setValue(0.02)
    dialog.make_phases()
    assert dialog.sched is not None
    greedy = dialog.sched.npv
    dialog.method.setCurrentIndex(1)
    dialog.time_limit.setValue(30)
    dialog.make_phases()
    assert isinstance(dialog.sched, OptimisedSchedule)
    assert dialog.sched.npv >= greedy - 1e-6 * abs(greedy)
    assert dialog.sched.greedy_npv == pytest.approx(greedy)
    assert any("MILP" in n for n in notes)
    project.close()


def test_pit_dialog_pushbacks_by_npv(qapp: QApplication, tmp_path: Path) -> None:
    notes: list[str] = []
    project, dialog, _parent = _gold_silver_dialog(tmp_path, notes)
    dialog.price.setValue(1800.0)
    dialog.coproducts.setText("ag:25:ozt:70:95")
    dialog.run()
    dialog.plant.setValue(0.02)
    dialog.min_ore.setValue(0.02)
    dialog.make_phases()
    assert dialog.sched is not None
    plain = dialog.sched.npv
    dialog.by_npv.setChecked(True)
    dialog.max_phases.setValue(4)
    dialog.make_phases()
    assert dialog.sched.npv >= plain - 1e-6 * abs(plain)
    assert 1 <= int(dialog.phase.max()) <= 4
    assert any("NPV'ye göre seçildi" in n for n in notes)
    project.close()


def test_pit_dialog_values_co_products(qapp: QApplication, tmp_path: Path) -> None:
    project, dialog, _parent = _gold_silver_dialog(tmp_path, [])
    dialog.price.setValue(1800.0)
    dialog.run()
    assert dialog.pit is not None
    gold_only = dialog.pit.rows[-1].ore_t
    dialog.coproducts.setText("ag:25:ozt:70:95")
    dialog.run()
    assert dialog.pit.rows[-1].ore_t > gold_only
    assert "ag" in dialog.route_label.text()
    dialog.nsr.setChecked(True)
    assert not dialog.coproducts.isEnabled()
    project.close()


def _write_pit_reports(dialog: PitDialog, folder: Path) -> None:
    from minecore.gui.pit import write_pit_reports, write_pit_reports_xlsx  # noqa: PLC0415

    assert dialog.pit is not None
    out = folder / "pit.csv"
    write_pit_reports(out, dialog.pit, dialog.sched)
    assert out.read_text(encoding="utf-8-sig").startswith("shell;")
    write_pit_reports_xlsx(
        folder / "pit.xlsx", dialog.pit, dialog.sched, dialog.inventory, dialog.flow
    )
    assert (folder / "pit.xlsx").stat().st_size > 0


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


def test_section_slab_and_controls(qapp: QApplication) -> None:
    from minecore.gui.app import MainWindow, in_slab  # noqa: PLC0415

    pts = np.array([[0.0, 10.0, 0.0], [0.0, 14.9, 0.0], [0.0, 15.1, 0.0]])
    assert in_slab(pts, 1, 10.0, 10.0).tolist() == [True, True, False]
    window = MainWindow()
    assert window.section() is None
    window.section_axis.setCurrentIndex(3)  # plan
    window.section_at.setValue(100.0)
    assert window.section() == (2, 100.0, window.section_width.value())
    window._step_section(1)
    assert window.section_at.value() == 100.0 + window.section_width.value()
    window.close()


def test_print_section_from_the_window(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui import app as app_module  # noqa: PLC0415

    project = Project.create(tmp_path / "w.mcproj", "w")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (6, 6, 4))
    BlockModel.create(project, "BM", grid).set_attributes(
        {"au": np.ones(grid.blocks)}, {"au": {"unit": "g/t"}}, "au"
    )
    window = app_module.MainWindow()
    window.project = project
    window.refresh()
    window.block_by.setCurrentText("BM / au")
    window.section_axis.setCurrentIndex(1)  # north-south
    window.section_at.setValue(25.0)
    out = tmp_path / "k.png"
    from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: PLC0415

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "PNG (*.png)"))
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    window.print_section()
    assert out.stat().st_size > 1000
    assert "24 blok" in shown[0]  # one north-south slice: 6 (y) x 4 (z) blocks
    window.close()
    project.close()


def test_period_surfaces_from_the_menu(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox  # noqa: PLC0415

    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.app import MainWindow  # noqa: PLC0415
    from minecore.surfaces import surface_names  # noqa: PLC0415

    project = Project.create(tmp_path / "d.mcproj", "d")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (4, 4, 3))
    period = np.zeros((3, 4, 4))
    period[2] = 1.0
    period[1, 1:3, 1:3] = 2.0
    BlockModel.create(project, "BM", grid).set_attributes(
        {"pit_period": period.ravel()}, {}, "schedule"
    )
    window = MainWindow()
    window.project = project
    out = tmp_path / "d.dxf"
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("BM", True))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "DXF"))
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    window.export_periods()
    assert {"donem_1", "donem_2"} <= set(surface_names(project))
    assert "2 dönem" in shown[0]
    assert "Y2_CREST" in out.read_text()
    window.close()
    project.close()


def test_classification_dialog_and_filtered_report(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415

    project = Project.create(tmp_path / "k.mcproj", "k")
    model = BlockModel.create(project, "BM", Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (2, 2, 1)))
    model.set_attributes(
        {
            "au": np.array([1.0, 2.0, 3.0, 4.0]),
            "au_n": np.array([10.0, 5.0, 2.0, 1.0]),
            "au_dist": np.array([10.0, 40.0, 80.0, 500.0]),
        },
        {"au": {"unit": "g/t"}},
        "estimate",
    )
    parent = QWidget()
    dialog = resource.ClassificationDialog(project, parent)
    assert dialog.attribute.currentText() == "au"
    dialog.run()
    assert dialog.counts == {0: 1, 1: 1, 2: 1, 3: 1}
    gt = resource.GradeTonnageDialog(project, parent)
    gt.filter_attr.setCurrentText("kat_au")
    gt.f0.setValue(1.0)
    gt.f1.setValue(2.0)
    gt.c0.setValue(0.0)
    gt.compute()
    assert gt.report is not None
    assert gt.report.rows[0].blocks == 2
    model.set_attributes({"dom_z_pct": np.array([0.5, 0.0, 1.0, 0.25])}, {}, "pct")
    gt = resource.GradeTonnageDialog(project, parent)
    gt.weight_attr.setCurrentText("dom_z_pct")
    gt.c0.setValue(0.0)
    gt.compute()
    assert gt.report is not None
    assert gt.report.rows[0].volume == pytest.approx(1.75 * 1000.0)
    project.close()


def test_calculator_dialog(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415

    project = Project.create(tmp_path / "h.mcproj", "h")
    model = BlockModel.create(project, "BM", Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (2, 1, 1)))
    model.set_attributes({"cu": np.array([1.0, 2.0])}, {"cu": {"unit": "%"}}, "cu")
    parent = QWidget()
    dialog = resource.CalculatorDialog(project, parent)
    dialog.target.setText("cu2")
    dialog.expression.setText("cu * 2 + x")
    dialog.unit.setText("%")
    dialog.run()
    assert BlockModel.open(project, "BM").values("cu2").tolist() == [7.0, 19.0]
    assert "cu2" in [dialog.names.item(i).text() for i in range(dialog.names.count())]
    project.close()


def test_bench_design_dialog(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.pit import BenchDesignDialog  # noqa: PLC0415
    from minecore.pitopt.economics import PitOptError  # noqa: PLC0415
    from minecore.surfaces import surface_names  # noqa: PLC0415

    project = Project.create(tmp_path / "d.mcproj", "d")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (12, 12, 5))
    model = BlockModel.create(project, "BM", grid)
    shell = np.zeros((5, 12, 12))
    shell[3:, 4:8, 4:8] = 1.0
    model.set_attributes({"pit_shell": shell.ravel(), "pit_value": np.ones(grid.blocks)}, {}, "pit")
    parent = QWidget()
    dialog = BenchDesignDialog(project, parent)
    assert dialog.model.currentText() == "BM"
    dialog.run()
    assert dialog.report is not None
    assert dialog.report.design_blocks >= 32
    assert "tasarim_1" in surface_names(project)
    assert "pit_tasarim" in BlockModel.open(project, "BM").attributes
    dialog.ramp_on.setChecked(True)
    dialog.ramp_w.setValue(8.0)
    dialog.run()
    assert dialog.report.pit.ramp is not None
    assert "Rampa" in dialog.result_label.text()
    dialog.ramp_on.setChecked(False)
    dialog.sectors.setText("0:75:4; 180:50:8")
    dialog.run()
    assert len(dialog.report.pit.sectors) == 2
    dialog.sectors.setText("0:75")
    with pytest.raises(PitOptError, match="azimuth:face:berm"):
        dialog._design(None)
    project.close()


def test_bench_design_dialog_two_pits(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.pit import BenchDesignDialog  # noqa: PLC0415

    project = Project.create(tmp_path / "d2.mcproj", "d2")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (40, 12, 5))
    model = BlockModel.create(project, "BM", grid)
    shell = np.zeros((5, 12, 40))
    shell[2:, 4:8, 4:8] = 1.0
    shell[3:, 4:8, 30:34] = 1.0
    model.set_attributes({"pit_shell": shell.ravel(), "pit_value": np.ones(grid.blocks)}, {}, "pit")
    parent = QWidget()
    dialog = BenchDesignDialog(project, parent)
    dialog.ramp_on.setChecked(True)
    dialog.ramp_w.setValue(8.0)
    dialog.ramp_exits.setText("180")
    dialog.run()
    assert dialog.report is not None
    pit = dialog.report.pit
    assert pit.pits == 2
    assert len(pit.ramps) + pit.pits_without_ramp == 2
    text = dialog.result_label.text()
    assert "Ayrı ocak sayısı 2" in text
    assert "Rampa 1:" in text
    project.close()


def test_cutoff_dialog(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.pit import CutoffDialog  # noqa: PLC0415
    from minecore.pitopt import Economics, PriceUnit  # noqa: PLC0415

    project = Project.create(tmp_path / "c.mcproj", "c")
    grid = Grid((0.0, 0.0, 0.0), (10.0, 10.0, 10.0), (10, 10, 4))
    model = BlockModel.create(project, "BM", grid)
    au = np.linspace(0.0, 4.0, grid.blocks)
    econ = Economics(1500.0, PriceUnit.TROY_OUNCE, 0.9, 2.0, 15.0)
    model.set_attributes(
        {"au": au, "pit_shell": np.ones(grid.blocks), "pit_value": np.zeros(grid.blocks)},
        {"au": {"unit": "g/t"}, "pit_value": {"economics": econ.to_json()}},
        "pit",
    )
    parent = QWidget()
    dialog = CutoffDialog(project, parent)
    dialog.mine.setValue(0.03)
    dialog.mill.setValue(0.01)
    dialog.fixed.setValue(0.1)
    dialog.run()
    assert dialog.lane is not None
    assert dialog.lane.npv > 0
    assert dialog.table.rowCount() == len(dialog.lane.years) > 1
    cut = [y.cutoff for y in dialog.lane.years]
    assert cut[0] >= cut[-1]
    project.close()


def test_stope_dialog(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.underground import StopeDialog  # noqa: PLC0415

    project = Project.create(tmp_path / "u.mcproj", "u")
    grid = Grid((0.0, 0.0, 0.0), (5.0, 5.0, 5.0), (8, 4, 4))
    model = BlockModel.create(project, "BM", grid)
    au = np.zeros((4, 4, 8))
    au[:, 1:3, 2:6] = 5.0
    model.set_attributes({"au": au.ravel()}, {"au": {"unit": "g/t"}}, "au")
    parent = QWidget()
    dialog = StopeDialog(project, parent)
    dialog.sx.setValue(10.0)
    dialog.sy.setValue(10.0)
    dialog.sz.setValue(10.0)
    dialog.cutoff.setValue(4.0)
    dialog.run()
    assert dialog.stopes is not None
    assert len(dialog.stopes.stopes) == 4
    assert dialog.table.rowCount() == 4
    assert "ug_pano" in BlockModel.open(project, "BM").attributes
    dialog.capacity.setValue(0.005)  # 5 000 t/y: the four 2 970 t stopes take three years
    dialog.capex.setText("0:0.5")
    dialog.plan()
    assert dialog.sched is not None
    assert len(dialog.sched.rows) == 3
    assert sum(r.mined_t for r in dialog.sched.rows) == pytest.approx(4 * 2700 * 0.9 * 1.1)
    assert dialog.flow is not None
    assert "proje NPV" in dialog.plan_summary.text()
    from minecore.gui.underground import DeclineDialog  # noqa: PLC0415
    from minecore.strings import string_names  # noqa: PLC0415

    dec = DeclineDialog(project, parent)
    dec.ore.setCurrentText("ug_pano")
    dec.pz.setValue(100.0)
    dec.interval.setValue(10.0)
    dec.run(None)
    assert dec.dev is not None
    assert dec.dev.accesses
    assert "cevher galerileri" in dec.text.text()
    dec.drives.setChecked(False)
    dec.run(None)
    assert dec.dev.drive_length == 0.0
    assert "dekline" in string_names(project)
    project.close()


def test_implicit_dialog(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.blockmodel import Grid  # noqa: PLC0415
    from minecore.gui.geology import ImplicitDialog  # noqa: PLC0415
    from minecore.solids import solid_names  # noqa: PLC0415

    project, db, field = _project(tmp_path)
    samples = resource._samples(db, f"komp / {field}")
    BlockModel.create(project, "BM", Grid.covering(samples.xyz, (10.0, 10.0, 5.0)))
    parent = QWidget()
    dialog = ImplicitDialog(project, db, parent)
    dialog.source.setCurrentText(f"komp / {field}")
    dialog.threshold.setValue(0.1)
    dialog.use_aniso.setChecked(True)
    dialog.aniso.major.setValue(3.0)
    dialog.aniso.semi.setValue(3.0)
    dialog.aniso.minor.setValue(1.0)
    dialog.run()
    assert dialog.result_ is not None
    assert dialog.result_.solid.open_edges() == 0
    assert "cevher" in solid_names(project)
    assert "dom_cevher" in BlockModel.open(project, "BM").attributes
    project.close()


def test_guide_dialog(qapp: QApplication) -> None:
    from minecore.gui.guide import GuideDialog  # noqa: PLC0415

    parent = QWidget()
    dialog = GuideDialog(parent)
    assert "Açık ocak" in dialog.text.toPlainText()


def test_self_test_from_the_help_menu(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    from minecore.gui.app import MainWindow  # noqa: PLC0415

    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append("FAILED " + a[2]))
    window = MainWindow()
    window.self_test()
    assert shown[0].startswith("Tüm adımlar başarılı"), shown[0]
    window.close()


def test_rotated_block_model_dialog_and_3d_cells(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.gui.app import block_mesh  # noqa: PLC0415

    project, db, field = _project(tmp_path)
    parent = QWidget()
    bm = resource.BlockModelDialog(db, parent)
    bm.source.setCurrentText(f"komp / {field}")
    bm.azimuth.setValue(30.0)
    grid = bm.grid()
    assert grid.azimuth == 30.0
    assert "Döndürme 30" in bm.info.text()
    samples = resource._samples(db, f"komp / {field}")
    idx = grid.cell_index(samples.xyz)
    assert (idx >= 0).all()
    assert (idx < np.asarray(grid.count)).all()
    origin = np.asarray(grid.origin)
    mesh = block_mesh(grid, origin)
    assert mesh.n_cells == grid.blocks
    # Cell k of the mesh is block k of the model, rotated with it.
    centres = np.asarray(mesh.cell_centers().points) + origin
    np.testing.assert_allclose(centres, grid.centroids(), atol=1e-6)
    project.close()


def test_pit_dialog_scenarios(qapp: QApplication, tmp_path: Path) -> None:
    from minecore.pitopt.scenarios import list_scenarios  # noqa: PLC0415

    project, dialog, _parent = _gold_silver_dialog(tmp_path, [])
    dialog.price.setValue(1800.0)
    dialog.run()
    dialog.plant.setValue(0.02)
    dialog.min_ore.setValue(0.02)
    dialog.make_phases()
    dialog.run_fleet()
    assert dialog.fleet
    assert dialog.fleet_table.rowCount() == len(dialog.sched.rows)  # type: ignore[union-attr]
    assert all(r.trucks >= 1 for r in dialog.fleet)
    dialog.ask_name = lambda default: "baz"  # type: ignore[method-assign]
    dialog.save_scenario()
    dialog.price.setValue(2200.0)
    dialog.run()
    dialog.make_phases()
    dialog.ask_name = lambda default: "yüksek fiyat"  # type: ignore[method-assign]
    dialog.save_scenario()
    assert [s.name for s in list_scenarios(project)] == ["baz", "yüksek fiyat"]
    assert dialog.scenario_table.columnCount() == 3
    dialog.scenario_pick.setCurrentText("baz")
    dialog.load_scenario()
    assert dialog.price.value() == 1800.0
    dialog.delete_scenario()
    assert [s.name for s in list_scenarios(project)] == ["yüksek fiyat"]
    project.close()
