"""Open-pit optimisation dialog: economics, slopes, nested shells, pushbacks, schedule."""

from __future__ import annotations

import csv
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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
from minecore.gui.layout import fit_to_screen, scroll_panel
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
    parse_exits,
)
from minecore.pitopt.benchdesign import (
    parse_sectors as parse_bench_sectors,
)
from minecore.pitopt.cashflow import (
    Capital,
    CashFlow,
    CashFlowRow,
    FinancialTerms,
    cash_flow,
    parse_capital,
)
from minecore.pitopt.cutoff import LaneResult, cutoffs_from_model
from minecore.pitopt.design import edge_blocks
from minecore.pitopt.economics import PitOptError, parse_products, parse_routes
from minecore.pitopt.milp import OptimisedSchedule, schedule_optimised
from minecore.pitopt.optimise import route_tonnes, store
from minecore.pitopt.phases import best_pushbacks
from minecore.pitopt.scenarios import (
    Scenario,
    comparison,
    delete_scenario,
    list_scenarios,
    results_of,
    save_scenario,
    write_comparison,
)
from minecore.pitopt.scheduling import (
    ConstrainedSchedule,
    InventoryRow,
    Limits,
    bench_inventory,
    schedule_constrained,
)
from minecore.pitopt.sensitivity import Input, Plan, Sensitivity, parse_changes, sensitivity
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


