"""Grade control dialog: blasthole CSV to ore/waste dig lines on a plan map."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from minecore.gradecontrol import GradeControl, grade_control, read_holes_csv
from minecore.gui.layout import fit_to_screen, scroll_panel
from minecore.gui.resource import _row, _spin
from minecore.io.dxf import Polyline, write_dxf
from minecore.project import Project
from minecore.strings import String, load_strings, save_strings, string_names

COLOURS = ["#9e9e9e", "#ffb300", "#e53935", "#8e24aa", "#1e88e5"]


def parse_cutoffs(text: str) -> tuple[float, ...]:
    try:
        values = sorted(float(v) for v in text.replace(",", ";").split(";") if v.strip())
    except ValueError:
        raise RuntimeError(f"Cut-off listesi sayı değil: {text!r}") from None
    if not values:
        raise RuntimeError("En az bir cut-off girin.")
    return tuple(values)


class GradeControlDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tenör kontrolü (patlatma deliği → kazı hatları)")
        fit_to_screen(self, 1200, 760)
        self.project = project
        layout = QHBoxLayout(self)
        form = QFormLayout()
        scroll_panel(layout, form)
        self.outline = QComboBox()
        self.outline.addItems(string_names(project))
        form.addRow("Atım sınırı (kapalı çizgi, kot = taban)", self.outline)
        self.path = QLineEdit()
        pick = QPushButton("Seç...")
        pick.clicked.connect(self._pick)
        form.addRow("Delik tenörleri (CSV: x, y, tenör)", _row(self.path, pick))
        self.grade = QLineEdit("au")
        form.addRow("Tenör sütunu", self.grade)
        self.cutoffs = QLineEdit("0.5")
        self.cutoffs.setPlaceholderText("örn. 0.5; 1.5 (pasa / düşük tenör / yüksek tenör)")
        form.addRow("Cut-off(lar)", self.cutoffs)
        self.bench = _spin(10.0, 1.0, 50.0, 1.0, 1)
        self.density = _spin(2.7, 1.0, 6.0, 0.05, 2)
        form.addRow("Basamak (m) / yoğunluk (t/m³)", _row(self.bench, self.density))
        self.cell = _spin(1.0, 0.25, 10.0, 0.25, 2)
        self.radius = _spin(15.0, 1.0, 200.0, 1.0, 1)
        form.addRow("Hücre (m) / arama yarıçapı (m)", _row(self.cell, self.radius))
        self.smu = _spin(5.0, 0.0, 50.0, 0.5, 1)
        self.smu.setToolTip("Seçici kazı birimi: bu genişlikten dar cevher/pasa parçası kalmaz.")
        form.addRow("En dar kazı genişliği (m)", self.smu)
        self.name = QLineEdit("kazi_hatlari")
        form.addRow("Kayıt adı (çizgi seti)", self.name)
        run = QPushButton("Kazı hatlarını hesapla ve kaydet")
        run.clicked.connect(self._guard(self.run))
        form.addRow(run)
        dxf = QPushButton("Kazı hatlarını DXF olarak dışa aktar...")
        dxf.clicked.connect(self._guard(self.export))
        form.addRow(dxf)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        right = QVBoxLayout()
        self.figure = Figure(figsize=(7, 5))
        self.canvas = FigureCanvasQTAgg(self.figure)  # type: ignore[no-untyped-call]
        right.addWidget(self.canvas, 3)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Sınıf", "Alan m²", "Kazılan t", "Ort. tenör", "İdeal t", "Poligon"]
        )
        right.addWidget(self.table, 1)
        layout.addLayout(right, 3)
        self.gc: GradeControl | None = None

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Tenör kontrolü", str(exc))

        return run

    def _pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Delik tenörleri", "", "CSV (*.csv *.txt)")
        if path:
            self.path.setText(path)

    def run(self) -> None:
        name = self.outline.currentText()
        rings = [s.points for s in load_strings(self.project, name) if s.closed] if name else []
        if not rings:
            raise RuntimeError("Önce atım sınırını kapalı çizgi olarak çizin veya içe aktarın.")
        if not self.path.text():
            raise RuntimeError("Delik tenörleri dosyasını seçin.")
        cutoffs = parse_cutoffs(self.cutoffs.text())
        self.gc = gc = grade_control(
            rings[0],
            read_holes_csv(Path(self.path.text()), self.grade.text()),
            cutoffs,
            bench_height=self.bench.value(),
            density=self.density.value(),
            cell=self.cell.value(),
            radius=self.radius.value(),
            smu=self.smu.value(),
        )
        out = self.name.text().strip() or "kazi_hatlari"
        save_strings(self.project, out, [String(p, True, f"SINIF_{c}") for c, p in gc.lines])
        self.table.setRowCount(len(gc.rows))
        for i, r in enumerate(gc.rows):
            cells = [
                _class_name(r.cls, cutoffs),
                f"{r.area:,.0f}",
                f"{r.tonnes:,.0f}",
                "" if math.isnan(r.grade) else f"{r.grade:.3f}",
                f"{r.ideal_tonnes:,.0f}",
                str(r.polygons),
            ]
            for j, text in enumerate(cells):
                self.table.setItem(i, j, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        self.summary.setText(
            f"Cevher kaybı (pasaya giden) {gc.ore_loss_t:,.0f} t, seyrelme (cevhere giden pasa) "
            f"{gc.dilution_t:,.0f} t, yanlış sınıf cevher {gc.misrouted_t:,.0f} t. "
            f"Kazı hatları '{out}' çizgi seti olarak kaydedildi."
        )
        self._plot(gc, cutoffs)

    def _plot(self, gc: GradeControl, cutoffs: tuple[float, ...]) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        n = len(cutoffs) + 1
        cmap = ListedColormap(COLOURS[:n] if n <= len(COLOURS) else COLOURS)
        shown = np.ma.masked_less(gc.dug, 0)
        dx, dy = (gc.x[1] - gc.x[0]) / 2, (gc.y[1] - gc.y[0]) / 2
        ax.imshow(
            shown,
            origin="lower",
            extent=(gc.x[0] - dx, gc.x[-1] + dx, gc.y[0] - dy, gc.y[-1] + dy),
            cmap=cmap,
            vmin=-0.5,
            vmax=n - 0.5,
            interpolation="nearest",
        )
        for _c, line in gc.lines:
            ax.plot(line[:, 0], line[:, 1], color="black", lw=0.8)
        ax.set_aspect("equal")
        ax.set_title("Kazı sınıfları ve hatları")
        self.figure.tight_layout()
        self.canvas.draw_idle()  # type: ignore[no-untyped-call]

    def export(self) -> None:
        if self.gc is None:
            raise RuntimeError("Önce hesaplayın.")
        path, _ = QFileDialog.getSaveFileName(self, "DXF", "kazi_hatlari.dxf", "DXF (*.dxf)")
        if path:
            write_dxf(Path(path), [Polyline(f"GC_CLASS_{c}", p, True) for c, p in self.gc.lines])


def _class_name(cls: int, cutoffs: tuple[float, ...]) -> str:
    if cls == 0:
        return f"Pasa (< {cutoffs[0]:g})"
    hi = f"{cutoffs[cls]:g}" if cls < len(cutoffs) else "∞"
    return f"Cevher {cls} ({cutoffs[cls - 1]:g}-{hi})"
