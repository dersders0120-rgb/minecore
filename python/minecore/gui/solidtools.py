"""Solid tools dialog: verify and repair, booleans, cut by a surface."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from minecore import surfaces
from minecore.gui.layout import fit_to_screen
from minecore.gui.resource import _row, _spin
from minecore.project import Project
from minecore.solids import load_solid, save_solid, solid_names
from minecore.solidtools import Boolean, boolean, cut_by_surface, repair, verify

OPS = {
    "Birleşim (A + B)": Boolean.UNION,
    "Kesişim (A ve B ortak)": Boolean.INTERSECTION,
    "Fark (A eksi B)": Boolean.DIFFERENCE,
}


class SolidToolsDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Katı model araçları")
        fit_to_screen(self, 640, 560)
        self.project = project
        form = QFormLayout(self)
        names = solid_names(project)
        self.a = QComboBox()
        self.a.addItems(names)
        self.b = QComboBox()
        self.b.addItems(names)
        if len(names) > 1:
            self.b.setCurrentIndex(1)
        form.addRow("Katı A / katı B", _row(self.a, self.b))
        check = QPushButton("A'yı doğrula")
        check.clicked.connect(self._guard(self.check))
        fix = QPushButton("A'yı onar ve kaydet")
        fix.clicked.connect(self._guard(self.fix))
        form.addRow(_row(check, fix))
        self.op = QComboBox()
        self.op.addItems(list(OPS))
        self.voxel = _spin(1.0, 0.05, 50.0, 0.25, 2)
        self.voxel.setToolTip("Sonuç bu boyutta yuvarlanır; ayrıntıya göre seçin.")
        form.addRow("İşlem / voksel boyutu (m)", _row(self.op, self.voxel))
        run = QPushButton("Boolean işlemini yap")
        run.clicked.connect(self._guard(self.run_boolean))
        form.addRow(run)
        self.surface = QComboBox()
        self.surface.addItems(surfaces.surface_names(project))
        self.side = QComboBox()
        self.side.addItems(["Yüzeyin altı", "Yüzeyin üstü"])
        form.addRow("A'yı yüzeyle kes", _row(self.surface, self.side))
        cut = QPushButton("Kes")
        cut.clicked.connect(self._guard(self.run_cut))
        form.addRow(cut)
        self.name = QLineEdit("sonuc_kati")
        form.addRow("Sonuç adı", self.name)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Katı model araçları", str(exc))

        return run

    def _need(self, box: QComboBox) -> str:
        if not box.currentText():
            raise RuntimeError("Önce katı model içe aktarın veya oluşturun.")
        return box.currentText()

    def _out(self) -> str:
        name = self.name.text().strip()
        if not name:
            raise RuntimeError("Sonuç adı yazın.")
        return name

    def _stored(self, name: str) -> None:
        for box in (self.a, self.b):
            if box.findText(name) < 0:
                box.addItem(name)

    def check(self) -> None:
        r = verify(load_solid(self.project, self._need(self.a)))
        self.summary.setText(
            f"{r.triangles:,} üçgen, {r.shells} kabuk, hacim {r.volume:,.0f} m³, alan "
            f"{r.area:,.0f} m². Açık kenar {r.open_edges}, çok-yüzlü kenar "
            f"{r.non_manifold_edges}, sıfır alanlı üçgen {r.degenerate}, tekrar {r.duplicates}, "
            f"ters yönlü kenar {r.flipped_edges}"
            + (", normaller içe dönük" if r.volume < 0 else "")
            + (". Geçerli." if r.valid else ". Onarım gerekli.")
        )

    def fix(self) -> None:
        out = self._out()
        fixed = repair(load_solid(self.project, self._need(self.a)))
        save_solid(self.project, out, fixed)
        self._stored(out)
        r = verify(fixed)
        self.summary.setText(
            f"'{out}' kaydedildi: hacim {r.volume:,.0f} m³, açık kenar {r.open_edges}"
            + (" (kapalı değil: eksik yüzler onarımla eklenmez)." if r.open_edges else ".")
        )

    def run_boolean(self) -> None:
        out = self._out()
        result = boolean(
            load_solid(self.project, self._need(self.a)),
            load_solid(self.project, self._need(self.b)),
            OPS[self.op.currentText()],
            self.voxel.value(),
        )
        save_solid(self.project, out, result)
        self._stored(out)
        self.summary.setText(f"'{out}' kaydedildi: hacim {result.volume():,.0f} m³.")

    def run_cut(self) -> None:
        out = self._out()
        if not self.surface.currentText():
            raise RuntimeError("Önce bir yüzey içe aktarın.")
        result = cut_by_surface(
            load_solid(self.project, self._need(self.a)),
            surfaces.load_surface(self.project, self.surface.currentText()),
            self.side.currentIndex() == 0,
            self.voxel.value(),
        )
        save_solid(self.project, out, result)
        self._stored(out)
        self.summary.setText(f"'{out}' kaydedildi: hacim {result.volume():,.0f} m³.")