def parse_bins(text: str) -> tuple[float, ...]:
    """``"1.0; 2.0"`` -> (1.0, 2.0): grade edges of the stockpile bins."""
    try:
        return tuple(float(v) for v in text.replace(",", ";").split(";") if v.strip())
    except ValueError:
        raise PitOptError(f"grade bins {text!r} are not numbers") from None


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
        fit_to_screen(self, 1400, 860)
        self.project = project
        self.pit: PitResult | None = None
        self.phase: Any = None
        self.sched: ConstrainedSchedule | None = None

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        scroll_panel(layout, left)
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
        self.payable = _spin(100.0, 1.0, 100.0, 0.5, 1)
        self.payable.setToolTip("Alıcının ödediği metal oranı (izabe koşulları)")
        self.royalty = _spin(0.0, 0.0, 99.0, 0.5, 2)
        self.royalty.setToolTip(
            "Satış gelirinin devlet/arazi payı (ör. Türkiye maden devlet hakkı)"
        )
        ef.addRow("Ödenebilir metal % / royalty (devlet hakkı) %", _row(self.payable, self.royalty))
        self.routes = QLineEdit()
        self.routes.setPlaceholderText("örn. liç:60:4; yığın liçi:40:2 (ad:kazanım %:maliyet $/t)")
        self.routes.setToolTip(
            "Ek işleme yolları. Yukarıdaki kazanım ve zenginleştirme maliyeti ana yoldur; "
            "her blok en yüksek marjı veren yola gider (pit_route özniteliği)."
        )
        ef.addRow("Ek işleme yolları", self.routes)
        self.coproducts = QLineEdit()
        self.coproducts.setPlaceholderText(
            "örn. ag:24:ozt:70:95 (öznitelik:fiyat:birim:kazanım %:ödenebilir %:satış)"
        )
        self.coproducts.setToolTip(
            "Ana metalle birlikte değerlenen yan ürünler, ';' ile ayrılır. Birim ozt, g, lb "
            "veya t. Ek işleme yolu varsa son alan her yol için kazanımı '/' ile verir "
            "(örn. ag:24:ozt:70:95:0:20). Blok geliri tüm metallerin toplamıdır."
        )
        ef.addRow("Yan ürünler", self.coproducts)
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
        self.by_npv = QCheckBox("Pushback'leri NPV'ye göre seç")
        self.by_npv.setToolTip(
            "Kabukları pushback'lere, çizelge NPV'sini en büyütecek şekilde gruplar "
            "(her pushback en az yukarıdaki cevheri taşır). Az kabukta tüm gruplamalar "
            "denenir, çok kabukta yerel arama yapılır."
        )
        self.max_phases = QSpinBox(minimum=0, maximum=30, value=0)
        self.max_phases.setSpecialValueText("sınırsız")
        pf.addRow(_row(self.by_npv, QLabel("en çok"), self.max_phases))
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
        self.bins = QLineEdit()
        self.bins.setPlaceholderText("örn. 1.0; 2.0 (tenör sınırları; boş: tek karışık stok)")
        self.bins.setToolTip(
            "Stok tenör sınıfları. Tesis her yıl en yüksek sınıftan başlayarak beslenir; "
            "düşük tenörlü cevher stokta bekler, yüksek tenörlü stok önce geri alınır."
        )
        pf.addRow("Stok tenör sınıfları", self.bins)
        self.stock_caps = QLineEdit()
        self.stock_caps.setPlaceholderText("örn. 5 (Mt, ana yol önce, ; ile; boş: sınırsız)")
        self.stock_caps.setToolTip("Stok sahası kapasitesi; dolunca fazla cevher döküme gider.")
        pf.addRow("Stok kapasitesi (Mt)", self.stock_caps)
        self.rehandle = _spin(0.0, 0.0, 1e3, 0.1, 2)
        pf.addRow("Stoktan geri yükleme maliyeti ($/t)", self.rehandle)
        self.method = QComboBox()
        self.method.addItems(["Kurallı (hızlı)", "NPV optimizasyonu (MILP)"])
        self.method.setToolTip(
            "MILP: aynı kurallarla (kapasiteler, gecikme, batma hızı, stok) NPV'yi en büyüten "
            "çizelge; kurallı çizelgeden kötü olamaz. Büyük modellerde süre sınırına kadar sürer."
        )
        self.time_limit = QSpinBox(minimum=5, maximum=3600, value=60)
        pf.addRow("Çizelge yöntemi / süre sınırı (s)", _row(self.method, self.time_limit))
        pf.addRow("Pushback'ler arası basamak gecikmesi", self.lag)
        pf.addRow("Batma hızı (basamak/yıl)", self.sinking)
        left.addWidget(pb)

        fin = QGroupBox("Proje nakit akışı")
        ff = QFormLayout(fin)
        self.capex = QLineEdit()
        self.capex.setPlaceholderText("örn. 0:250; 4:40 (yıl:M$; 0 = üretim öncesi)")
        self.capex.setToolTip("Yatırımlar. 0. yıl inşaat dönemidir; ek yatırımlar sonraki yıllara.")
        self.fixed = _spin(0.0, 0.0, 1e5, 1.0, 2)
        self.closure = _spin(0.0, 0.0, 1e5, 1.0, 2)
        self.tax = _spin(0.0, 0.0, 99.0, 1.0, 1)
        self.depreciation = QSpinBox(minimum=1, maximum=50, value=5)
        self.mid_period = QCheckBox("Yıl ortasında iskonto")
        ff.addRow("Yatırım (capex)", self.capex)
        ff.addRow("Sabit gider (M$/yıl)", self.fixed)
        ff.addRow("Kapanış maliyeti (M$)", self.closure)
        ff.addRow("Gelir vergisi % / amortisman (yıl)", _row(self.tax, self.depreciation))
        ff.addRow(self.mid_period)
        ff.addRow(make)
        ff.addRow(save)
        ff.addRow(export)
        left.addWidget(fin)
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
        self.sched_table = QTableWidget(0, 11)
        self.sched_table.setHorizontalHeaderLabels(
            [
                "Yıl",
                "Kazı Mt",
                "Cevher kazı Mt",
                "Tesise Mt",
                "Besleme tenörü",
                "Metal",
                "Stok Mt",
                "Stoktan Mt",
                "Atığa cevher Mt",
                "Nakit M$",
                "İskontolu M$",
            ]
        )
        right.addTab(self.sched_table, "Üretim çizelgesi")
        self.inv_table = QTableWidget(0, len(INVENTORY_HEADERS))
        self.inv_table.setHorizontalHeaderLabels(INVENTORY_HEADERS)
        right.addTab(self.inv_table, "Pushback-basamak envanteri")
        self.cash_table = QTableWidget(0, len(CASH_HEADERS))
        self.cash_table.setHorizontalHeaderLabels(CASH_HEADERS)
        right.addTab(self.cash_table, "Nakit akışı")
        right.addTab(self._sensitivity_page(), "Duyarlılık")
        right.addTab(self._scenario_page(), "Senaryolar")
        self.flow: CashFlow | None = None
        self.inventory: list[InventoryRow] = []
        self.tabs = right
        self.note = QLabel(
            "Kabuklar Lerchs-Grossmann ile aynı sonucu veren en büyük kapanış (max-flow) ile "
            "bulunur. Çizelge kurallı (greedy) bir sıralamadır: kazı ve tesis kapasitesi, "
            "basamak gecikmesi, batma hızı ve stok sahasına uyar; aynı kurallarla NPV'yi en "
            "büyüten MILP çizelgesi de seçilebilir."
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
        for w in (
            self.price,
            self.price_unit,
            self.recovery,
            self.selling,
            self.routes,
            self.coproducts,
        ):
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
            payable=self.payable.value() / 100.0,
            royalty=self.royalty.value() / 100.0,
            coproducts=() if self.nsr.isChecked() else parse_products(self.coproducts.text()),
        )

    def limits(self) -> Limits:
        return Limits(
            mining_capacity=self.rate.value() * 1e6,
            plant_capacity=self.plant.value() * 1e6,
            bench_lag=self.lag.value(),
            sinking_rate=self.sinking.value(),
            discount_rate=self.discount.value() / 100.0,
            route_capacities=parse_capacities(self.route_caps.text()),
            grade_bins=parse_bins(self.bins.text()),
            stockpile_capacities=parse_capacities(self.stock_caps.text()),
            rehandle_cost=self.rehandle.value(),
        )

    def rock_attribute(self) -> str | None:
        return None if self.rock.currentIndex() == 0 else self.rock.currentText()

    def terms(self) -> FinancialTerms:
        capital = parse_capital(self.capex.text())
        return FinancialTerms(
            tuple(Capital(c.period, c.amount * 1e6) for c in capital),
            fixed_cost=self.fixed.value() * 1e6,
            closure_cost=self.closure.value() * 1e6,
            tax_rate=self.tax.value() / 100.0,
            depreciation_years=self.depreciation.value(),
            discount_rate=self.discount.value() / 100.0,
            mid_period=self.mid_period.isChecked(),
            royalty=self.royalty.value() / 100.0,
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
                rock_attribute=self.rock_attribute(),
                progress=progress,
            ),
        )
        self.phase, self.sched = None, None
        self._show_shells()
        parts = []
        if econ.routes:
            parts.append(
                "Nihai kabukta yollar: "
                + "; ".join(
                    f"{'ana' if name == 'main' else name} {feed_t / 1e6:,.2f} Mt"
                    for name, feed_t, _ in route_tonnes(self.pit, econ, len(self.pit.rows))
                )
            )
        if econ.coproducts:
            parts.append(
                "Nihai kabukta kazanılan yan ürün: "
                + "; ".join(
                    f"{p.attribute} {m:,.0f} {p.price_unit.value}"
                    for p, m in zip(econ.coproducts, self.pit.rows[-1].coproduct_metal, strict=True)
                )
            )
        self.route_label.setText("\n".join(parts))
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
        width_note = ""
        if self.by_npv.isChecked():
            pit, limits, ore = self.pit, self.limits(), self.min_ore.value() * 1e6
            most = self.max_phases.value() or None
            choice = _with_progress(
                self,
                "Pushback'ler NPV'ye göre seçiliyor...",
                lambda progress: best_pushbacks(
                    pit, final, limits, min_ore_t=ore, max_pushbacks=most, progress=progress.update
                ),
            )
            self.phase = choice.phase
            width_note = (
                f"\nPushback'ler NPV'ye göre seçildi ({choice.evaluations} tasarım, "
                + ("tümü denendi" if choice.exhaustive else "yerel arama")
                + f"): çizelge NPV {choice.start_npv / 1e6:,.1f} → {choice.npv / 1e6:,.1f} M$."
            )
        else:
            self.phase = pushbacks(self.pit, final, self.min_ore.value() * 1e6)
        if self.min_width.value() > 0:
            grid = BlockModel.open(self.project, self.model.currentText()).grid
            fixed = apply_mining_width(
                grid, self.phase, self.slope_profile(), self.min_width.value()
            )
            self.phase = fixed.phase
            width_note += f"\nMinimum genişlik: {fixed.moved:,} blok başka pushback'e alındı" + (
                f"; {fixed.remaining_slivers:,} dar blok kaldı." if fixed.remaining_slivers else "."
            )
        limits = self.limits()
        if self.method.currentIndex() == 1:
            pit, phase, seconds = self.pit, self.phase, self.time_limit.value()
            self.sched = _with_progress(
                self,
                f"Çizelge MILP ile optimize ediliyor (en çok {seconds} s)...",
                lambda progress: schedule_optimised(pit, phase, limits, time_limit=seconds),
            )
        else:
            self.sched = schedule_constrained(self.pit, self.phase, limits)
        self.flow = cash_flow(self.sched.rows, self.terms()) if self.sched.rows else None
        self._show_cash_flow()
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
                _m(p.reclaimed_t),
                _m(p.discarded_t),
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
        _cell(self.sched_table, total, 7, _m(sum(p.reclaimed_t for p in rows)))
        _cell(self.sched_table, total, 8, _m(sum(p.discarded_t for p in rows)))
        _cell(self.sched_table, total, 9, _m(sum(p.cash for p in rows)))
        _cell(self.sched_table, total, 10, _m(self.sched.npv))
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
            f"Çizelge NPV {self.sched.npv / 1e6:,.1f} M$."
            + _method_note(self.sched)
            + (_project_summary(self.flow) if self.flow is not None else "")
            + width_note
            + (
                f"\nUyarı: stok sahası doldu, {lost / 1e6:,.2f} Mt cevher döküme gitti."
                if (lost := sum(p.discarded_t for p in rows)) > 1
                else ""
            )
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
            write_pit_reports_xlsx(target, self.pit, self.sched, self.inventory, self.flow)

    def _scenario_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        save = QPushButton("Mevcut çalışmayı senaryo olarak kaydet...")
        save.clicked.connect(self._guard(self.save_scenario))
        self.scenario_pick = QComboBox()
        load = QPushButton("Girdileri yükle")
        load.setToolTip("Seçili senaryonun tüm girdilerini forma geri yükler.")
        load.clicked.connect(self._guard(self.load_scenario))
        delete = QPushButton("Sil")
        delete.clicked.connect(self._guard(self.delete_scenario))
        export = QPushButton("Karşılaştırmayı CSV olarak kaydet...")
        export.clicked.connect(self._guard(self.export_scenarios))
        v.addLayout(_row(save, export))
        v.addLayout(_row(QLabel("Senaryo"), self.scenario_pick, load, delete))
        self.scenario_table = QTableWidget(0, 0)
        v.addWidget(self.scenario_table, 1)
        self.scenario_figure = Figure(figsize=(6, 3))
        self.scenario_canvas = FigureCanvasQTAgg(self.scenario_figure)  # type: ignore[no-untyped-call]
        v.addWidget(self.scenario_canvas, 1)
        self._show_scenarios()
        return page

    def form_values(self) -> dict[str, Any]:
        """Every input widget of the form by attribute name (a scenario's inputs)."""
        out: dict[str, Any] = {}
        for name, w in vars(self).items():
            if name.startswith("scenario_"):
                continue
            if isinstance(w, QDoubleSpinBox | QSpinBox):
                out[name] = w.value()
            elif isinstance(w, QCheckBox):
                out[name] = w.isChecked()
            elif isinstance(w, QLineEdit):
                out[name] = w.text()
            elif isinstance(w, QComboBox):
                out[name] = w.currentText()
        return out

    def set_form_values(self, values: dict[str, Any]) -> None:
        if "model" in values:
            self.model.setCurrentText(str(values["model"]))  # refills the attribute lists
        for name, value in values.items():
            w = getattr(self, name, None)
            if name == "model" or w is None:
                continue
            if isinstance(w, QDoubleSpinBox):
                w.setValue(float(value))
            elif isinstance(w, QSpinBox):
                w.setValue(int(value))
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QLineEdit):
                w.setText(str(value))
            elif isinstance(w, QComboBox):
                w.setCurrentText(str(value))

    def ask_name(self, default: str) -> str | None:
        """Scenario name from the user (tests replace it)."""
        name, ok = QInputDialog.getText(self, "Senaryo kaydet", "Senaryo adı:", text=default)
        return name.strip() if ok and name.strip() else None

    def save_scenario(self) -> None:
        if self.pit is None:
            raise RuntimeError("Önce kabukları hesaplayın.")
        name = self.ask_name(f"Senaryo {len(list_scenarios(self.project)) + 1}")
        if name is None:
            return
        final = int(self.final.currentData())
        phases = int(self.phase.max()) if self.phase is not None else 0
        save_scenario(
            self.project,
            Scenario(
                name,
                datetime.now(UTC).isoformat(timespec="seconds"),
                self.model.currentText(),
                self.form_values(),
                results_of(self.pit, final, phases, self.sched, self.flow),
            ),
        )
        self._show_scenarios()
        self.scenario_pick.setCurrentText(name)

    def _picked(self) -> Scenario:
        name = self.scenario_pick.currentText()
        for sc in list_scenarios(self.project):
            if sc.name == name:
                return sc
        raise RuntimeError("Önce bir senaryo kaydedin ve seçin.")

    def load_scenario(self) -> None:
        picked = self._picked()
        self.set_form_values(picked.inputs)
        self.inform(
            "Senaryo",
            f"'{picked.name}' girdileri yüklendi. Sonuçlar için kabukları yeniden "
            "hesaplayıp çizelge oluşturun.",
        )

    def delete_scenario(self) -> None:
        delete_scenario(self.project, self._picked().name)
        self._show_scenarios()

    def export_scenarios(self) -> None:
        scenarios = list_scenarios(self.project)
        if not scenarios:
            raise RuntimeError("Kayıtlı senaryo yok.")
        path, _ = QFileDialog.getSaveFileName(
            self, "Karşılaştırmayı kaydet", "senaryolar.csv", "CSV (*.csv)"
        )
        if path:
            write_comparison(Path(path), scenarios)

    def _show_scenarios(self) -> None:
        scenarios = list_scenarios(self.project)
        current = self.scenario_pick.currentText()
        self.scenario_pick.clear()
        self.scenario_pick.addItems([s.name for s in scenarios])
        if current:
            self.scenario_pick.setCurrentText(current)
        table = comparison(scenarios)
        self.scenario_table.setColumnCount(len(table[0]))
        self.scenario_table.setRowCount(len(table) - 1)
        self.scenario_table.setHorizontalHeaderLabels(["Gösterge", *table[0][1:]])
        for r, row in enumerate(table[1:]):
            for c, text in enumerate(row):
                _cell(self.scenario_table, r, c, text)
        self.scenario_figure.clear()
        if scenarios:
            ax = self.scenario_figure.add_subplot(111)
            names = [s.name for s in scenarios]
            bars = ((-0.2, "schedule_npv", "Çizelge NPV"), (0.2, "project_npv", "Proje NPV"))
            for offset, key, label in bars:
                values = [(s.results.get(key) or 0.0) / 1e6 for s in scenarios]
                ax.bar([i + offset for i in range(len(names))], values, width=0.4, label=label)
            ax.set_xticks(range(len(names)), names)
            ax.set_ylabel("M$")
            ax.axhline(0.0, color="0.6", linewidth=0.8)
            ax.legend()
            self.scenario_figure.tight_layout()
        self.scenario_canvas.draw_idle()  # type: ignore[no-untyped-call]

    def _sensitivity_page(self) -> QWidget:
        page = QWidget()
        v = QVBoxLayout(page)
        form = QFormLayout()
        self.sens_changes = QLineEdit("-20; -10; 10; 20")
        self.sens_changes.setToolTip("Her girdinin değişimi, yüzde; ';' ile ayrılır.")
        self.sens_reopt = QCheckBox("Ocağı her durumda yeniden optimize et (yavaş)")
        self.sens_reopt.setToolTip(
            "Kapalı: ocak ve pushback'ler sabit, yalnız blok değerleri ve çizelge değişir "
            "(tek maden planının duyarlılığı). Açık: her durumda ocak yeniden bulunur."
        )
        run = QPushButton("Duyarlılık analizi")
        run.clicked.connect(self._guard(self.run_sensitivity))
        form.addRow("Değişimler (%)", self.sens_changes)
        form.addRow(self.sens_reopt)
        form.addRow(run)
        v.addLayout(form)
        self.sens_table = QTableWidget(0, 0)
        v.addWidget(self.sens_table, 1)
        self.sens_figure = Figure(figsize=(6, 3.5))
        self.sens_canvas = FigureCanvasQTAgg(self.sens_figure)  # type: ignore[no-untyped-call]
        v.addWidget(self.sens_canvas, 2)
        self.sens: Sensitivity | None = None
        return page

    def run_sensitivity(self) -> None:
        if self.pit is None or self.phase is None:
            raise RuntimeError("Önce kabukları hesaplayıp pushback ve çizelge oluşturun.")
        factors = self.pit.revenue_factors
        plan = Plan(
            BlockModel.open(self.project, self.model.currentText()),
            self.grade.currentText(),
            self.economics(),
            self.slope_profile(),
            self.limits(),
            self.terms(),
            factors,
            factors[int(self.final.currentData()) - 1],
            self.min_ore.value() * 1e6,
            self.min_width.value(),
            self.rock_attribute(),
        )
        changes = parse_changes(self.sens_changes.text())
        pit, phase = self.pit, self.phase
        self.sens = _with_progress(
            self,
            "Duyarlılık analizi...",
            lambda progress: sensitivity(
                plan,
                changes=changes,
                reoptimise=self.sens_reopt.isChecked(),
                base=(pit, phase),
                progress=progress.update,
            ),
        )
        self._show_sensitivity()

    def _show_sensitivity(self) -> None:
        s = self.sens
        if s is None:
            return
        changes = sorted({c.change for c in s.cases} | {0.0})
        inputs = list(dict.fromkeys(c.input for c in s.cases if c.input is not None))
        npv = {(c.input, c.change): c.npv for c in s.cases}
        self.sens_table.setColumnCount(len(changes) + 1)
        self.sens_table.setRowCount(len(inputs))
        self.sens_table.setHorizontalHeaderLabels(
            ["Proje NPV (M$)", *(f"{c:+.0%}" if c else "Baz" for c in changes)]
        )
        self.sens_figure.clear()
        ax = self.sens_figure.add_subplot(111)
        for i, which in enumerate(inputs):
            _cell(self.sens_table, i, 0, SENSITIVITY_LABELS[which])
            values = [s.base.npv if c == 0 else npv[(which, c)] for c in changes]
            for j, value in enumerate(values, start=1):
                _cell(self.sens_table, i, j, _m(value) if math.isfinite(value) else "-")
            ax.plot(
                [100 * c for c in changes],
                [v / 1e6 for v in values],
                marker="o",
                label=SENSITIVITY_LABELS[which],
            )
        ax.axhline(0.0, color="0.6", linewidth=0.8)
        ax.set_xlabel("Girdi değişimi (%)")
        ax.set_ylabel("Proje NPV (M$)")
        mode = "yeniden optimize edilen ocak" if s.reoptimised else "sabit ocak tasarımı"
        ax.set_title(f"Duyarlılık ({mode})")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        self.sens_figure.tight_layout()
        self.sens_canvas.draw_idle()  # type: ignore[no-untyped-call]
        self.sens_table.resizeColumnsToContents()
        self.tabs.setCurrentIndex(self.tabs.count() - 1)

    def _show_cash_flow(self) -> None:
        rows = self.flow.rows if self.flow is not None else ()
        self.cash_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, value in enumerate(_cash_cells(r)):
                _cell(self.cash_table, i, j, value if isinstance(value, str) else _m(value))
        self.cash_table.resizeColumnsToContents()


