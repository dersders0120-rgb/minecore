"""Underground workbench: stope envelope, stope selection, decline layout."""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QWidget,
)

from minecore.blockmodel import BlockModel
from minecore.gui.layout import fit_to_screen, scroll_panel
from minecore.gui.pit import PRICE_TR
from minecore.gui.resource import _row, _spin, block_models
from minecore.pitopt.cashflow import (
    Capital,
    CashFlow,
    FinancialTerms,
    cash_flow,
    parse_capital,
)
from minecore.pitopt.economics import Economics, PriceUnit
from minecore.pitopt.scheduling import ConstrainedSchedule
from minecore.project import Project
from minecore.underground.development import (
    DeclineDesign,
    Development,
    development_from_model,
)
from minecore.underground.economics import StopeValue, schedule_stopes, stope_values
from minecore.underground.stopes import StopeResult, stopes_from_model


class StopeDialog(QDialog):
    """Floating stope envelope and non-overlapping stopes."""

    def __init__(self, project: Project, parent: QWidget) -> None:  # noqa: PLR0915 - one form
        super().__init__(parent)
        self.setWindowTitle("Yeraltı: pano zarfı ve pano seçimi")
        fit_to_screen(self, 1000, 600)
        self.project = project
        self.stopes: StopeResult | None = None
        layout = QHBoxLayout(self)
        form = QFormLayout()
        scroll_panel(layout, form)
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
        form.addRow(QLabel("<b>Pano ekonomisi ve üretim çizelgesi</b>"))
        self.price = _spin(2000.0, 0.0001, 1e9, 10.0, 4)
        self.price_unit = QComboBox()
        for unit, label in PRICE_TR.items():
            self.price_unit.addItem(label, unit.value)
        self.recovery = _spin(90.0, 1.0, 100.0, 1.0, 1)
        self.mining = _spin(45.0, 0.0, 1e6, 1.0, 2)
        self.processing = _spin(20.0, 0.0, 1e6, 1.0, 2)
        self.selling = _spin(0.0, 0.0, 1e9, 1.0, 2)
        self.payable = _spin(100.0, 1.0, 100.0, 0.5, 1)
        self.royalty = _spin(0.0, 0.0, 99.0, 0.5, 1)
        self.dilution = _spin(10.0, 0.0, 200.0, 1.0, 1)
        self.mine_recovery = _spin(90.0, 1.0, 100.0, 1.0, 1)
        self.dev_cost = _spin(0.0, 0.0, 1e9, 10000.0, 0)
        self.capacity = _spin(0.5, 0.001, 100.0, 0.05, 3)
        self.discount = _spin(8.0, 0.0, 50.0, 0.5, 1)
        self.order = QComboBox()
        self.order.addItems(["Alttan yukarı (seviye seviye)", "Üstten aşağı (seviye seviye)"])
        self.capex = QLineEdit()
        self.capex.setPlaceholderText("yıl:M$, örn. 0:80; 3:15")
        self.fixed = _spin(0.0, 0.0, 1e6, 0.5, 2)
        self.tax = _spin(0.0, 0.0, 80.0, 1.0, 1)
        form.addRow("Metal fiyatı", _row(self.price, self.price_unit))
        form.addRow("Tesis kazanımı % / satış maliyeti", _row(self.recovery, self.selling))
        form.addRow("Ödenebilir % / royalty %", _row(self.payable, self.royalty))
        form.addRow("Yeraltı madencilik / zenginleştirme ($/t)", _row(self.mining, self.processing))
        form.addRow("Seyrelme % / kazı kazanımı %", _row(self.dilution, self.mine_recovery))
        form.addRow("Pano başına hazırlık maliyeti ($)", self.dev_cost)
        form.addRow("Üretim kapasitesi (Mt/yıl) / iskonto %", _row(self.capacity, self.discount))
        form.addRow("Sıralama", self.order)
        form.addRow("Yatırım", self.capex)
        form.addRow("Sabit gider (M$/yıl) / vergi %", _row(self.fixed, self.tax))
        plan = QPushButton("Panoları değerle, çizelgele, nakit akışı")
        plan.clicked.connect(self._guarded(self.plan))
        form.addRow(plan)
        self.plan_summary = QLabel()
        self.plan_summary.setWordWrap(True)
        form.addRow(self.plan_summary)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Pano", "i / j / k", "Tonaj (t)", "Tenör", "Metal", "Değer ($)"]
        )
        tabs.addTab(self.table, "Panolar")
        self.sched_table = QTableWidget(0, 7)
        self.sched_table.setHorizontalHeaderLabels(
            ["Yıl", "Kazı (t)", "Tenör", "Metal", "Gelir M$", "Maliyet M$", "Nakit M$"]
        )
        tabs.addTab(self.sched_table, "Üretim çizelgesi")
        self.cash_table = QTableWidget(0, 0)
        tabs.addTab(self.cash_table, "Nakit akışı")
        self.values: list[StopeValue] = []
        self.sched: ConstrainedSchedule | None = None
        self.flow: CashFlow | None = None
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

    def economics(self) -> Economics:
        return Economics(
            price=self.price.value(),
            price_unit=PriceUnit(self.price_unit.currentData()),
            recovery=self.recovery.value() / 100.0,
            mining_cost=self.mining.value(),
            processing_cost=self.processing.value(),
            selling_cost=self.selling.value(),
            mining_recovery=self.mine_recovery.value() / 100.0,
            dilution=self.dilution.value() / 100.0,
            payable=self.payable.value() / 100.0,
            royalty=self.royalty.value() / 100.0,
        )

    def plan(self) -> None:
        if self.stopes is None:
            self.run()
        assert self.stopes is not None
        model = BlockModel.open(self.project, self.model.currentText())
        unit = model.attributes[self.grade.currentText()].get("unit")
        dev = self.dev_cost.value()
        self.values = stope_values(self.stopes.stopes, self.economics(), unit, dev)
        self.sched, starts = schedule_stopes(
            self.values,
            self.capacity.value() * 1e6,
            self.discount.value() / 100.0,
            bottom_up=self.order.currentIndex() == 0,
            development_cost=dev,
        )
        for n, v in enumerate(self.values):
            self.table.setItem(n, 5, QTableWidgetItem(f"{v.value:,.0f}"))
        rows = self.sched.rows
        self.sched_table.setRowCount(len(rows))
        for n, r in enumerate(rows):
            cells = [
                str(r.period),
                f"{r.mined_t:,.0f}",
                f"{r.feed_grade:.3f}",
                f"{r.metal:,.0f}",
                f"{r.revenue / 1e6:,.2f}",
                f"{r.mining_cost / 1e6:,.2f}",
                f"{r.cash / 1e6:,.2f}",
            ]
            for c, text in enumerate(cells):
                self.sched_table.setItem(n, c, QTableWidgetItem(text))
        if not rows:
            self.flow = None
            self.plan_summary.setText("Pozitif değerli pano yok: çizelge boş.")
            return
        terms = FinancialTerms(
            tuple(Capital(c.period, c.amount * 1e6) for c in parse_capital(self.capex.text())),
            fixed_cost=self.fixed.value() * 1e6,
            tax_rate=self.tax.value() / 100.0,
            discount_rate=self.discount.value() / 100.0,
            royalty=self.royalty.value() / 100.0,
        )
        self.flow = flow = cash_flow(rows, terms)
        headers = ["Kalem", *(str(r.period) for r in flow.rows)]
        lines = [
            ("Gelir", [r.revenue for r in flow.rows]),
            ("FAVÖK", [r.ebitda for r in flow.rows]),
            ("Yatırım", [r.capital for r in flow.rows]),
            ("Vergi", [r.tax for r in flow.rows]),
            ("Nakit akışı", [r.cash for r in flow.rows]),
        ]
        self.cash_table.setColumnCount(len(headers))
        self.cash_table.setRowCount(len(lines))
        self.cash_table.setHorizontalHeaderLabels(headers)
        for n, (label, vals) in enumerate(lines):
            self.cash_table.setItem(n, 0, QTableWidgetItem(label))
            for c, value in enumerate(vals, start=1):
                self.cash_table.setItem(n, c, QTableWidgetItem(f"{value / 1e6:,.2f}"))
        used = len(starts)
        irr = flow.irr()
        self.plan_summary.setText(
            f"{used} pozitif değerli pano {len(rows)} yılda çizelgelendi "
            f"({sum(r.mined_t for r in rows) / 1e6:,.2f} Mt). Çizelge NPV "
            f"{self.sched.npv / 1e6:,.1f} M$; proje NPV {flow.npv / 1e6:,.1f} M$"
            + (f", İVO %{irr * 100:.1f}" if math.isfinite(irr) else "")
            + ". Sıralama kurallıdır (seviye seviye, değerli pano önce); optimizasyon değildir."
        )

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
        self.drives = QCheckBox("Her katta cevher doğrultusunda galeri (ore drive)")
        self.drives.setChecked(True)
        form.addRow(self.drives)
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
        lo, hi = model.grid.bounds()
        # Default portal: beside the model on the east side, at the model top.
        self.px.setValue(float(hi[0]) + 100.0)
        self.py.setValue(0.5 * float(lo[1] + hi[1]))
        self.pz.setValue(float(hi[2]))

    def run(self, dxf: Path | None) -> None:
        design = DeclineDesign(
            self.gradient.value() / 100.0,
            self.radius.value(),
            self.standoff.value(),
            self.drive_w.value(),
            self.drive_h.value(),
            clockwise=self.direction.currentIndex() == 0,
            ore_drives=self.drives.isChecked(),
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
            f"{d.crosscut_length:,.0f} m, cevher galerileri {d.drive_length:,.0f} m; "
            f"toplam {d.total_length:,.0f} m, "
            f"{d.volume:,.0f} m³ ({d.tonnes:,.0f} t) kazı. Çizgiler 'dekline' adıyla "
            "kaydedildi (3B görünümde)."
        )

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "DXF kaydet", "dekline.dxf", "DXF (*.dxf)")
        if path:
            self.run(Path(path))
