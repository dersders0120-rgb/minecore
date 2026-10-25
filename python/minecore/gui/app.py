"""MineCore workbench: the first desktop window (Turkish UI).

Scope: create/open a project, import drillhole files, review the validation
report, view holes in 3D coloured by a field, composite and export; define a
block model, model variograms, estimate grades (NN, IDW, OK), view blocks in
3D above a cut-off and report grade-tonnage. Every action calls the same public API as scripts do.
"""

from __future__ import annotations

import csv
import math
import sys
import traceback
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from minecore import __version__
from minecore.blockmodel import BlockModel
from minecore.blockmodel.workflow import load_variograms
from minecore.drillholes import (
    CATALOGUE,
    DrillholeDatabase,
    FieldType,
    Role,
    Severity,
    ValidationReport,
    validate,
)
from minecore.drillholes.importer import ImportOptions, import_folder, import_table
from minecore.drillholes.workflow import composite_table, hole_geometry, save_composites
from minecore.gui.resource import (
    BlockModelDialog,
    EstimationDialog,
    GradeTonnageDialog,
    VariogramDialog,
    block_models,
    export_block_model_csv,
)
from minecore.project import Project

SAMPLE_DIR = Path(__file__).resolve().parent / "sample"
ROLE_TR = {
    Role.COLLAR: "Kuyu ağzı",
    Role.SURVEY: "Sapma",
    Role.INTERVAL: "Aralık",
    Role.POINT: "Nokta",
}
SEVERITY_TR = {Severity.ERROR: "Hata", Severity.WARNING: "Uyarı", Severity.INFO: "Bilgi"}
SEVERITY_COLOR = {Severity.ERROR: "#c62828", Severity.WARNING: "#ef6c00", Severity.INFO: "#1565c0"}


def _viewer(parent: QWidget) -> Any:
    """The 3D view, or a placeholder when no OpenGL is available."""
    try:
        from pyvistaqt import QtInteractor  # noqa: PLC0415

        view: Any = QtInteractor(parent)
        view.set_background("#1e1e24", top="#3a3f4b")
        view.add_axes()
        return view
    except Exception:  # pragma: no cover - depends on the graphics driver
        label = QLabel("3B görünüm başlatılamadı (OpenGL yok).")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label