def _method_note(sched: ConstrainedSchedule) -> str:
    if not isinstance(sched, OptimisedSchedule):
        return ""
    if sched.method == "milp":
        gain = sched.npv - sched.greedy_npv
        return (
            f"\nMILP çizelgesi: kurallı çizelgeye göre {gain / 1e6:+,.2f} M$ "
            f"(boşluk %{100 * sched.mip_gap:.2f})."
        )
    return f"\nMILP kurallı çizelgeyi geçemedi, kurallı çizelge kullanıldı ({sched.status})."


SENSITIVITY_LABELS = {
    Input.PRICE: "Metal fiyatı",
    Input.MINING: "Madencilik maliyeti",
    Input.PROCESSING: "Zenginleştirme maliyeti",
    Input.RECOVERY: "Metalurjik kazanım",
    Input.DISCOUNT: "İskonto oranı",
    Input.CAPITAL: "Yatırım",
}

CASH_HEADERS = [
    "Yıl",
    "Satış geliri M$",
    "Royalty M$",
    "Net gelir M$",
    "Zenginleştirme M$",
    "Madencilik M$",
    "Sabit gider M$",
    "FAVÖK M$",
    "Amortisman M$",
    "Vergi M$",
    "Yatırım M$",
    "Kapanış M$",
    "Nakit akışı M$",
    "İskontolu M$",
    "Kümülatif M$",
]


