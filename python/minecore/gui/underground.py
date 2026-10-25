"""Underground workbench: stope envelope and stope selection."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from minecore.blockmodel import BlockModel
from minecore.gui.resource import _row, _spin, block_models
from minecore.project import Project
from minecore.underground.stopes import StopeResult, stopes_from_model


class StopeDialog(QDialog):
    """Floating stope envelope and non-overlapping stopes."""

    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Yeraltı: pano zarfı ve pano seçimi")
        self.resize(1000, 600)
        self.project = project
        self.stopes: StopeResult | None = None
        layout = QHBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form, 0)
        self.model = QComboBox()
        self.model.addItems(block_models(project))
        self.grade = QComboBox()
        self.density_attr = QComboBox()
        self.density = _spin(2.7, 0.1, 25.0, 0.05, 3)
        self.rock = QComboBox()
        self.sx = _spin(20.0, 0.1, 1000.0, 5.0, 1)
        self.sy = _spin(5.0, 0.1, 1000.0, 1.0, 1)
        self.sz = _spin(20.0, 0.1, 1000.0, 5.0, 1)
        self.cutoff = _spin(3.0, 0.0, 1e9, 0.1, 4)
        form.addRow("Blok modeli", self.model)
        form.addRow("Tenör", self.grade)
        form.addRow("Yoğunluk özniteliği", self.density_attr)
        form.addRow("Sabit yoğunluk (t/m³)", self.density)
        form.addRow("Kaya/hava özniteliği", self.rock)
        form.addRow("Pano boyutu X / Y / Z (m)", _row(self.sx, self.sy, self.sz))
        form.addRow("Sınır tenör (pano ortalaması)", self.cutoff)
        run = QPushButton("Hesapla ve kaydet")
        run.clicked.connect(self._guarded(self.run))
        export = QPushButton("Panoları DXF olarak dışa aktar...")
        export.clicked.connect(self._guarded(self.export))
        form.addRow(_row(run, export))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Pano", "i / j / k", "Tonaj (t)", "Tenör", "Metal"])
        layout.addWidget(self.table, 1)
        self.model.currentTextChanged.connect(self._fill)
        self._fill(self.model.currentText())

    def _guarded(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Yeraltı", str(exc))

        return run

    def _fill(self, name: str) -> None:
        for combo in (self.grade, self.density_attr, self.rock):
            combo.clear()
        self.density_attr.addItem("(sabit)")
        self.rock.addItem("(yok)")
        if not name:
            return
        attrs = [
            a
            for a in BlockModel.open(self.project, name).attributes
            if not a.endswith(("_n", "_dist", "_kv"))
        ]
        self.grade.addItems(attrs)
        self.density_attr.addItems(attrs)
        self.rock.addItems(attrs)
        if "kaya" in attrs:
            self.rock.setCurrentText("kaya")

    def _run(self, dxf: Path | None) -> StopeResult:
        d = self.density_attr.currentText()
        r = self.rock.currentText()
        return stopes_from_model(
            BlockModel.open(self.project, self.model.currentText()),
            self.grade.currentText(),
            (self.sx.value(), self.sy.value(), self.sz.value()),
            self.cutoff.value(),
            density=self.density.value() if d == "(sabit)" else d,
            rock_attribute=None if r == "(yok)" else r,
            dxf=dxf,
        )

    def run(self) -> None:
        self.stopes = r = self._run(None)
        st_t = sum(s.tonnes for s in r.stopes)
        st_m = sum(s.metal for s in r.stopes)
        self.summary.setText(
            f"Zarf: {r.envelope_tonnes / 1e6:,.3f} Mt, tenör {r.envelope_grade:.3f}. "
            f"Seçilen {len(r.stopes)} pano: {st_t / 1e6:,.3f} Mt, tenör "
            f"{(st_m / st_t if st_t else 0):.3f}. Öznitelikler 'ug_zarf' ve 'ug_pano' kaydedildi. "
            "Pano seçimi açgözlü (greedy) bir yöntemdir, optimum değildir."
        )
        self.table.setRowCount(len(r.stopes))
        for n, s in enumerate(r.stopes):
            cells = [str(n + 1), f"{s.i} / {s.j} / {s.k}", f"{s.tonnes:,.0f}", f"{s.grade:.3f}"]
            cells.append(f"{s.metal:,.0f}")
            for c, text in enumerate(cells):
                self.table.setItem(n, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "DXF kaydet", "panolar.dxf", "DXF (*.dxf)")
        if path:
            self.stopes = self._run(Path(path))
            self.summary.setText(f"{len(self.stopes.stopes)} pano yazıldı: {path}")