class CompositeDialog(QDialog):
    def __init__(self, db: DrillholeDatabase, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Kompozit")
        self.db = db
        form = QFormLayout(self)
        self.table = QComboBox()
        for info in db.tables_with_role(Role.INTERVAL):
            if any(f.type is FieldType.NUMERIC for f in info.fields):
                self.table.addItem(info.name)
        self.fields = QListWidget()
        self.fields.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.domain = QComboBox()
        self.length = QDoubleSpinBox(minimum=0.1, maximum=1000.0, value=2.0, suffix=" m")
        self.coverage = QDoubleSpinBox(minimum=0.0, maximum=1.0, value=0.5, singleStep=0.05)
        self.bdl = QDoubleSpinBox(minimum=0.0, maximum=1.0, value=0.5, singleStep=0.05)
        form.addRow("Tablo", self.table)
        form.addRow("Alanlar", self.fields)
        form.addRow("Domen alanı", self.domain)
        form.addRow("Kompozit boyu", self.length)
        form.addRow("En az kapsama", self.coverage)
        form.addRow("Tespit sınırı altı çarpanı", self.bdl)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.table.currentTextChanged.connect(self._fill)
        self._fill(self.table.currentText())

    def _fill(self, name: str) -> None:
        self.fields.clear()
        self.domain.clear()
        self.domain.addItem("(yok)")
        if not name:
            return
        for spec in self.db.table_info(name).fields:
            if spec.type is FieldType.NUMERIC:
                item = QListWidgetItem(spec.name)
                self.fields.addItem(item)
                item.setSelected(True)
            else:
                self.domain.addItem(spec.name)

    def values(self) -> dict[str, Any]:
        domain = self.domain.currentText()
        return {
            "table": self.table.currentText(),
            "fields": [i.text() for i in self.fields.selectedItems()],
            "length": self.length.value(),
            "domain_field": None if domain == "(yok)" else domain,
            "min_coverage": self.coverage.value(),
            "below_detection_factor": self.bdl.value(),
        }


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.project: Project | None = None
        self.db: DrillholeDatabase | None = None
        self.report: ValidationReport | None = None
        self.setWindowTitle(f"MineCore {__version__}")
        self.resize(1400, 900)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Proje", "Satır"])
        self.tree.setColumnWidth(0, 210)
        self.tree.itemDoubleClicked.connect(self._show_table_item)
        dock = QDockWidget("Gezgin", self)
        dock.setWidget(self.tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.tabs = QTabWidget()
        view_page = QWidget()
        layout = QVBoxLayout(view_page)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Renk:"))
        self.color_by = QComboBox()
        self.color_by.setMinimumWidth(260)
        self.color_by.currentIndexChanged.connect(self.redraw)
        bar.addWidget(self.color_by)
        bar.addSpacing(20)
        bar.addWidget(QLabel("Blok:"))
        self.block_by = QComboBox()
        self.block_by.setMinimumWidth(260)
        self.block_by.currentIndexChanged.connect(self.redraw)
        bar.addWidget(self.block_by)
        bar.addWidget(QLabel("Sınır tenör ≥"))
        self.block_cutoff = QDoubleSpinBox(minimum=-1e9, maximum=1e9, value=0.0, decimals=3)
        self.block_cutoff.setKeyboardTracking(False)
        self.block_cutoff.valueChanged.connect(self.redraw)
        bar.addWidget(self.block_cutoff)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.view = _viewer(view_page)
        layout.addWidget(self.view, 1)
        self.tabs.addTab(view_page, "3B Görünüm")
        self.issues = QTableWidget(0, 6)
        self.issues.setHorizontalHeaderLabels(["Önem", "Kod", "Tablo", "Kuyu", "Satır", "Açıklama"])
        self.issues.horizontalHeader().setStretchLastSection(True)
        self.issues.setSortingEnabled(True)
        self.tabs.addTab(self.issues, "Doğrulama Raporu")
        self.data = QTableWidget()
        self.tabs.addTab(self.data, "Tablo")
        self.setCentralWidget(self.tabs)
        self._menus()
        self.statusBar().showMessage("Bir proje oluşturun veya açın.")

    # -- menus ---------------------------------------------------------------

    def _menus(self) -> None:
        def action(menu: Any, text: str, slot: Any, shortcut: str | None = None) -> QAction:
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.triggered.connect(lambda: self._guard(slot))
            menu.addAction(act)
            return act

        project = self.menuBar().addMenu("&Proje")
        action(project, "Yeni proje...", self.new_project, "Ctrl+N")
        action(project, "Proje aç...", self.open_project, "Ctrl+O")
        project.addSeparator()
        action(project, "Geri al", self.undo, "Ctrl+Z")
        action(project, "Yinele", self.redo, "Ctrl+Y")
        project.addSeparator()
        action(project, "Çıkış", self.close)
        drill = self.menuBar().addMenu("&Sondaj")
        action(drill, "Klasörden içe aktar (kuyu ağzı, sapma, analizler)...", self.import_folder)
        action(drill, "Dosya içe aktar...", self.import_file)
        drill.addSeparator()
        action(drill, "Doğrula", self.revalidate, "F5")
        action(drill, "Kompozit oluştur...", self.composite)
        action(drill, "Tabloyu CSV olarak dışa aktar...", self.export_table)
        bm = self.menuBar().addMenu("&Blok Modeli")
        action(bm, "Blok modeli oluştur...", self.new_block_model)
        action(bm, "Variogram analizi...", self.variogram)
        action(bm, "Tenör kestirimi (NN / IDW / Kriging)...", self.estimate)
        bm.addSeparator()
        action(bm, "Tenör-tonaj raporu...", self.grade_tonnage)
        action(bm, "Blok modelini CSV olarak dışa aktar...", self.export_block_model)
        help_menu = self.menuBar().addMenu("&Yardım")
        action(help_menu, "Örnek sondaj projesini aç", self.open_sample)
        action(help_menu, "Hakkında", self.about)

    def _guard(self, slot: Any) -> None:
        try:
            slot()
        except Exception as exc:
            QMessageBox.critical(self, "MineCore", f"{exc}\n\n{traceback.format_exc(limit=3)}")

    # -- project -------------------------------------------------------------

    def new_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Yeni proje", "", "MineCore projesi (*.mcproj)")
        if not path:
            return
        if not path.endswith(".mcproj"):
            path += ".mcproj"
        self._set_project(Project.create(path, Path(path).stem))

    def open_project(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Proje klasörü (.mcproj)")
        if path:
            self._set_project(Project.open(path))

    def _set_project(self, project: Project) -> None:
        if self.project is not None:
            self.project.close()
        self.project = project
        dbs = DrillholeDatabase.__mro__ and project.objects("drillhole_db")
        self.db = DrillholeDatabase(project, dbs[0].object_id) if dbs else None
        self.setWindowTitle(f"MineCore {__version__} - {project.info.name}")
        self.refresh()

    def _need_db(self) -> DrillholeDatabase:
        if self.project is None:
            raise RuntimeError("Önce bir proje oluşturun veya açın.")
        if self.db is None:
            self.db = DrillholeDatabase.create(self.project, "Sondajlar")
        return self.db

    def undo(self) -> None:
        if self.project and self.project.undo():
            self.refresh()

    def redo(self) -> None:
        if self.project and self.project.redo():
            self.refresh()

    # -- drillholes ----------------------------------------------------------

    def import_folder(self) -> None:
        db = self._need_db()
        folder = QFileDialog.getExistingDirectory(self, "Sondaj dosyalarının klasörü")
        if not folder:
            return
        results = import_folder(db, Path(folder))
        lines = [
            f"{r.table.name}: {ROLE_TR[r.table.role]}, {r.table.rows} satır, "
            f"ayraç '{r.dialect.delimiter}', ondalık '{r.dialect.decimal}'"
            for r in results
        ]
        self.refresh()
        QMessageBox.information(self, "İçe aktarıldı", "\n".join(lines))

    def import_file(self) -> None:
        db = self._need_db()
        path, _ = QFileDialog.getOpenFileName(
            self, "Sondaj dosyası", "", "Metin tabloları (*.csv *.txt *.tsv)"
        )
        if not path:
            return
        roles = [ROLE_TR[r] for r in Role]
        choice, ok = QInputDialog.getItem(self, "Tablo türü", "Bu dosya nedir?", roles, 2, False)
        if not ok:
            return
        role = list(Role)[roles.index(choice)]
        dip = "negative_down"
        if role in (Role.SURVEY, Role.COLLAR):
            conv, ok = QInputDialog.getItem(
                self,
                "Eğim işareti",
                "Aşağı eğimler dosyada nasıl yazılı?",
                ["Negatif (-60 aşağı)", "Pozitif (60 aşağı)"],
                0,
                False,
            )
            if not ok:
                return
            dip = "negative_down" if conv.startswith("Negatif") else "positive_down"
        name, ok = QInputDialog.getText(self, "Tablo adı", "Ad:", text=Path(path).stem)
        if ok and name.strip():
            import_table(db, name.strip(), role, Path(path), ImportOptions(dip_convention=dip))
            self.refresh()

    def revalidate(self) -> None:
        self.refresh()

    def composite(self) -> None:
        db = self._need_db()
        dialog = CompositeDialog(db, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        v = dialog.values()
        result = composite_table(
            db,
            v["table"],
            v["fields"],
            v["length"],
            domain_field=v["domain_field"],
            min_coverage=v["min_coverage"],
            below_detection_factor=v["below_detection_factor"],
            report=self.report,
        )
        name = f"{v['table']}_komp_{v['length']:g}m"
        save_composites(db, name, result, v["table"], v["fields"])
        self.refresh()
        index = self.color_by.findText(f"{name} / {v['fields'][0]}")
        if index >= 0:
            self.color_by.setCurrentIndex(index)
        QMessageBox.information(
            self, "Kompozit", f"{result.table.num_rows} kompozit '{name}' tablosuna yazıldı."
        )

    def export_table(self) -> None:
        db = self._need_db()
        names = list(db.tables())
        name, ok = QInputDialog.getItem(self, "Dışa aktar", "Tablo:", names, len(names) - 1, False)
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(self, "CSV kaydet", f"{name}.csv", "CSV (*.csv)")
        if not path:
            return
        table = db.table(name)
        cols = [c for c in table.column_names if not c.endswith("__flag")]
        with Path(path).open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(cols)
            data = {c: table.column(c).to_pylist() for c in cols}
            for i in range(table.num_rows):
                writer.writerow(["" if _blank(data[c][i]) else _fmt(data[c][i]) for c in cols])
        self.statusBar().showMessage(f"{path} yazıldı.")

    # -- block models --------------------------------------------------------

    def _need_project(self) -> Project:
        if self.project is None:
            raise RuntimeError("Önce bir proje oluşturun veya açın.")
        return self.project

    def new_block_model(self) -> None:
        project = self._need_project()
        dialog = BlockModelDialog(self._need_db(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        model = BlockModel.create(project, dialog.name.text().strip(), dialog.grid())
        self.refresh()
        QMessageBox.information(
            self, "Blok modeli", f"'{model.name}': {model.grid.blocks:,} blok oluşturuldu."
        )

    def variogram(self) -> None:
        VariogramDialog(self._need_db(), self._need_project(), self).exec()
        self.refresh()

    def estimate(self) -> None:
        project = self._need_project()
        if not block_models(project):
            raise RuntimeError("Önce 'Blok modeli oluştur' ile bir model tanımlayın.")
        dialog = EstimationDialog(self._need_db(), project, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        message = dialog.run()
        attribute = dialog.output.text().strip()
        self.refresh()
        index = self.block_by.findText(f"{dialog.model.currentText()} / {attribute}")
        if index >= 0:
            self.block_by.setCurrentIndex(index)
        QMessageBox.information(self, "Kestirim", message)

    def grade_tonnage(self) -> None:
        project = self._need_project()
        if not block_models(project):
            raise RuntimeError("Projede blok modeli yok.")
        GradeTonnageDialog(project, self).exec()

    def export_block_model(self) -> None:
        project = self._need_project()
        names = block_models(project)
        if not names:
            raise RuntimeError("Projede blok modeli yok.")
        name, ok = QInputDialog.getItem(self, "Dışa aktar", "Blok modeli:", names, 0, False)
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(self, "CSV kaydet", f"{name}.csv", "CSV (*.csv)")
        if path:
            rows = export_block_model_csv(Path(path), BlockModel.open(project, name))
            self.statusBar().showMessage(f"{rows:,} blok {path} dosyasına yazıldı.")

    def open_sample(self) -> None:
        """Create a project from the bundled 30-hole sample (with deliberate errors)."""
        folder = QFileDialog.getExistingDirectory(self, "Örnek projenin kaydedileceği klasör")
        if not folder:
            return
        target = Path(folder) / "Ornek.mcproj"
        index = 1
        while target.exists():
            index += 1
            target = Path(folder) / f"Ornek{index}.mcproj"
        self._set_project(Project.create(target, "Örnek"))
        import_folder(self._need_db(), SAMPLE_DIR)
        self.refresh()
        self.statusBar().showMessage(f"Örnek proje oluşturuldu: {target}")

    def about(self) -> None:
        QMessageBox.about(
            self, "MineCore", f"MineCore {__version__}\nMadencilik mühendisliği platformu."
        )

    # -- views ---------------------------------------------------------------

    def refresh(self) -> None:
        self.tree.clear()
        self.report = None
        if self.project is None:
            return
        root = QTreeWidgetItem([self.project.info.name, ""])
        self.tree.addTopLevelItem(root)
        if self.db is not None:
            db_item = QTreeWidgetItem([self.db.name, ""])
            root.addChild(db_item)
            for info in self.db.tables().values():
                item = QTreeWidgetItem([f"{info.name} ({ROLE_TR[info.role]})", str(info.rows)])
                item.setData(0, Qt.ItemDataRole.UserRole, info.name)
                db_item.addChild(item)
            if self.db.tables_with_role(Role.COLLAR):
                self.report = validate(self.db)
        for name in block_models(self.project):
            model = BlockModel.open(self.project, name)
            item = QTreeWidgetItem([f"{name} (blok modeli)", f"{model.grid.blocks:,}"])
            for attr in model.attributes:
                item.addChild(QTreeWidgetItem([attr, ""]))
            root.addChild(item)
        variograms = load_variograms(self.project)
        if variograms:
            vg_item = QTreeWidgetItem(["Variogram modelleri", str(len(variograms))])
            for vg_name, vg in variograms.items():
                s = vg.structures[0] if vg.structures else None
                text = f"C0={vg.nugget:.3g}" + (
                    f", C1={s.sill:.3g}, a={s.ranges.major:.0f}/{s.ranges.semi:.0f}/"
                    f"{s.ranges.minor:.0f} m"
                    if s
                    else ""
                )
                vg_item.addChild(QTreeWidgetItem([vg_name, text]))
            root.addChild(vg_item)
        self.tree.expandAll()
        self._fill_issues()
        self._fill_color_choices()
        self._fill_block_choices()
        self.redraw()
        if self.report is not None:
            errors = self.report.count(severity=Severity.ERROR)
            warnings = self.report.count(severity=Severity.WARNING)
            self.statusBar().showMessage(
                f"{len(self.report.holes)} kullanılabilir kuyu, {errors} hata, {warnings} uyarı"
            )

    def _fill_issues(self) -> None:
        self.issues.setSortingEnabled(False)
        issues = self.report.issues if self.report else ()
        self.issues.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            cells = [
                SEVERITY_TR[issue.severity],
                issue.code,
                issue.table,
                issue.hole_id or "",
                "" if issue.src_line is None else str(issue.src_line),
                f"{CATALOGUE[issue.code].title_tr}: {issue.message}",
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setForeground(QColor(SEVERITY_COLOR[issue.severity]))
                self.issues.setItem(row, col, item)
        self.issues.setSortingEnabled(True)
        self.issues.resizeColumnsToContents()

    def _fill_color_choices(self) -> None:
        self.color_by.blockSignals(True)
        current = self.color_by.currentText()
        self.color_by.clear()
        self.color_by.addItem("(yalnız kuyu izleri)")
        if self.db is not None:
            for info in self.db.tables_with_role(Role.INTERVAL):
                for spec in info.fields:
                    if not spec.name.endswith("_coverage") and not spec.name.startswith("mid_"):
                        self.color_by.addItem(f"{info.name} / {spec.name}")
        index = self.color_by.findText(current)
        self.color_by.setCurrentIndex(max(index, 0))
        self.color_by.blockSignals(False)

    def _fill_block_choices(self) -> None:
        self.block_by.blockSignals(True)
        current = self.block_by.currentText()
        self.block_by.clear()
        self.block_by.addItem("(blok gösterme)")
        if self.project is not None:
            for name in block_models(self.project):
                model = BlockModel.open(self.project, name)
                self.block_by.addItem(f"{name} / (ızgara)")
                for attr in model.attributes:
                    self.block_by.addItem(f"{name} / {attr}")
        index = self.block_by.findText(current)
        self.block_by.setCurrentIndex(max(index, 0))
        self.block_by.blockSignals(False)

    def _show_table_item(self, item: QTreeWidgetItem) -> None:
        name = item.data(0, Qt.ItemDataRole.UserRole)
        if not name or self.db is None:
            return
        table = self.db.table(name)
        cols = [c for c in table.column_names if not c.endswith("__flag")]
        rows = min(table.num_rows, 5000)
        self.data.clear()
        self.data.setColumnCount(len(cols))
        self.data.setRowCount(rows)
        self.data.setHorizontalHeaderLabels(cols)
        for c, col in enumerate(cols):
            values = table.column(col).slice(0, rows).to_pylist()
            for r, value in enumerate(values):
                self.data.setItem(r, c, QTableWidgetItem("" if _blank(value) else _fmt(value)))
        self.tabs.setCurrentWidget(self.data)

    def redraw(self) -> None:
        if not hasattr(self.view, "add_mesh"):
            return
        import pyvista as pv  # noqa: PLC0415

        self.view.clear()
        self.view.add_axes()
        geometry = (
            hole_geometry(self.db, self.report)
            if self.db is not None and self.report is not None
            else {}
        )
        model = self._selected_block_model()
        if geometry:
            origin = np.mean([g.collar for g in geometry.values()], axis=0)
        elif model is not None:
            origin = np.asarray(model[0].grid.origin)
        else:
            self.view.render()
            return
        if model is not None:
            self._draw_blocks(model[0], model[1], origin)
        if not geometry:
            self.view.reset_camera()
            self.view.render()
            return
        traces = []
        for g in geometry.values():
            pts = g.trace() - origin
            traces.append(pv.lines_from_points(pts))
        self.view.add_mesh(pv.merge(traces), color="#9e9e9e", line_width=2)
        collars = np.array([g.collar for g in geometry.values()]) - origin
        self.view.add_point_labels(
            collars,
            list(geometry),
            font_size=10,
            point_size=6,
            text_color="white",
            shape_opacity=0.0,
            always_visible=True,
        )
        choice = self.color_by.currentText()
        if " / " in choice:
            table_name, field_name = choice.split(" / ", 1)
            self._draw_intervals(geometry, table_name, field_name, origin)
        self.view.reset_camera()
        self.view.render()

    def _selected_block_model(self) -> tuple[BlockModel, str | None] | None:
        choice = self.block_by.currentText()
        if self.project is None or " / " not in choice:
            return None
        name, attr = choice.split(" / ", 1)
        try:
            model = BlockModel.open(self.project, name)
        except Exception:
            return None
        return model, (None if attr == "(ızgara)" else attr)

    def _draw_blocks(self, model: BlockModel, attr: str | None, origin: Any) -> None:
        import pyvista as pv  # noqa: PLC0415

        grid = model.grid
        image = pv.ImageData(
            dimensions=np.array(grid.count) + 1,
            spacing=grid.size,
            origin=np.asarray(grid.origin) - origin,
        )
        if attr is None:
            self.view.add_mesh(image.outline(), color="#ffca28", line_width=2)
            return
        # Cell order of ImageData is i fastest, then j, then k: same as the model.
        values = model.values(attr)
        image.cell_data[attr] = values
        cutoff = self.block_cutoff.value()
        keep = np.isfinite(values) & (values >= cutoff)
        self.view.add_mesh(image.outline(), color="#757575", line_width=1)
        if not keep.any():
            self.statusBar().showMessage(f"{attr}: sınır tenör üstünde blok yok.")
            return
        shown = image.extract_cells(keep)  # type: ignore[arg-type]
        finite = values[np.isfinite(values)]
        lo = max(float(np.nanpercentile(finite, 2)), cutoff)
        hi = float(np.nanpercentile(finite, 98))
        self.view.add_mesh(
            shown,
            scalars=attr,
            cmap="turbo",
            clim=(lo, hi if hi > lo else lo + 1.0),
            show_edges=grid.blocks <= 200_000,
            edge_color="#202020",
            scalar_bar_args={"title": attr},
        )
        self.statusBar().showMessage(f"{attr}: {int(keep.sum()):,} blok ≥ {cutoff:g} gösteriliyor.")

    def _draw_intervals(
        self, geometry: dict[str, Any], table_name: str, field_name: str, origin: Any
    ) -> None:
        import pyvista as pv  # noqa: PLC0415

        assert self.db is not None
        assert self.report is not None
        info = self.db.table_info(table_name)
        table = self.db.table(table_name)
        usable = self.report.usable[table_name]
        ids = table.column("hole_id").to_pylist()
        start = table.column("from").to_numpy()
        end = table.column("to").to_numpy()
        spec = next(f for f in info.fields if f.name == field_name)
        raw = table.column(field_name)
        numeric = spec.type is FieldType.NUMERIC
        values = (
            np.asarray(raw.to_numpy(zero_copy_only=False), float)
            if numeric
            else raw.cast("string").to_pylist()
        )
        points: list[Any] = []
        lines: list[int] = []
        scalars: list[Any] = []
        for i, hole in enumerate(ids):
            if not usable[i] or hole not in geometry:
                continue
            ends = geometry[hole].at(np.array([start[i], end[i]])) - origin
            base = len(points)
            points.extend(ends)
            lines.extend([2, base, base + 1])
            scalars.append(values[i])
        if not points:
            return
        mesh = pv.PolyData(np.array(points), lines=np.array(lines))
        if numeric:
            mesh.cell_data[field_name] = np.array(scalars, float)
            finite = np.isfinite(mesh.cell_data[field_name])
            hi = (
                float(np.nanpercentile(mesh.cell_data[field_name][finite], 97))
                if finite.any()
                else 1.0
            )
            self.view.add_mesh(
                mesh,
                scalars=field_name,
                cmap="turbo",
                clim=(0, hi or 1.0),
                line_width=8,
                nan_color="#555555",
                scalar_bar_args={"title": field_name},
            )
        else:
            codes = sorted({str(v) for v in scalars})
            mesh.cell_data[field_name] = np.array([codes.index(str(v)) for v in scalars])
            self.view.add_mesh(
                mesh,
                scalars=field_name,
                cmap="tab20",
                line_width=8,
                categories=True,
                annotations=dict(enumerate(codes)),
                scalar_bar_args={"title": field_name},
            )

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        if self.project is not None:
            self.project.close()
        if hasattr(self.view, "close"):
            self.view.close()
        super().closeEvent(event)


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MineCore")
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and sys.argv[1].endswith(".mcproj"):
        window._guard(lambda: window._set_project(Project.open(sys.argv[1])))
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