def _cash_cells(r: CashFlowRow) -> list[Any]:
    return [
        str(r.period),
        r.gross_revenue,
        r.royalty,
        r.revenue,
        r.processing,
        r.mining,
        r.fixed,
        r.ebitda,
        r.depreciation,
        r.tax,
        r.capital,
        r.closure,
        r.cash,
        r.discounted,
        r.cumulative,
    ]


def _project_summary(flow: CashFlow) -> str:
    irr, payback = flow.irr(), flow.payback()
    return (
        f"\nProje NPV (yatırım, vergi, kapanış dahil) {flow.npv / 1e6:,.1f} M$; "
        + (f"İVO (IRR) %{irr * 100:.1f}" if math.isfinite(irr) else "İVO hesaplanamadı")
        + "; geri ödeme "
        + (f"{payback:.1f} yıl." if math.isfinite(payback) else "sağlanmıyor.")
    )


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
    flow: CashFlow | None = None,
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
                "Stoktan t",
                "Atığa cevher t",
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
                    p.reclaimed_t,
                    p.discarded_t,
                    p.cash,
                    p.discounted,
                ]
                for p in sched.rows
            ],
        )
    if inventory:
        sheets["Envanter"] = (INVENTORY_HEADERS, [_inventory_cells(r) for r in inventory])
    if flow is not None:
        headers = [h.replace(" M$", "") for h in CASH_HEADERS]
        sheets["Nakit akışı"] = (headers, [[r.period, *_cash_cells(r)[1:]] for r in flow.rows])
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
                    "reclaimed_t",
                    "discarded_t",
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
                        f"{p.reclaimed_t:.0f}",
                        f"{p.discarded_t:.0f}",
                        f"{p.cash:.0f}",
                        f"{p.discounted:.0f}",
                    ]
                )


