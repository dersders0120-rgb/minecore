"""Waste dump design dialog."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
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
from minecore.gui.layout import fit_to_screen, scroll_panel
from minecore.gui.resource import _row, _spin
from minecore.pitopt.benchdesign import BenchGeometry
from minecore.pitopt.dump import DumpDesign, capacity_t, design_dump
from minecore.project import Project
from minecore.strings import load_strings, string_names


class DumpDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Döküm sahası tasarımı")
        fit_to_screen(self, 900, 600)
        self.project = project
        layout = QHBoxLayout(self)
        form = QFormLayout()
        scroll_panel(layout, form)
        self.outline = QComboBox()
        self.outline.addItems(string_names(project))
        self.ground = QComboBox()
        self.ground.addItem("(düz zemin)")
        self.ground.addItems(surfaces.surface_names(project))
        self.ground_z = _spin(0.0, -1e5, 1e5, 10.0, 1)
        self.lift = _spin(10.0, 1.0, 100.0, 1.0, 1)
        self.face = _spin(37.0, 10.0, 60.0, 1.0, 1)
        self.berm = _spin(15.0, 0.0, 200.0, 1.0, 1)
        self.max_h = _spin(60.0, 0.0, 1000.0, 5.0, 1)
        self.max_h.setSpecialValueText("sınırsız")
        self.swell = _spin(1.30, 1.0, 2.5, 0.05, 2)
        self.density = _spin(2.6, 1.0, 5.0, 0.05, 2)
        self.need = _spin(0.0, 0.0, 1e5, 1.0, 2)
        self.res = _spin(5.0, 0.5, 50.0, 0.5, 1)
        form.addRow("Döküm sınırı (kapalı çizgi seti)", self.outline)
        form.addRow("Zemin yüzeyi / düz zemin kotu", _row(self.ground, self.ground_z))
        form.addRow("Kat yüksekliği (m) / şev açısı (°)", _row(self.lift, self.face))
        form.addRow("Berm (m) / en büyük yükseklik (m)", _row(self.berm, self.max_h))
        form.addRow("Kabarma faktörü / yerinde yoğunluk (t/m³)", _row(self.swell, self.density))
        form.addRow("Yerleştirilecek pasa (Mt, isteğe bağlı)", self.need)
        form.addRow("Raster çözünürlüğü (m)", self.res)
        run = QPushButton("Dökümü tasarla ve yüzeyi kaydet")
        run.clicked.connect(self._guard(self.run))
        form.addRow(run)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Kat taban kotu", "Gevşek hacim (Mm³)", "Kümülatif Mt"]
        )
        layout.addWidget(self.table, 1)
        self.dump: DumpDesign | None = None

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Döküm sahası", str(exc))

        return run

    def run(self) -> None:
        name = self.outline.currentText()
        if not name:
            raise RuntimeError("Önce döküm sınırını çizgi olarak içe aktarın (Yüzey menüsü).")
        rings = [s for s in load_strings(self.project, name) if s.closed]
        if not rings:
            raise RuntimeError(f"'{name}' içinde kapalı çizgi yok.")
        g = self.ground.currentText()
        ground = None if self.ground.currentIndex() == 0 else surfaces.load_surface(self.project, g)
        self.dump = d = design_dump(
            rings[0].points,
            BenchGeometry(self.face.value(), self.lift.value(), self.berm.value()),
            ground=ground,
            ground_z=self.ground_z.value(),
            max_height=self.max_h.value() or None,
            resolution=self.res.value(),
        )
        surfaces.save_surface(self.project, f"dokum_{name}", d.surface())
        cap = capacity_t(d, self.density.value(), self.swell.value())
        lifts = d.volume_by_lift()
        self.table.setRowCount(len(lifts))
        cumulative = 0.0
        for n, (level, vol) in enumerate(lifts):
            cumulative += vol / self.swell.value() * self.density.value()
            for c, text in enumerate(
                (f"{level:,.1f}", f"{vol / 1e6:,.3f}", f"{cumulative / 1e6:,.2f}")
            ):
                self.table.setItem(n, c, QTableWidgetItem(text))
        need = self.need.value() * 1e6
        verdict = ""
        if need > 0:
            verdict = f" Gereken {need / 1e6:,.2f} Mt: " + (
                "sığıyor." if cap >= need else f"{(need - cap) / 1e6:,.2f} Mt eksik kapasite."
            )
        self.summary.setText(
            f"Gevşek hacim {d.volume() / 1e6:,.2f} Mm³, taban alanı {d.area() / 1e4:,.1f} ha, "
            f"üst kot {d.top:,.1f} m, {len(lifts)} kat. Kapasite {cap / 1e6:,.2f} Mt "
            f"(yerinde).{verdict} Yüzey 'dokum_{name}' olarak kaydedildi."
        )
