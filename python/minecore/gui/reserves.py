"""Design reserves per bench and cut/fill volume between surfaces."""

from __future__ import annotations

import csv
import math
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

from minecore import surfaces
from minecore.blockmodel import BlockModel
from minecore.gui.layout import fit_to_screen, scroll_panel
from minecore.gui.resource import _row, _spin, block_models
from minecore.project import Project
from minecore.reserves import BenchRow, bench_reserves, between_fraction, volume_between

HEADERS = [
    "Basamak taban kotu",
    "Sınıf",
    "Toplam Mt",
    "Cevher Mt",
    "Cevher tenörü",
    "Pasa Mt",
    "Pasa/cevher",
    "Tahminsiz Mt",
]


class ReservesDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:  # noqa: PLR0915 - form
        super().__init__(parent)
        self.setWindowTitle("Tasarım rezervi ve hacim")
        fit_to_screen(self, 1000, 650)
        self.project = project
        layout = QHBoxLayout(self)
        form = QFormLayout()
        scroll_panel(layout, form)
        names = surfaces.surface_names(project)
        self.model = QComboBox()
        self.model.addItems(block_models(project))
        self.grade = QComboBox()
        self.by = QComboBox()
        self.density_attr = QComboBox()
        self.top = QComboBox()
        self.top.addItem("(yok)")
        self.top.addItems(names)
        if "topo" in names:
            self.top.setCurrentText("topo")
        self.bottom = QComboBox()
        self.bottom.addItems(names)
        self.cutoff = _spin(0.5, 0.0, 1e6, 0.1, 3)
        self.density = _spin(2.7, 0.5, 10.0, 0.05, 2)
        self.bench = _spin(0.0, 0.0, 100.0, 1.0, 1)
        self.bench.setSpecialValueText("blok yüksekliği")
        form.addRow("Blok modeli", self.model)
        form.addRow("Tenör / cut-off", _row(self.grade, self.cutoff))
        form.addRow("Yoğunluk (öznitelik / sabit t/m³)", _row(self.density_attr, self.density))
        form.addRow("Üst yüzey (topoğrafya)", self.top)
        form.addRow("Alt yüzey (pit tasarımı / dönem sonu)", self.bottom)
        form.addRow("Basamak yüksekliği (m)", self.bench)
        form.addRow("Basamakları ayır (kaya tipi, kategori)", self.by)
        run = QPushButton("Basamak rezervini hesapla")
        run.clicked.connect(self._guard(self.run))
        form.addRow(run)
        export = QPushButton("Tabloyu CSV olarak kaydet...")
        export.clicked.connect(self._guard(self.export))
        form.addRow(export)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        self.vol_a = QComboBox()
        self.vol_a.addItems(names)
        self.vol_b = QComboBox()
        self.vol_b.addItems(names)
        if len(names) > 1:
            self.vol_b.setCurrentIndex(1)
        self.cell = _spin(5.0, 0.5, 100.0, 0.5, 1)
        form.addRow(QLabel("<b>İki yüzey arası hacim (kazı / dolgu)</b>"))
        form.addRow("Yüzey A / yüzey B", _row(self.vol_a, self.vol_b))
        form.addRow("Raster hücresi (m)", self.cell)
        vol = QPushButton("Hacmi hesapla")
        vol.clicked.connect(self._guard(self.volume))
        form.addRow(vol)
        self.vol_label = QLabel()
        self.vol_label.setWordWrap(True)
        form.addRow(self.vol_label)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        layout.addWidget(self.table, 1)
        self.rows: list[BenchRow] = []
        self.model.currentTextChanged.connect(self._fill)
        self._fill(self.model.currentText())

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Tasarım rezervi", str(exc))

        return run

    def _fill(self, name: str) -> None:
        for box in (self.grade, self.by, self.density_attr):
            box.clear()
        self.by.addItem("(ayırma)")
        self.density_attr.addItem("(sabit)")
        if not name:
            return
        attrs = [a for a in BlockModel.open(self.project, name).attributes if a != "kaya"]
        self.grade.addItems(attrs)
        self.by.addItems(attrs)
        self.density_attr.addItems(attrs)

    def run(self) -> None:
        if not self.model.currentText() or not self.bottom.currentText():
            raise RuntimeError("Blok modeli ve alt yüzey (pit tasarımı) gerekli.")
        model = BlockModel.open(self.project, self.model.currentText())
        top = (
            None
            if self.top.currentIndex() == 0
            else surfaces.load_surface(self.project, self.top.currentText())
        )
        bottom = surfaces.load_surface(self.project, self.bottom.currentText())
        density: float | str = (
            self.density.value()
            if self.density_attr.currentIndex() == 0
            else self.density_attr.currentText()
        )
        self.rows = rows = bench_reserves(
            model,
            self.grade.currentText(),
            self.cutoff.value(),
            between_fraction(model, top, bottom),
            density=density,
            bench_height=self.bench.value() or None,
            category=None if self.by.currentIndex() == 0 else self.by.currentText(),
        )
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            cells = [
                f"{r.toe:,.1f}",
                r.category,
                f"{r.total_t / 1e6:,.3f}",
                f"{r.ore_t / 1e6:,.3f}",
                "" if math.isnan(r.grade) else f"{r.grade:.3f}",
                f"{r.waste_t / 1e6:,.3f}",
                "" if math.isinf(r.strip_ratio) else f"{r.strip_ratio:.2f}",
                f"{r.unestimated_t / 1e6:,.3f}",
            ]
            for j, text in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        ore = sum(r.ore_t for r in rows)
        waste = sum(r.waste_t for r in rows)
        metal = sum(r.metal for r in rows)
        self.summary.setText(
            f"Toplam cevher {ore / 1e6:,.2f} Mt"
            + (f", ortalama tenör {metal / ore:.3f}" if ore > 0 else "")
            + f"; pasa {waste / 1e6:,.2f} Mt"
            + (f"; pasa/cevher {waste / ore:.2f}" if ore > 0 else "")
            + ". Sınır bloklar yüzeylerle kesilen yükseklik oranında sayıldı. "
            "Bu bir tasarım envanteridir, JORC/NI 43-101 rezerv beyanı değildir."
        )

    def export(self) -> None:
        if not self.rows:
            raise RuntimeError("Önce rezervi hesaplayın.")
        path, _ = QFileDialog.getSaveFileName(self, "Kaydet", "basamak_rezerv.csv", "CSV (*.csv)")
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["toe", "class", "total_t", "ore_t", "grade", "waste_t", "unestimated_t"])
            for r in self.rows:
                w.writerow(
                    [r.toe, r.category, r.total_t, r.ore_t, r.grade, r.waste_t, r.unestimated_t]
                )

    def volume(self) -> None:
        a, b = self.vol_a.currentText(), self.vol_b.currentText()
        if not a or not b:
            raise RuntimeError("İki yüzey gerekli.")
        v = volume_between(
            surfaces.load_surface(self.project, a),
            surfaces.load_surface(self.project, b),
            self.cell.value(),
        )
        self.vol_label.setText(
            f"Kazı ({a} üstte): {v.cut / 1e6:,.3f} Mm³; dolgu ({b} üstte): {v.fill / 1e6:,.3f} "
            f"Mm³; net {v.net / 1e6:,.3f} Mm³; ortak alan {v.area / 1e4:,.2f} ha."
        )
