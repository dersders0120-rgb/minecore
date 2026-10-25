"""Underground workbench: stope envelope, stope selection, decline layout."""

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
from minecore.underground.development import (
    DeclineDesign,
    Development,
    development_from_model,
)
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


class DeclineDialog(QDialog):
    """Decline from a portal and level crosscuts to the selected stopes."""

    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Yeraltı: dekline ve kat galerileri")
        self.project = project
        self.dev: Development | None = None
        form = QFormLayout(self)
        self.model = QComboBox()
        self.model.addItems(block_models(project))
        self.ore = QComboBox()
        self.px = _spin(0.0, -1e8, 1e8, 10.0, 1)
        self.py = _spin(0.0, -1e8, 1e8, 10.0, 1)
        self.pz = _spin(0.0, -1e5, 1e5, 5.0, 1)
        self.interval = _spin(25.0, 2.0, 200.0, 5.0, 1)
        self.gradient = _spin(14.3, 1.0, 25.0, 0.5, 1)
        self.radius = _spin(25.0, 5.0, 200.0, 1.0, 1)
        self.standoff = _spin(40.0, 0.0, 500.0, 5.0, 1)
        self.drive_w = _spin(5.0, 1.0, 20.0, 0.5, 1)
        self.drive_h = _spin(5.5, 1.0, 20.0, 0.5, 1)
        self.direction = QComboBox()
        self.direction.addItems(["Saat yönünde (inerken)", "Saat yönünün tersine (inerken)"])
        form.addRow("Blok modeli", self.model)
        form.addRow("Cevher işareti (pano/zarf)", self.ore)
        form.addRow("Ağız (portal) X / Y / Z", _row(self.px, self.py, self.pz))
        form.addRow("Kat aralığı (m)", self.interval)
        form.addRow("Eğim % / dönüş yarıçapı (m)", _row(self.gradient, self.radius))
        form.addRow("Cevherden uzaklık (m)", self.standoff)
        form.addRow("Galeri genişliği / yüksekliği (m)", _row(self.drive_w, self.drive_h))
        form.addRow("Spiral yönü", self.direction)
        run = QPushButton("Dekline ve kat galerilerini tasarla")
        run.clicked.connect(self._guard(lambda: self.run(None)))
        export = QPushButton("DXF olarak kaydet...")
        export.clicked.connect(self._guard(self.export))
        form.addRow(_row(run, export))
        self.text = QLabel()
        self.text.setWordWrap(True)
        form.addRow(self.text)
        self.model.currentTextChanged.connect(self._fill)
        self._fill(self.model.currentText())

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Dekline", str(exc))

        return run

    def _fill(self, name: str) -> None:
        self.ore.clear()
        if not name:
            return
        model = BlockModel.open(self.project, name)
        flags = [a for a in model.attributes if a.startswith(("ug_", "dom_"))]
        self.ore.addItems(flags)
        g = model.grid
        # Default portal: beside the model on the east side, at the model top.
        self.px.setValue(g.origin[0] + g.size[0] * g.count[0] + 100.0)
        self.py.setValue(g.origin[1] + 0.5 * g.size[1] * g.count[1])
        self.pz.setValue(g.origin[2] + g.size[2] * g.count[2])

    def run(self, dxf: Path | None) -> None:
        design = DeclineDesign(
            self.gradient.value() / 100.0,
            self.radius.value(),
            self.standoff.value(),
            self.drive_w.value(),
            self.drive_h.value(),
            clockwise=self.direction.currentIndex() == 0,
        )
        self.dev = d = development_from_model(
            BlockModel.open(self.project, self.model.currentText()),
            (self.px.value(), self.py.value(), self.pz.value()),
            design,
            ore_attribute=self.ore.currentText(),
            level_interval=self.interval.value(),
            dxf=dxf,
            project=self.project,
            save_as="dekline",
        )
        self.text.setText(
            f"Dekline {d.decline_length:,.0f} m, {len(d.accesses)} kat galerisi "
            f"{d.crosscut_length:,.0f} m; toplam {d.total_length:,.0f} m, "
            f"{d.volume:,.0f} m³ ({d.tonnes:,.0f} t) kazı. Çizgiler 'dekline' adıyla "
            "kaydedildi (3B görünümde)."
        )

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "DXF kaydet", "dekline.dxf", "DXF (*.dxf)")
        if path:
            self.run(Path(path))
