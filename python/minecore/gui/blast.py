"""Drill and blast design dialog: pattern, charge, powder factor, hole layout."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QWidget,
)

from minecore.drillblast import BlastDesign, annual, layout
from minecore.gui.layout import fit_to_screen, scroll_panel
from minecore.gui.resource import _row, _spin
from minecore.io.dxf import Polyline, write_dxf
from minecore.project import Project
from minecore.strings import String, load_strings, save_strings, string_names


class BlastDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:  # noqa: PLR0915 - one form
        super().__init__(parent)
        self.setWindowTitle("Delme-patlatma tasarımı")
        fit_to_screen(self, 900, 650)
        self.project = project
        layout_ = QHBoxLayout(self)
        form = QFormLayout()
        scroll_panel(layout_, form)
        self.diameter = _spin(165.0, 25.0, 450.0, 5.0, 0)
        self.bench_h = _spin(10.0, 1.0, 30.0, 0.5, 1)
        self.kb = _spin(30.0, 10.0, 60.0, 1.0, 1)
        self.sb = _spin(1.15, 0.8, 2.0, 0.05, 2)
        self.jb = _spin(0.3, 0.0, 1.0, 0.05, 2)
        self.tb = _spin(0.7, 0.2, 2.0, 0.05, 2)
        self.burden = _spin(0.0, 0.0, 20.0, 0.1, 2)
        self.burden.setSpecialValueText("otomatik")
        self.spacing = _spin(0.0, 0.0, 25.0, 0.1, 2)
        self.spacing.setSpecialValueText("otomatik")
        self.rho_e = _spin(0.85, 0.5, 1.6, 0.05, 2)
        self.rho_r = _spin(2.7, 1.0, 5.0, 0.05, 2)
        self.angle = _spin(0.0, 0.0, 30.0, 1.0, 0)
        self.c_exp = _spin(1.0, 0.0, 100.0, 0.1, 2)
        self.c_drill = _spin(25.0, 0.0, 1000.0, 1.0, 1)
        self.c_acc = _spin(20.0, 0.0, 1000.0, 1.0, 1)
        self.tonnage = _spin(10.0, 0.0, 1000.0, 1.0, 2)
        form.addRow("Delik çapı (mm) / basamak yüksekliği (m)", _row(self.diameter, self.bench_h))
        form.addRow("Dilim/çap oranı / aralık/dilim", _row(self.kb, self.sb))
        form.addRow("Taban delgisi / sıkılama (x dilim)", _row(self.jb, self.tb))
        form.addRow("Dilim / aralık (m, elle)", _row(self.burden, self.spacing))
        form.addRow("Patlayıcı / kaya yoğunluğu (g/cm³, t/m³)", _row(self.rho_e, self.rho_r))
        form.addRow("Delik eğimi (° düşeyden)", self.angle)
        form.addRow(
            "Patlayıcı $/kg / delme $/m / kapsül $/delik",
            _row(self.c_exp, self.c_drill, self.c_acc),
        )
        form.addRow("Yıllık patlatılacak malzeme (Mt)", self.tonnage)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)
        form.addRow(QLabel("<b>Delik planı</b> (kapalı bir çizgi içinde)"))
        self.outline = QComboBox()
        self.outline.addItems(string_names(project))
        self.azimuth = _spin(0.0, 0.0, 359.0, 5.0, 0)
        self.staggered = QCheckBox("Şaşırtmalı (zigzag) düzen")
        self.staggered.setChecked(True)
        form.addRow("Patlatma sınırı (çizgi seti)", self.outline)
        form.addRow("Sıra yönü (° kuzeyden)", self.azimuth)
        form.addRow(self.staggered)
        make = QPushButton("Delik planı oluştur, kaydet ve DXF yaz...")
        make.clicked.connect(self._guard(self.make_layout))
        form.addRow(make)
        self.layout_note = QLabel()
        self.layout_note.setWordWrap(True)
        form.addRow(self.layout_note)
        for w in (
            self.diameter, self.bench_h, self.kb, self.sb, self.jb, self.tb, self.burden,
            self.spacing, self.rho_e, self.rho_r, self.angle, self.c_exp, self.c_drill,
            self.c_acc, self.tonnage,
        ):  # fmt: skip
            w.valueChanged.connect(self.update_result)
        self.holes: tuple[np.ndarray, np.ndarray] | None = None
        self.update_result()

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Delme-patlatma", str(exc))

        return run

    def design(self) -> BlastDesign:
        return BlastDesign(
            diameter_mm=self.diameter.value(),
            bench_height=self.bench_h.value(),
            burden_factor=self.kb.value(),
            spacing_ratio=self.sb.value(),
            subdrill_ratio=self.jb.value(),
            stemming_ratio=self.tb.value(),
            explosive_density=self.rho_e.value(),
            rock_density=self.rho_r.value(),
            hole_angle=self.angle.value(),
            burden=self.burden.value() or None,
            spacing=self.spacing.value() or None,
            explosive_cost_kg=self.c_exp.value(),
            drilling_cost_m=self.c_drill.value(),
            accessory_cost_hole=self.c_acc.value(),
        )

    def update_result(self) -> None:
        try:
            d = self.design()
        except Exception as exc:
            self.summary.setText(str(exc))
            return
        year = annual(d, self.tonnage.value() * 1e6)
        self.summary.setText(
            f"Dilim {d.b:.2f} m, aralık {d.s:.2f} m, taban delgisi {d.subdrill:.2f} m, "
            f"sıkılama {d.stemming:.2f} m.\n"
            f"Delik boyu {d.hole_length:.2f} m, şarj boyu {d.charge_length:.2f} m, "
            f"{d.linear_charge:.1f} kg/m, delik başına {d.charge_kg:,.0f} kg.\n"
            f"Delik başına {d.volume_per_hole:,.0f} m³ ({d.tonnes_per_hole:,.0f} t). "
            f"Özgül şarj {d.powder_factor:.3f} kg/m³ "
            f"({d.powder_factor / d.rock_density:.3f} kg/t).\n"
            f"Maliyet {d.cost_per_hole:,.0f} $/delik, {d.cost_per_t:.3f} $/t.\n"
            f"Yılda {year.holes:,} delik, {year.drill_m:,.0f} m delme, "
            f"{year.explosive_t:,.0f} t patlayıcı, {year.cost / 1e6:,.2f} M$."
        )

    def make_layout(self) -> None:
        name = self.outline.currentText()
        if not name:
            raise RuntimeError("Önce patlatma sınırını çizgi olarak içe aktarın (Yüzey menüsü).")
        rings = [s for s in load_strings(self.project, name) if s.closed]
        if not rings:
            raise RuntimeError(f"'{name}' içinde kapalı çizgi yok.")
        d = self.design()
        collars_all, toes_all = [], []
        for ring in rings:
            c, t = layout(
                d, ring.points, azimuth=self.azimuth.value(), staggered=self.staggered.isChecked()
            )
            collars_all.append(c)
            toes_all.append(t)
        collars, toes = np.concatenate(collars_all), np.concatenate(toes_all)
        self.holes = (collars, toes)
        holes = [
            String(np.vstack([c, t]), False, "DELIK") for c, t in zip(collars, toes, strict=True)
        ]
        save_strings(self.project, f"{name}_delikler", holes)
        path = self.ask_path()
        if path:
            write_dxf(Path(path), [Polyline("BLAST_HOLES", s.points, False) for s in holes])
        volume = len(collars) * d.volume_per_hole
        self.layout_note.setText(
            f"{len(collars)} delik, {len(collars) * d.hole_length:,.0f} m delme, "
            f"{len(collars) * d.charge_kg / 1000:,.1f} t patlayıcı, yaklaşık {volume:,.0f} m³. "
            f"Çizgi seti '{name}_delikler' olarak kaydedildi." + (f" DXF: {path}" if path else "")
        )

    def ask_path(self) -> str:
        """DXF target (tests replace it)."""
        path, _ = QFileDialog.getSaveFileName(self, "DXF kaydet", "delikler.dxf", "DXF (*.dxf)")
        return path
