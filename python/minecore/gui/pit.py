"""Open-pit optimisation dialog: economics, slopes, nested shells, pushbacks, schedule."""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from minecore.blockmodel import BlockModel
from minecore.gui.resource import _row, _spin, _with_progress, block_models
from minecore.pitopt import (
    Economics,
    PitResult,
    PriceUnit,
    Schedule,
    SlopeProfile,
    optimise_pit,
    pushbacks,
    schedule,
)
from minecore.pitopt.optimise import store
from minecore.project import Project

PRICE_TR = {
    PriceUnit.TROY_OUNCE: "$/ons (troy)",
    PriceUnit.GRAM: "$/g",
    PriceUnit.POUND: "$/lb",
    PriceUnit.TONNE: "$/t metal",
}


def parse_sectors(text: str) -> tuple[tuple[float, float], ...]:
    """``"0:50; 90:38; 180:50"`` -> ((0, 50), (90, 38), (180, 50))."""
    out = []
    for part in text.replace(",", ";").split(";"):
        if part.strip():
            az, angle = part.split(":")
            out.append((float(az), float(angle)))
    return tuple(out)


def _cell(table: QTableWidget, r: int, c: int, text: str) -> None:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    table.setItem(r, c, item)


def _m(v: float) -> str:
    return "" if not math.isfinite(v) else f"{v / 1e6:,.2f}"


class PitDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:  # noqa: PLR0915 - one form
        super().__init__(parent)
        self.setWindowTitle("Açık ocak optimizasyonu")
        self.resize(1400, 860)
        self.project = project
        self.pit: PitResult | None = None
        self.phase: Any = None
        self.sched: Schedule | None = None

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        layout.addLayout(left, 0)
        model_box = QGroupBox("Model")
        form = QFormLayout(model_box)
        self.model = QComboBox()
        self.model.addItems(block_models(project))
        self.grade = QComboBox()
        self.density_attr = QComboBox()
        self.density = _spin(2.7, 0.1, 25.0, 0.05, 3)
        form.addRow("Blok modeli", self.model)
        form.addRow("Tenör özniteliği", self.grade)
        form.addRow("Yoğunluk özniteliği", self.density_attr)
        form.addRow("Sabit yoğunluk (t/m³)", self.density)
        self.rock = QComboBox()
        form.addRow("Kaya/hava özniteliği (topoğrafya)", self.rock)
        left.addWidget(model_box)

        econ_box = QGroupBox("Ekonomi")
        ef = QFormLayout(econ_box)
        self.price = _spin(2000.0, 0.0001, 1e9, 10)
        self.price_unit = QComboBox()
        for u in PriceUnit:
            self.price_unit.addItem(PRICE_TR[u], u.value)
        self.recovery = _spin(90.0, 1.0, 100.0, 1, 1)
        self.selling = _spin(0.0, 0.0, 1e9, 1)
        self.mining = _spin(2.5, 0.0, 1e4, 0.1)
        self.bench_cost = _spin(0.0, 0.0, 100.0, 0.01, 3)
        self.processing = _spin(20.0, 0.0, 1e5, 0.5)
        ef.addRow("Metal fiyatı", _row(self.price, self.price_unit))
        ef.addRow("Satış maliyeti (fiyat biriminde)", self.selling)
        ef.addRow("Kazanım %", self.recovery)
        ef.addRow("Madencilik maliyeti $/t", self.mining)
        ef.addRow("Basamak başına ek maliyet $/t", self.bench_cost)
        ef.addRow("Zenginleştirme maliyeti $/t cevher", self.processing)
        left.addWidget(econ_box)

        geo = QGroupBox("Şev ve kabuklar")
        gf = QFormLayout(geo)
        self.slope = _spin(45.0, 10.0, 89.0, 1, 1)
        self.sectors = QLineEdit()
        self.sectors.setPlaceholderText("örn. 0:50; 90:38; 180:50; 270:42 (azimut:açı)")
        self.levels = QSpinBox(minimum=0, maximum=12, value=0)
        self.levels.setSpecialValueText("otomatik")
        self.rf0 = _spin(0.3, 0.01, 5.0, 0.05)
        self.rf1 = _spin(1.2, 0.01, 5.0, 0.05)
        self.rfs = _spin(0.05, 0.01, 1.0, 0.01)
        self.rate = _spin(10.0, 0.001, 1e4, 1, 3)
        self.discount = _spin(8.0, 0.0, 50.0, 0.5, 1)
        gf.addRow("Genel şev açısı °", self.slope)
        gf.addRow("Yöne göre şev (isteğe bağlı)", self.sectors)
        gf.addRow("Şev deseni basamak sayısı", self.levels)
        gf.addRow("Gelir çarpanı başl./bitiş/adım", _row(self.rf0, self.rf1, self.rfs))
        gf.addRow("Toplam kazı hızı (Mt/yıl)", self.rate)
        gf.addRow("İskonto oranı %", self.discount)
        run = QPushButton("Kabukları hesapla")
        run.clicked.connect(self._guard(self.run))
        gf.addRow(run)
        left.addWidget(geo)

        pb = QGroupBox("Pushback ve çizelge")
        pf = QFormLayout(pb)
        self.final = QComboBox()
        self.min_ore = _spin(5.0, 0.001, 1e5, 1, 3)
        make = QPushButton("Pushback ve çizelge oluştur")
        make.clicked.connect(self._guard(self.make_phases))
        save = QPushButton("Sonuçları blok modeline kaydet")
        save.clicked.connect(self._guard(self.save))
        export = QPushButton("Raporları CSV olarak dışa aktar...")
        export.clicked.connect(self._guard(self.export))
        pf.addRow("Nihai ocak kabuğu", self.final)
        pf.addRow("Pushback başına en az cevher (Mt)", self.min_ore)
        pf.addRow(make)
        pf.addRow(save)
        pf.addRow(export)
        left.addWidget(pb)
        left.addStretch(1)

        right = QTabWidget()
        layout.addWidget(right, 1)
        graph_page = QWidget()
        gl = QVBoxLayout(graph_page)
        self.figure = Figure(figsize=(7, 4))
        self.canvas = FigureCanvasQTAgg(self.figure)  # type: ignore[no-untyped-call]
        gl.addWidget(self.canvas, 2)
        self.shells = QTableWidget(0, 11)
        self.shells.setHorizontalHeaderLabels(
            [
                "Kabuk",
                "Gelir ç.",
                "Cevher Mt",
                "Pasa Mt",
                "Dekapaj",
                "Tenör",
                "Metal",
                "Nakit M$",
                "En iyi NPV M$",
                "En kötü NPV M$",
                "Ömür (yıl)",
            ]
        )
        gl.addWidget(self.shells, 1)
        right.addTab(graph_page, "Kabuk raporu")
        self.sched_table = QTableWidget(0, 7)
        self.sched_table.setHorizontalHeaderLabels(
            ["Yıl", "Cevher Mt", "Pasa Mt", "Tenör", "Metal", "Nakit M$", "İskontolu M$"]
        )
        right.addTab(self.sched_table, "Üretim çizelgesi")
        self.tabs = right
        self.note = QLabel(
            "Kabuklar Lerchs-Grossmann ile aynı sonucu veren en büyük kapanış (max-flow) ile "
            "bulunur. Çizelge ilk geçiş içindir: fazlar arası basamak gecikmesi, tesis kapasitesi "
            "ve stok sahası henüz yok."
        )
        self.note.setWordWrap(True)
        left.addWidget(self.note)
        self.model.currentTextChanged.connect(self._fill)
        self._fill(self.model.currentText())

    def inform(self, title: str, text: str) -> None:
        """Modal note to the user (tests replace it)."""
        QMessageBox.information(self, title, text)

    def _guard(self, slot: Any) -> Any:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "MineCore", str(exc))

        return run

    def _fill(self, name: str) -> None:
        self.grade.clear()
        self.density_attr.clear()
        self.density_attr.addItem("(sabit)")
        self.rock.clear()
        self.rock.addItem("(yok: model üstü yüzeydir)")
        if not name:
            return
        attrs = BlockModel.open(self.project, name).attributes
        grades = [
            a for a in attrs if not a.endswith(("_n", "_dist", "_kv")) and not a.startswith("pit_")
        ]
        self.grade.addItems([g for g in grades if g != "kaya"])
        self.density_attr.addItems(grades)
        self.rock.addItems(grades)
        if "kaya" in grades:
            self.rock.setCurrentText("kaya")
        unit = attrs.get(grades[0], {}).get("unit") if grades else None
        if unit == "%":
            self.price_unit.setCurrentIndex(list(PriceUnit).index(PriceUnit.TONNE))
            self.price.setValue(9000.0)
        elif unit in ("g/t", "ppm"):
            self.price_unit.setCurrentIndex(list(PriceUnit).index(PriceUnit.TROY_OUNCE))
            self.price.setValue(2000.0)

    def economics(self) -> Economics:
        d = self.density_attr.currentText()
        return Economics(
            price=self.price.value(),
            price_unit=PriceUnit(self.price_unit.currentData()),
            recovery=self.recovery.value() / 100.0,
            mining_cost=self.mining.value(),
            processing_cost=self.processing.value(),
            selling_cost=self.selling.value(),
            mining_cost_per_bench=self.bench_cost.value(),
            density=self.density.value() if d == "(sabit)" else d,
        )

    def slope_profile(self) -> SlopeProfile:
        return SlopeProfile(self.slope.value(), parse_sectors(self.sectors.text()))

    def run(self) -> None:
        model = BlockModel.open(self.project, self.model.currentText())
        factors = np.arange(
            self.rf0.value(), self.rf1.value() + self.rfs.value() / 2, self.rfs.value()
        )
        econ, slope = self.economics(), self.slope_profile()
        levels = self.levels.value() or None
        self.pit = _with_progress(
            self,
            "Ocak kabukları hesaplanıyor...",
            lambda progress: optimise_pit(
                model,
                self.grade.currentText(),
                econ,
                slope,
                [float(f) for f in factors],
                mining_rate=self.rate.value() * 1e6,
                discount_rate=self.discount.value() / 100.0,
                max_levels=levels,
                rock_attribute=None if self.rock.currentIndex() == 0 else self.rock.currentText(),
                progress=progress,
            ),
        )
        self.phase, self.sched = None, None
        self._show_shells()

    def _show_shells(self) -> None:
        r = self.pit
        assert r is not None
        self.shells.setRowCount(len(r.rows))
        for i, row in enumerate(r.rows):
            cells = [
                str(row.shell),
                f"{row.revenue_factor:.2f}",
                _m(row.ore_t),
                _m(row.waste_t),
                "" if not math.isfinite(row.strip_ratio) else f"{row.strip_ratio:.2f}",
                "" if math.isnan(row.grade) else f"{row.grade:.3f}",
                f"{row.metal:,.0f}",
                _m(row.cash),
                _m(row.best_npv),
                _m(row.worst_npv),
                f"{row.years:.1f}",
            ]
            for j, text in enumerate(cells):
                _cell(self.shells, i, j, text)
        self.shells.resizeColumnsToContents()
        self.final.clear()
        best = max(r.rows, key=lambda x: x.best_npv)
        for row in r.rows:
            self.final.addItem(f"{row.shell} (gelir ç. {row.revenue_factor:.2f})", row.shell)
        self.final.setCurrentIndex(best.shell - 1)
        self.plot()

    def plot(self) -> None:
        r = self.pit
        self.figure.clear()
        if r is None:
            return
        ax = self.figure.add_subplot(111)
        x = np.array([row.shell for row in r.rows])
        ore = np.array([row.ore_t for row in r.rows]) / 1e6
        waste = np.array([row.waste_t for row in r.rows]) / 1e6
        ax.bar(x, ore, color="#ef6c00", label="Cevher")
        ax.bar(x, waste, bottom=ore, color="#9e9e9e", label="Pasa")
        ax.set_xlabel("Kabuk")
        ax.set_ylabel("Tonaj (Mt)")
        ax2 = ax.twinx()
        ax2.plot(
            x, [row.best_npv / 1e6 for row in r.rows], color="#1565c0", marker="o", label="En iyi"
        )
        ax2.plot(
            x, [row.worst_npv / 1e6 for row in r.rows], color="#c62828", marker="s", label="En kötü"
        )
        ax2.set_ylabel("İskontolu nakit akışı (M$)")
        ax.legend(loc="upper left")
        ax2.legend(loc="upper center")
        ax.set_title("Kabuk kabuk analiz (pit by pit)")
        ax.grid(alpha=0.3)
        self.figure.tight_layout()
        self.canvas.draw_idle()  # type: ignore[no-untyped-call]

    def make_phases(self) -> None:
        if self.pit is None:
            raise RuntimeError("Önce kabukları hesaplayın.")
        final = int(self.final.currentData())
        self.phase = pushbacks(self.pit, final, self.min_ore.value() * 1e6)
        self.sched = schedule(
            self.pit, self.phase, self.rate.value() * 1e6, self.discount.value() / 100.0
        )
        rows = self.sched.rows
        self.sched_table.setRowCount(len(rows) + 1)
        for i, p in enumerate(rows):
            cells = [
                str(p.period),
                _m(p.ore_t),
                _m(p.waste_t),
                "" if math.isnan(p.grade) else f"{p.grade:.3f}",
                f"{p.metal:,.0f}",
                _m(p.cash),
                _m(p.discounted),
            ]
            for j, text in enumerate(cells):
                _cell(self.sched_table, i, j, text)
        total = len(rows)
        _cell(self.sched_table, total, 0, "Toplam")
        _cell(self.sched_table, total, 1, _m(sum(p.ore_t for p in rows)))
        _cell(self.sched_table, total, 2, _m(sum(p.waste_t for p in rows)))
        _cell(self.sched_table, total, 5, _m(sum(p.cash for p in rows)))
        _cell(self.sched_table, total, 6, _m(self.sched.npv))
        self.sched_table.resizeColumnsToContents()
        self.tabs.setCurrentIndex(1)
        self.inform(
            "Pushback",
            f"{int(self.phase.max())} pushback, {len(rows)} yıllık çizelge. "
            f"NPV {self.sched.npv / 1e6:,.1f} M$.",
        )

    def save(self) -> None:
        if self.pit is None:
            raise RuntimeError("Önce kabukları hesaplayın.")
        model = BlockModel.open(self.project, self.model.currentText())
        store(model, self.pit, self.economics(), self.slope_profile(), self.phase, self.sched)
        self.inform(
            "Kaydedildi",
            "pit_value, pit_shell"
            + (", pit_phase, pit_period" if self.sched else "")
            + " blok modeline yazıldı. 3B görünümde 'Blok' listesinden seçebilirsiniz.",
        )

    def export(self) -> None:
        if self.pit is None:
            raise RuntimeError("Önce kabukları hesaplayın.")
        path, _ = QFileDialog.getSaveFileName(self, "CSV kaydet", "kabuk_raporu.csv", "CSV (*.csv)")
        if path:
            write_pit_reports(Path(path), self.pit, self.sched)


