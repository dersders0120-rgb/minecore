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
    QCheckBox,
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

from minecore import surfaces
from minecore.blockmodel import BlockModel
from minecore.gui.resource import _row, _spin, _with_progress, block_models
from minecore.pitopt import (
    Economics,
    PitResult,
    PriceUnit,
    SlopeProfile,
    optimise_pit,
    pushbacks,
)
from minecore.pitopt.benchdesign import (
    BenchGeometry,
    DesignReport,
    Ramp,
    design_from_model,
)
from minecore.pitopt.benchdesign import (
    parse_sectors as parse_bench_sectors,
)
from minecore.pitopt.cutoff import LaneResult, cutoffs_from_model
from minecore.pitopt.design import edge_blocks
from minecore.pitopt.economics import PitOptError, parse_routes
from minecore.pitopt.optimise import route_tonnes, store
from minecore.pitopt.scheduling import (
    ConstrainedSchedule,
    InventoryRow,
    Limits,
    bench_inventory,
    schedule_constrained,
)
from minecore.pitopt.width import apply_mining_width
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


def parse_capacities(text: str) -> tuple[float, ...]:
    """``"1.5; 0.8"`` Mt/year -> (1.5e6, 0.8e6) t/year."""
    try:
        return tuple(float(v) * 1e6 for v in text.replace(",", ";").split(";") if v.strip())
    except ValueError:
        raise PitOptError(f"route capacities {text!r} are not numbers") from None


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
        self.sched: ConstrainedSchedule | None = None

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
        self.mine_recovery = _spin(100.0, 1.0, 100.0, 1, 1)
        self.dilution = _spin(0.0, 0.0, 100.0, 1, 1)
        self.nsr = QCheckBox("Öznitelik NSR değeridir ($/t; fiyat ve kazanım kullanılmaz)")
        self.nsr.toggled.connect(self._sync_nsr)
        ef.addRow(self.nsr)
        ef.addRow("Metal fiyatı", _row(self.price, self.price_unit))
        ef.addRow("Satış maliyeti (fiyat biriminde)", self.selling)
        ef.addRow("Kazanım %", self.recovery)
        ef.addRow("Madencilik maliyeti $/t", self.mining)
        ef.addRow("Basamak başına ek maliyet $/t", self.bench_cost)
        ef.addRow("Zenginleştirme maliyeti $/t cevher", self.processing)
        ef.addRow("Madencilik kazanımı % (cevher kaybı)", self.mine_recovery)
        ef.addRow("Seyrelme % (sıfır tenörlü pasa)", self.dilution)
        self.routes = QLineEdit()
        self.routes.setPlaceholderText("örn. liç:60:4; yığın liçi:40:2 (ad:kazanım %:maliyet $/t)")
        self.routes.setToolTip(
            "Ek işleme yolları. Yukarıdaki kazanım ve zenginleştirme maliyeti ana yoldur; "
            "her blok en yüksek marjı veren yola gider (pit_route özniteliği)."
        )
        ef.addRow("Ek işleme yolları", self.routes)
        self.route_label = QLabel()
        self.route_label.setWordWrap(True)
        ef.addRow(self.route_label)
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
        self.min_width = _spin(0.0, 0.0, 1000.0, 5.0, 1)
        self.min_width.setToolTip("Her basamaktaki pushback dilimi için en az genişlik; 0 = yok")
        pf.addRow("Minimum kazı genişliği (m)", self.min_width)
        self.plant = _spin(2.0, 0.001, 1e4, 0.5, 3)
        self.lag = QSpinBox(minimum=0, maximum=50, value=2)
        self.sinking = QSpinBox(minimum=1, maximum=50, value=6)
        pf.addRow("Tesis kapasitesi (Mt cevher/yıl)", self.plant)
        self.route_caps = QLineEdit()
        self.route_caps.setPlaceholderText("örn. 1.5 (ek yolların sırasıyla, ; ile; boş: sınırsız)")
        pf.addRow("Ek yol kapasiteleri (Mt/yıl)", self.route_caps)
        pf.addRow("Pushback'ler arası basamak gecikmesi", self.lag)
        pf.addRow("Batma hızı (basamak/yıl)", self.sinking)
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
        self.sched_table = QTableWidget(0, 9)
        self.sched_table.setHorizontalHeaderLabels(
            [
                "Yıl",
                "Kazı Mt",
                "Cevher kazı Mt",
                "Tesise Mt",
                "Besleme tenörü",
                "Metal",
                "Stok Mt",
                "Nakit M$",
                "İskontolu M$",
            ]
        )
        right.addTab(self.sched_table, "Üretim çizelgesi")
        self.inv_table = QTableWidget(0, len(INVENTORY_HEADERS))
        self.inv_table.setHorizontalHeaderLabels(INVENTORY_HEADERS)
        right.addTab(self.inv_table, "Pushback-basamak envanteri")
        self.inventory: list[InventoryRow] = []
        self.tabs = right
        self.note = QLabel(
            "Kabuklar Lerchs-Grossmann ile aynı sonucu veren en büyük kapanış (max-flow) ile "
            "bulunur. Çizelge kurallı (greedy) bir sıralamadır: kazı ve tesis kapasitesi, "
            "basamak gecikmesi, batma hızı ve stok sahasına uyar; MILP optimizasyonu değildir."
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

    def _sync_nsr(self, on: bool) -> None:
        for w in (self.price, self.price_unit, self.recovery, self.selling, self.routes):
            w.setEnabled(not on)

    def economics(self) -> Economics:
        d = self.density_attr.currentText()
        return Economics(
            price=self.price.value(),
            price_unit=PriceUnit(self.price_unit.currentData()),
            recovery=self.recovery.value() / 100.0,
            mining_cost=self.mining.value(),
            processing_cost=self.processing.value(),
            selling_cost=self.selling.value(),
            nsr=self.nsr.isChecked(),
            mining_cost_per_bench=self.bench_cost.value(),
            density=self.density.value() if d == "(sabit)" else d,
            mining_recovery=self.mine_recovery.value() / 100.0,
            dilution=self.dilution.value() / 100.0,
            routes=() if self.nsr.isChecked() else parse_routes(self.routes.text()),
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
        self.route_label.setText(
            "Nihai kabukta yollar: "
            + "; ".join(
                f"{'ana' if name == 'main' else name} {feed_t / 1e6:,.2f} Mt"
                for name, feed_t, _ in route_tonnes(self.pit, econ, len(self.pit.rows))
            )
            if econ.routes
            else ""
        )
        edge = edge_blocks(model.grid, self.pit.shell > 0)
        if edge:
            self.inform(
                "Ocak",
                f"Uyarı: en büyük kabuk {edge:,} blokla modelin yan sınırına değiyor. "
                "Model dışındaki şev kısıtları görülemez; kabuk kesik olabilir. "
                "Blok modelini daha geniş (dolgu payı ile) oluşturun.",
            )

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
        width_note = ""
        if self.min_width.value() > 0:
            grid = BlockModel.open(self.project, self.model.currentText()).grid
            fixed = apply_mining_width(
                grid, self.phase, self.slope_profile(), self.min_width.value()
            )
            self.phase = fixed.phase
            width_note = f"\nMinimum genişlik: {fixed.moved:,} blok başka pushback'e alındı" + (
                f"; {fixed.remaining_slivers:,} dar blok kaldı." if fixed.remaining_slivers else "."
            )
        limits = Limits(
            mining_capacity=self.rate.value() * 1e6,
            plant_capacity=self.plant.value() * 1e6,
            bench_lag=self.lag.value(),
            sinking_rate=self.sinking.value(),
            discount_rate=self.discount.value() / 100.0,
            route_capacities=parse_capacities(self.route_caps.text()),
        )
        self.sched = schedule_constrained(self.pit, self.phase, limits)
        rows = self.sched.rows
        self.sched_table.setRowCount(len(rows) + 1)
        for i, p in enumerate(rows):
            cells = [
                str(p.period),
                _m(p.mined_t),
                _m(p.ore_mined_t),
                _m(p.ore_fed_t)
                + (
                    " (" + " + ".join(_m(f) for f in p.fed_by_route) + ")"
                    if len(p.fed_by_route) > 1
                    else ""
                ),
                "" if math.isnan(p.feed_grade) else f"{p.feed_grade:.3f}",
                f"{p.metal:,.0f}",
                _m(p.stockpile_t),
                _m(p.cash),
                _m(p.discounted),
            ]
            for j, text in enumerate(cells):
                _cell(self.sched_table, i, j, text)
        total = len(rows)
        _cell(self.sched_table, total, 0, "Toplam")
        _cell(self.sched_table, total, 1, _m(sum(p.mined_t for p in rows)))
        _cell(self.sched_table, total, 2, _m(sum(p.ore_mined_t for p in rows)))
        _cell(self.sched_table, total, 3, _m(sum(p.ore_fed_t for p in rows)))
        _cell(self.sched_table, total, 5, f"{sum(p.metal for p in rows):,.0f}")
        _cell(self.sched_table, total, 7, _m(sum(p.cash for p in rows)))
        _cell(self.sched_table, total, 8, _m(self.sched.npv))
        self.sched_table.resizeColumnsToContents()
        grid = BlockModel.open(self.project, self.model.currentText()).grid
        self.inventory = bench_inventory(
            self.pit, self.phase, grid.origin[2], grid.size[2], grid.count[2]
        )
        self.inv_table.setRowCount(len(self.inventory))
        for i, row in enumerate(self.inventory):
            for j, value in enumerate(_inventory_cells(row)):
                text = "" if value is None else value if isinstance(value, str) else f"{value:,.4g}"
                _cell(self.inv_table, i, j, text)
        self.inv_table.resizeColumnsToContents()
        self.tabs.setCurrentIndex(1)
        self.inform(
            "Pushback",
            f"{int(self.phase.max())} pushback, {len(rows)} yıllık çizelge. "
            f"NPV {self.sched.npv / 1e6:,.1f} M$."
            + width_note
            + (
                f"\nUyarı: {self.sched.unmined_t / 1e6:,.2f} Mt {limits.max_periods} yılda "
                "kazılamadı."
                if self.sched.unmined_t > 1
                else ""
            ),
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
        path, _ = QFileDialog.getSaveFileName(
            self, "Raporları kaydet", "ocak_raporu.xlsx", "Excel (*.xlsx);;CSV (*.csv)"
        )
        if not path:
            return
        if path.lower().endswith(".csv"):
            write_pit_reports(Path(path), self.pit, self.sched)
        else:
            target = Path(path if path.lower().endswith(".xlsx") else path + ".xlsx")
            write_pit_reports_xlsx(target, self.pit, self.sched, self.inventory)


INVENTORY_HEADERS = [
    "Pushback",
    "Basamak tabanı (m)",
    "Cevher t",
    "Tenör",
    "Metal",
    "Pasa t",
    "Dekapaj",
    "Değer",
]


def _inventory_cells(r: InventoryRow) -> list[Any]:
    strip = r.waste_t / r.ore_t if r.ore_t > 0 else None
    grade = None if math.isnan(r.grade) else r.grade
    return [r.phase, r.bench_toe, r.ore_t, grade, r.metal, r.waste_t, strip, r.value]


def write_pit_reports_xlsx(
    path: Path,
    result: PitResult,
    sched: ConstrainedSchedule | None,
    inventory: list[InventoryRow] | None = None,
) -> None:
    from minecore.io.excel import write_workbook  # noqa: PLC0415

    sheets: dict[str, Any] = {
        "Kabuklar": (
            [
                "Kabuk",
                "Gelir çarpanı",
                "Cevher t",
                "Pasa t",
                "Dekapaj",
                "Tenör",
                "Metal",
                "Nakit",
                "En iyi NPV",
                "En kötü NPV",
                "Ömür (yıl)",
            ],
            [
                [
                    r.shell,
                    r.revenue_factor,
                    r.ore_t,
                    r.waste_t,
                    r.strip_ratio if math.isfinite(r.strip_ratio) else None,
                    r.grade,
                    r.metal,
                    r.cash,
                    r.best_npv,
                    r.worst_npv,
                    r.years,
                ]
                for r in result.rows
            ],
        )
    }
    if sched is not None:
        sheets["Çizelge"] = (
            [
                "Yıl",
                "Kazı t",
                "Cevher kazı t",
                "Tesise t",
                "Besleme tenörü",
                "Metal",
                "Stok t",
                "Nakit",
                "İskontolu nakit",
            ],
            [
                [
                    p.period,
                    p.mined_t,
                    p.ore_mined_t,
                    p.ore_fed_t,
                    p.feed_grade,
                    p.metal,
                    p.stockpile_t,
                    p.cash,
                    p.discounted,
                ]
                for p in sched.rows
            ],
        )
    if inventory:
        sheets["Envanter"] = (INVENTORY_HEADERS, [_inventory_cells(r) for r in inventory])
    write_workbook(path, sheets)


def write_pit_reports(path: Path, result: PitResult, sched: ConstrainedSchedule | None) -> None:
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
            w.writerow(
                [
                    "period",
                    "mined_t",
                    "ore_mined_t",
                    "ore_fed_t",
                    "feed_grade",
                    "metal",
                    "stockpile_t",
                    "cash",
                    "discounted",
                ]
            )
            for p in sched.rows:
                w.writerow(
                    [
                        p.period,
                        f"{p.mined_t:.0f}",
                        f"{p.ore_mined_t:.0f}",
                        f"{p.ore_fed_t:.0f}",
                        f"{p.feed_grade:.5g}",
                        f"{p.metal:.3f}",
                        f"{p.stockpile_t:.0f}",
                        f"{p.cash:.0f}",
                        f"{p.discounted:.0f}",
                    ]
                )


class BenchDesignDialog(QDialog):
    """Bench design (face angle, bench height, berm) around the optimised pit."""

    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Basamak tasarımı")
        self.project = project
        self.report: DesignReport | None = None
        form = QFormLayout(self)
        self.model = QComboBox()
        self.model.addItems(
            [
                m
                for m in block_models(project)
                if "pit_shell" in BlockModel.open(project, m).attributes
            ]
        )
        self.shell = QSpinBox(minimum=1, maximum=1000, value=1)
        self.face = _spin(65.0, 10.0, 89.0, 1.0, 1)
        self.bench_h = _spin(10.0, 0.5, 100.0, 1.0, 1)
        self.berm = _spin(6.0, 0.0, 100.0, 0.5, 1)
        self.topography = QComboBox()
        self.topography.addItem("(blok modelindeki kaya üstü)")
        self.topography.addItems(surfaces.surface_names(project))
        self.overall = QLabel()
        for w in (self.face, self.bench_h, self.berm):
            w.valueChanged.connect(self._overall)
        form.addRow("Blok modeli", self.model)
        form.addRow("Son kabuk (nihai ocak)", self.shell)
        form.addRow("Basamak şev açısı °", self.face)
        form.addRow("Basamak yüksekliği (m)", self.bench_h)
        form.addRow("Berm genişliği (m)", self.berm)
        form.addRow("Arazi yüzeyi", self.topography)
        form.addRow("Genel şev açısı", self.overall)
        self.sectors = QLineEdit()
        self.sectors.setPlaceholderText("örn. 0:70:6; 120:55:8; 240:60:8 (boş: tek geometri)")
        self.sectors.setToolTip(
            "Geoteknik sektörler: başlangıç azimutu (° kuzeyden, 0 = kuzey şevi) : "
            "basamak şev açısı : berm. Her sektör bir sonrakinin başlangıcına kadar sürer; "
            "basamak yüksekliği tümünde aynıdır."
        )
        form.addRow("Geoteknik sektörler", self.sectors)
        self.ramp_on = QCheckBox("Spiral rampa (nakliye yolu) tasarla")
        self.ramp_w = _spin(25.0, 3.0, 80.0, 1.0, 1)
        self.ramp_g = _spin(10.0, 1.0, 25.0, 0.5, 1)
        self.ramp_exit = _spin(0.0, 0.0, 359.0, 15.0, 0)
        self.ramp_dir = QComboBox()
        self.ramp_dir.addItems(["Saat yönünde (inerken)", "Saat yönünün tersine (inerken)"])
        form.addRow(self.ramp_on)
        form.addRow("Rampa genişliği (m) / eğim %", _row(self.ramp_w, self.ramp_g))
        form.addRow("Çıkış yönü (° kuzeyden) / dönüş", _row(self.ramp_exit, self.ramp_dir))
        run = QPushButton("Tasarla, yüzeyi kaydet, blokları işaretle")
        run.clicked.connect(self._guard(self.run))
        export = QPushButton("Tasarımı DXF olarak dışa aktar...")
        export.clicked.connect(self._guard(self.export))
        form.addRow(_row(run, export))
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        form.addRow(self.result_label)
        self.model.currentTextChanged.connect(self._fill)
        self._fill(self.model.currentText())
        self._overall()

    def _guard(self, slot: Any) -> Any:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Basamak tasarımı", str(exc))

        return run

    def _fill(self, name: str) -> None:
        if name:
            top = int(np.nanmax(BlockModel.open(self.project, name).values("pit_shell")))
            self.shell.setMaximum(max(top, 1))
            self.shell.setValue(max(top, 1))

    def geometry_value(self) -> BenchGeometry:
        return BenchGeometry(self.face.value(), self.bench_h.value(), self.berm.value())

    def _overall(self) -> None:
        try:
            self.overall.setText(f"{self.geometry_value().overall_angle:.1f}°")
        except Exception as exc:
            self.overall.setText(str(exc))

    def _design(self, dxf: Path | None) -> DesignReport:
        topo = self.topography.currentText()
        return design_from_model(
            self.project,
            BlockModel.open(self.project, self.model.currentText()),
            self.geometry_value(),
            last_shell=self.shell.value(),
            topography=None if topo.startswith("(") else topo,
            save_surface=f"tasarim_{self.shell.value()}",
            flag_attribute="pit_tasarim",
            dxf=dxf,
            ramp=Ramp(
                self.ramp_w.value(),
                self.ramp_g.value() / 100.0,
                self.ramp_exit.value(),
                self.ramp_dir.currentIndex() == 0,
            )
            if self.ramp_on.isChecked()
            else None,
            sectors=parse_bench_sectors(self.sectors.text(), self.bench_h.value()),
        )

    def run(self) -> None:
        self.report = r = self._design(None)
        outside = r.pit.volume(outside_model_only=True)
        self.result_label.setText(
            f"Kazı hacmi {r.pit.volume() / 1e6:,.2f} Mm³; kabukta {r.shell_blocks:,} blok, "
            f"tasarımda {r.design_blocks:,} blok. Değer {r.shell_value / 1e6:,.1f} → "
            f"{r.design_value / 1e6:,.1f} M$. Yüzey 'tasarim_{self.shell.value()}', "
            "bloklar 'pit_tasarim' olarak kaydedildi."
            + (
                f"\nRampa: {r.pit.ramp_length:,.0f} m, {r.pit.ramp[0, 2]:.0f} m kotundan "
                f"{r.pit.ramp[-1, 2]:.0f} m kotuna."
                if r.pit.ramp is not None
                else ""
            )
            + (
                f"\nUyarı: tasarımın {outside / 1e6:,.2f} Mm³'ü blok modeli alanının dışında; "
                "modeli daha geniş (dolgu payı ile) oluşturun."
                if outside > 0
                else ""
            )
        )

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "DXF kaydet", "tasarim.dxf", "DXF (*.dxf)")
        if path:
            self.report = self._design(Path(path))
            self.result_label.setText(f"Tasarım yazıldı: {path}")


LANE_HEADERS = (
    "Yıl", "Cut-off", "Malzeme t", "Cevher t", "Tenör", "Metal", "Nakit akışı", "Kısıt",
)  # fmt: skip
LIMIT_NAMES = {"mine": "maden", "mill": "tesis", "market": "pazar"}


class CutoffDialog(QDialog):
    """Lane cut-off grades per year inside the optimised pit."""

    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cut-off optimizasyonu (Lane)")
        self.resize(820, 560)
        self.project = project
        self.lane: LaneResult | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.model = QComboBox()
        self.model.addItems(
            [
                m
                for m in block_models(project)
                if "pit_value" in BlockModel.open(project, m).attributes
            ]
        )
        self.grade = QComboBox()
        self.rock = QComboBox()
        self.shell = QSpinBox(minimum=1, maximum=1000, value=1)
        self.mine = _spin(10.0, 0.01, 1000.0, 0.5, 2)
        self.mill = _spin(2.0, 0.01, 1000.0, 0.1, 2)
        self.fixed = _spin(10.0, 0.0, 1e5, 1.0, 2)
        self.discount = _spin(10.0, 0.0, 50.0, 0.5, 1)
        self.economics = QLabel()
        self.economics.setWordWrap(True)
        form.addRow("Blok modeli (optimize edilmiş)", self.model)
        form.addRow("Tenör özniteliği", self.grade)
        form.addRow("Kaya/hava özniteliği", self.rock)
        form.addRow("Son kabuk (nihai ocak)", self.shell)
        form.addRow("Madencilik kapasitesi (Mt/yıl)", self.mine)
        form.addRow("Tesis kapasitesi (Mt cevher/yıl)", self.mill)
        form.addRow("Sabit gider (M$/yıl)", self.fixed)
        form.addRow("İskonto oranı %", self.discount)
        form.addRow("Ekonomi (ocak optimizasyonundan)", self.economics)
        layout.addLayout(form)
        run = QPushButton("Yıllık cut-off'ları hesapla")
        run.clicked.connect(self._guard)
        layout.addWidget(run)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(LANE_HEADERS))
        self.table.setHorizontalHeaderLabels(LANE_HEADERS)
        layout.addWidget(self.table)
        self.model.currentTextChanged.connect(self._fill)
        self._fill(self.model.currentText())

    def _guard(self) -> None:
        try:
            self.run()
        except Exception as exc:
            QMessageBox.critical(self, "Cut-off optimizasyonu", str(exc))

    def _econ(self) -> Economics:
        model = BlockModel.open(self.project, self.model.currentText())
        return Economics.from_json(model.attributes["pit_value"]["economics"])

    def _fill(self, name: str) -> None:
        self.grade.clear()
        self.rock.clear()
        if not name:
            return
        model = BlockModel.open(self.project, name)
        attrs = [a for a in model.attributes if not a.startswith(("pit_", "kat_", "ug_"))]
        self.grade.addItems(attrs)
        self.rock.addItem("(yok)")
        self.rock.addItems(attrs)
        if "kaya" in attrs:
            self.rock.setCurrentText("kaya")
        top = int(np.nanmax(model.values("pit_shell")))
        self.shell.setMaximum(max(top, 1))
        self.shell.setValue(max(top, 1))
        e = self._econ()
        self.economics.setText(
            ("NSR" if e.nsr else f"fiyat {e.price:g}/{e.price_unit.value}, verim {e.recovery:g}")
            + f"; madencilik {e.mining_cost:g}/t, işleme {e.processing_cost:g}/t"
        )

    def run(self) -> None:
        rock = self.rock.currentText()
        self.lane = lane = cutoffs_from_model(
            BlockModel.open(self.project, self.model.currentText()),
            self.grade.currentText(),
            self._econ(),
            mine_capacity=self.mine.value() * 1e6,
            mill_capacity=self.mill.value() * 1e6,
            fixed=self.fixed.value() * 1e6,
            discount=self.discount.value() / 100.0,
            last_shell=self.shell.value(),
            rock_attribute=None if rock.startswith("(") else rock,
        )
        self.table.setRowCount(len(lane.years))
        for r, y in enumerate(lane.years):
            cells = (
                str(y.year),
                "tümü atık" if math.isinf(y.cutoff) else f"{y.cutoff:.3f}",
                f"{y.material_t:,.0f}",
                f"{y.ore_t:,.0f}",
                "" if math.isnan(y.grade) else f"{y.grade:.3f}",
                f"{y.metal:,.1f}",
                f"{y.cash:,.0f}",
                LIMIT_NAMES[y.limit],
            )
            for c, text in enumerate(cells):
                _cell(self.table, r, c, text)
        self.summary.setText(
            f"NPV {lane.npv / 1e6:,.1f} M$, {len(lane.years)} yıl. Cut-off ilk yıllarda "
            "yüksek tutulur (paranın zaman değeri), rezerv azaldıkça düşer. Lane modeli: "
            "kalan rezervin tenör dağılımı değişmez varsayılır; seyrelme ve kazı kaybı "
            "uygulanmaz."
        )
