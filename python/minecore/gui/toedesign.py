"""Pit design expanded upwards from digitised toe strings (pit floors)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from minecore import surfaces
from minecore.gui.layout import fit_to_screen, scroll_panel
from minecore.gui.resource import _row, _spin
from minecore.io.dxf import write_dxf
from minecore.pitopt.benchdesign import (
    BenchGeometry,
    DesignedPit,
    Ramp,
    design_from_toes,
    parse_sectors,
)
from minecore.project import Project
from minecore.strings import String, load_strings, save_strings, string_names


class ToeDesignDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:  # noqa: PLR0915 - one form
        super().__init__(parent)
        self.setWindowTitle("Taban çizgisinden pit tasarımı")
        fit_to_screen(self, 700, 650)
        self.project = project
        layout = QHBoxLayout(self)
        form = QFormLayout()
        scroll_panel(layout, form)
        self.toes = QComboBox()
        self.toes.addItems(string_names(project))
        form.addRow("Taban (toe) çizgi seti, kapalı", self.toes)
        form.addRow(
            QLabel(
                "Her kapalı çizginin kotu taban kotudur; farklı kotlarda birden çok taban "
                "verilebilir. Çizgileri Yüzey → Çizgi düzenleyici ile çizebilirsiniz."
            )
        )
        self.topo = QComboBox()
        self.topo.addItem("(düz zemin)")
        self.topo.addItems(surfaces.surface_names(project))
        if self.topo.findText("topo") >= 0:
            self.topo.setCurrentText("topo")
        self.ground = _spin(0.0, -1e5, 1e5, 10.0, 1)
        form.addRow("Topoğrafya / düz zemin kotu", _row(self.topo, self.ground))
        self.face = _spin(70.0, 20.0, 89.0, 1.0, 1)
        self.bench = _spin(10.0, 1.0, 50.0, 1.0, 1)
        self.berm = _spin(6.0, 0.0, 50.0, 0.5, 1)
        form.addRow("Şev açısı (°) / basamak (m)", _row(self.face, self.bench))
        form.addRow("Berm genişliği (m)", self.berm)
        self.sectors = QLineEdit()
        self.sectors.setPlaceholderText("örn. 0:65:8; 180:55:10 (azimut:şev:berm)")
        form.addRow("Geoteknik sektörler", self.sectors)
        self.ramp_on = QCheckBox("Rampa tasarla")
        self.ramp_on.setChecked(True)
        self.ramp_w = _spin(25.0, 3.0, 80.0, 1.0, 1)
        self.ramp_g = _spin(10.0, 1.0, 25.0, 0.5, 1)
        self.ramp_bund = _spin(0.0, 0.0, 20.0, 0.5, 1)
        self.ramp_exit = _spin(0.0, 0.0, 359.0, 15.0, 0)
        self.ramp_kind = QComboBox()
        self.ramp_kind.addItems(["Spiral", "Zikzak"])
        form.addRow(self.ramp_on)
        form.addRow("Rampa genişliği (m) / eğim %", _row(self.ramp_w, self.ramp_g))
        form.addRow("Emniyet seddi genişliği (m)", self.ramp_bund)
        form.addRow("Çıkış yönü (°) / tip", _row(self.ramp_exit, self.ramp_kind))
        self.res = _spin(2.0, 0.5, 20.0, 0.5, 1)
        form.addRow("Raster çözünürlüğü (m)", self.res)
        self.name = QLineEdit("tasarim_taban")
        form.addRow("Kayıt adı (yüzey ve çizgiler)", self.name)
        run = QPushButton("Tasarla ve kaydet")
        run.clicked.connect(self._guard(self.run))
        form.addRow(run)
        dxf = QPushButton("Tasarımı DXF olarak dışa aktar...")
        dxf.clicked.connect(self._guard(self.export))
        form.addRow(dxf)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        self.pit: DesignedPit | None = None

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Pit tasarımı", str(exc))

        return run

    def run(self) -> None:
        name = self.toes.currentText()
        if not name:
            raise RuntimeError("Önce taban çizgilerini çizin veya içe aktarın.")
        rings = [s.points for s in load_strings(self.project, name) if s.closed]
        if not rings:
            raise RuntimeError(f"'{name}' içinde kapalı çizgi yok.")
        topo = (
            None
            if self.topo.currentIndex() == 0
            else surfaces.load_surface(self.project, self.topo.currentText())
        )
        ramp = (
            Ramp(
                self.ramp_w.value(),
                self.ramp_g.value() / 100.0,
                self.ramp_exit.value(),
                switchback_span=120.0 if self.ramp_kind.currentIndex() == 1 else 0.0,
                bund=self.ramp_bund.value(),
            )
            if self.ramp_on.isChecked()
            else None
        )
        self.pit = pit = design_from_toes(
            rings,
            BenchGeometry(self.face.value(), self.bench.value(), self.berm.value()),
            topography=topo,
            ground_z=None if topo is not None else self.ground.value(),
            resolution=self.res.value(),
            ramp=ramp,
            sectors=parse_sectors(self.sectors.text(), self.bench.value()),
        )
        out = self.name.text().strip() or "tasarim_taban"
        surfaces.save_surface(self.project, out, pit.surface())
        lines = pit.strings(out.upper())
        save_strings(
            self.project, f"{out}_cizgiler", [String(p.points, p.closed, p.layer) for p in lines]
        )
        self.summary.setText(
            f"Kazı hacmi {pit.volume() / 1e6:,.2f} Mm³, {pit.bottoms} taban, {pit.pits} ocak"
            + (f", rampa {pit.ramp_length:,.0f} m" if pit.ramp_length else "")
            + (". Uyarı: tasarım raster sınırına değiyor." if pit.reaches_edge else ".")
            + f" Yüzey '{out}', çizgiler '{out}_cizgiler' olarak kaydedildi; rezerv için "
            "Açık Ocak → Tasarım rezervi."
        )

    def export(self) -> None:
        if self.pit is None:
            raise RuntimeError("Önce tasarlayın.")
        path, _ = QFileDialog.getSaveFileName(self, "DXF", "tasarim.dxf", "DXF (*.dxf)")
        if path:
            from pathlib import Path  # noqa: PLC0415

            write_dxf(Path(path), self.pit.strings())