def write_pit_reports(path: Path, result: PitResult, sched: Schedule | None) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(
            [
                "shell",
                "revenue_factor",
                "ore_t",
                "waste_t",
                "strip_ratio",
                "grade",
                "metal",
                "cash",
                "best_npv",
                "worst_npv",
                "years",
            ]
        )
        for r in result.rows:
            w.writerow(
                [
                    r.shell,
                    f"{r.revenue_factor:.4g}",
                    f"{r.ore_t:.0f}",
                    f"{r.waste_t:.0f}",
                    f"{r.strip_ratio:.4g}",
                    f"{r.grade:.5g}",
                    f"{r.metal:.3f}",
                    f"{r.cash:.0f}",
                    f"{r.best_npv:.0f}",
                    f"{r.worst_npv:.0f}",
                    f"{r.years:.3f}",
                ]
            )
        if sched is not None:
            w.writerow([])
            w.writerow(["period", "ore_t", "waste_t", "grade", "metal", "cash", "discounted"])
            for p in sched.rows:
                w.writerow(
                    [
                        p.period,
                        f"{p.ore_t:.0f}",
                        f"{p.waste_t:.0f}",
                        f"{p.grade:.5g}",
                        f"{p.metal:.3f}",
                        f"{p.cash:.0f}",
                        f"{p.discounted:.0f}",
                    ]
                )