class BenchDesignDialog(QDialog):
    """Bench design (face angle, bench height, berm) around the optimised pit."""

    def __init__(self, project: Project, parent: QWidget) -> None:  # noqa: PLR0915 - one form
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
        self.ramp_on = QCheckBox("Rampa (nakliye yolu) tasarla")
        self.ramp_w = _spin(25.0, 3.0, 80.0, 1.0, 1)
        self.ramp_g = _spin(10.0, 1.0, 25.0, 0.5, 1)
        self.ramp_exit = _spin(0.0, 0.0, 359.0, 15.0, 0)
        self.ramp_dir = QComboBox()
        self.ramp_dir.addItems(["Saat yönünde (inerken)", "Saat yönünün tersine (inerken)"])
        form.addRow(self.ramp_on)
        form.addRow("Rampa genişliği (m) / eğim %", _row(self.ramp_w, self.ramp_g))
        form.addRow("Çıkış yönü (° kuzeyden) / dönüş", _row(self.ramp_exit, self.ramp_dir))
        self.ramp_kind = QComboBox()
        self.ramp_kind.addItems(["Spiral (ocak çevresinde)", "Zikzak (tek şevde, dönüşlü)"])
        self.ramp_span = _spin(120.0, 20.0, 300.0, 10.0, 0)
        self.ramp_span.setToolTip(
            "Zikzak rampanın kullandığı şev yayı (°), çıkış yönü ortada. Dar yay = daha çok dönüş."
        )
        form.addRow("Rampa tipi / zikzak yayı (°)", _row(self.ramp_kind, self.ramp_span))
        self.ramp_exits = QLineEdit()
        self.ramp_exits.setPlaceholderText("örn. 180; 270 (boş: hepsi yukarıdaki yönden)")
        self.ramp_exits.setToolTip(
            "Tasarım yüzeyde birden çok ayrı ocak açarsa her ocağa ayrı rampa çizilir. "
            "Ocaklar kazı hacmine göre sıralanır; en büyüğü yukarıdaki çıkış yönünü, "
            "sonrakiler buradaki yönleri sırayla kullanır."
        )
        form.addRow("Diğer ocakların çıkış yönleri (°)", self.ramp_exits)
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
                self.ramp_span.value() if self.ramp_kind.currentIndex() == 1 else 0.0,
                exits=parse_exits(self.ramp_exits.text()),
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
                f"\nAyrı ocak sayısı {r.pit.pits}, ocak tabanı sayısı {r.pit.bottoms}."
                if r.pit.pits > 1 or r.pit.bottoms > 1
                else ""
            )
            + "".join(
                f"\nRampa {k}: {_length(road):,.0f} m, {road[0, 2]:.0f} m kotundan "
                f"{road[-1, 2]:.0f} m kotuna."
                for k, road in enumerate(r.pit.ramps, 1)
            )
            + (
                f"\n{r.pit.pits_without_ramp} küçük ocak rampa için çok dar; rampa çizilmedi."
                if r.pit.pits_without_ramp
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


def _length(road: npt.NDArray[np.float64]) -> float:
    return float(np.linalg.norm(np.diff(road, axis=0), axis=1).sum())


LANE_HEADERS = (
    "Yıl", "Cut-off", "Malzeme t", "Cevher t", "Tenör", "Metal", "Nakit akışı", "Kısıt",
)  # fmt: skip
LIMIT_NAMES = {"mine": "maden", "mill": "tesis", "market": "pazar"}


class CutoffDialog(QDialog):
    """Lane cut-off grades per year inside the optimised pit."""

    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cut-off optimizasyonu (Lane)")
        fit_to_screen(self, 820, 560)
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
            + (
                "; yan ürünler (eşdeğer tenör): " + ", ".join(p.attribute for p in e.coproducts)
                if e.coproducts
                else ""
            )
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
            + (
                "\nBu girdilerde her yıl aynı cut-off, Lane politikasından daha yüksek NPV "
                "verdiği için o gösterildi."
                if lane.policy == "constant"
                else ""
            )
        )
