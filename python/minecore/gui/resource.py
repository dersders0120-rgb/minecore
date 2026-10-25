"""Workbench dialogs for block models, variograms, estimation and reporting (Turkish UI).

Each dialog is a thin front end over ``minecore.blockmodel`` and
``minecore.estimation``; the numbers come from the same functions scripts use.
"""

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
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from minecore.blockmodel import BlockModel, GradeTonnage, Grid
from minecore.blockmodel.model import KIND as BLOCK_MODEL_KIND
from minecore.blockmodel.workflow import (
    SampleSet,
    estimate_blocks,
    interval_samples,
    load_variograms,
    save_variogram,
)
from minecore.drillholes import DrillholeDatabase, FieldType, Role
from minecore.estimation import (
    Ellipsoid,
    Method,
    ModelType,
    Search,
    Structure,
    VariogramModel,
    experimental,
    fit_spherical,
)
from minecore.jobs import CancelledError, Progress, ProgressEvent
from minecore.project import Project

METHOD_TR = {
    Method.NEAREST: "En yakın komşu (NN)",
    Method.INVERSE_DISTANCE: "Ters mesafe ağırlıklı (IDW)",
    Method.ORDINARY_KRIGING: "Sıradan kriging (OK)",
}
MODEL_TR = {
    ModelType.SPHERICAL: "Küresel",
    ModelType.EXPONENTIAL: "Üstel",
    ModelType.GAUSSIAN: "Gauss",
}


def _spin(value: float, lo: float, hi: float, step: float = 1.0, decimals: int = 2) -> Any:
    box = QDoubleSpinBox(minimum=lo, maximum=hi, value=value, singleStep=step)
    box.setDecimals(decimals)
    return box


def _buttons(dialog: QDialog) -> QDialogButtonBox:
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


def numeric_interval_fields(db: DrillholeDatabase) -> list[str]:
    """``table / field`` choices for numeric interval fields (composites first)."""
    choices: list[tuple[bool, str]] = []
    for info in db.tables_with_role(Role.INTERVAL):
        composite = any(f.name == "mid_x" for f in info.fields)
        for spec in info.fields:
            if spec.type is not FieldType.NUMERIC:
                continue
            if spec.name.startswith("mid_") or spec.name.endswith("_coverage"):
                continue
            choices.append((not composite, f"{info.name} / {spec.name}"))
    return [c for _, c in sorted(choices)]


def block_models(project: Project) -> list[str]:
    return [r.name for r in project.objects(BLOCK_MODEL_KIND)]


def _samples(db: DrillholeDatabase, choice: str) -> SampleSet:
    table, field = choice.split(" / ", 1)
    return interval_samples(db, table, field)


class EllipsoidBox(QGroupBox):
    """Ranges and angles of an anisotropy ellipsoid (MineCore convention)."""

    def __init__(self, title: str, radius: float = 100.0) -> None:
        super().__init__(title)
        form = QFormLayout(self)
        self.major = _spin(radius, 0.01, 1e6, 10)
        self.semi = _spin(radius, 0.01, 1e6, 10)
        self.minor = _spin(radius, 0.01, 1e6, 10)
        self.azimuth = _spin(0.0, -360.0, 360.0, 5)
        self.dip = _spin(0.0, -90.0, 90.0, 5)
        self.rake = _spin(0.0, -180.0, 180.0, 5)
        row = QHBoxLayout()
        for box in (self.major, self.semi, self.minor):
            row.addWidget(box)
        form.addRow("Yarıçaplar (ana/ara/küçük) m", row)
        row2 = QHBoxLayout()
        for box in (self.azimuth, self.dip, self.rake):
            row2.addWidget(box)
        form.addRow("Azimut / eğim / yatım °", row2)
        form.addRow(
            QLabel(
                "Azimut: ana eksen yönü, kuzeyden saat yönünde. Eğim: ana eksenin aşağı "
                "dalımı (pozitif = aşağı)."
            )
        )

    def value(self) -> Ellipsoid:
        r = sorted([self.major.value(), self.semi.value(), self.minor.value()], reverse=True)
        return Ellipsoid(
            r[0], r[1], r[2], self.azimuth.value(), self.dip.value(), self.rake.value()
        )

    def set_value(self, e: Ellipsoid) -> None:
        self.major.setValue(e.major)
        self.semi.setValue(e.semi)
        self.minor.setValue(e.minor)
        self.azimuth.setValue(e.azimuth)
        self.dip.setValue(e.dip)
        self.rake.setValue(e.rake)


class BlockModelDialog(QDialog):
    """Define a regular block model covering the samples of a table."""

    def __init__(self, db: DrillholeDatabase, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Blok modeli oluştur")
        self.db = db
        form = QFormLayout(self)
        self.name = QLineEdit("BM_1")
        self.source = QComboBox()
        self.source.addItems(numeric_interval_fields(db))
        self.sx = _spin(10.0, 0.1, 1e4)
        self.sy = _spin(10.0, 0.1, 1e4)
        self.sz = _spin(5.0, 0.1, 1e4)
        size = QHBoxLayout()
        for box in (self.sx, self.sy, self.sz):
            size.addWidget(box)
        self.padding = _spin(20.0, 0.0, 1e4, 5)
        self.info = QLabel()
        form.addRow("Ad", self.name)
        form.addRow("Kapsanacak veri", self.source)
        form.addRow("Blok boyu X / Y / Z (m)", size)
        form.addRow("Kenar payı (m)", self.padding)
        form.addRow(self.info)
        form.addRow(_buttons(self))
        for box in (self.sx, self.sy, self.sz, self.padding):
            box.valueChanged.connect(self._preview)
        self.source.currentTextChanged.connect(self._preview)
        self._preview()

    def grid(self) -> Grid:
        samples = _samples(self.db, self.source.currentText())
        return Grid.covering(
            samples.xyz, (self.sx.value(), self.sy.value(), self.sz.value()), self.padding.value()
        )

    def _preview(self) -> None:
        if not self.source.currentText():
            self.info.setText("Önce analiz veya kompozit tablosu gerekli.")
            return
        try:
            g = self.grid()
        except Exception as exc:
            self.info.setText(str(exc))
            return
        self.info.setText(
            f"Orijin {g.origin[0]:.1f}, {g.origin[1]:.1f}, {g.origin[2]:.1f}\n"
            f"Blok sayısı {g.count[0]} x {g.count[1]} x {g.count[2]} = {g.blocks:,}"
        )


class VariogramDialog(QDialog):
    """Experimental variogram, model fitting and saving."""

    def __init__(  # noqa: PLR0915 - one form, built inline
        self, db: DrillholeDatabase, project: Project, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Variogram")
        self.resize(1100, 720)
        self.db = db
        self.project = project
        self.samples: SampleSet | None = None
        self.exp: Any = None

        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        layout.addLayout(left, 0)

        data = QGroupBox("Deneysel variogram")
        form = QFormLayout(data)
        self.source = QComboBox()
        self.source.addItems(numeric_interval_fields(db))
        self.lag = _spin(10.0, 0.01, 1e5)
        self.nlags = QSpinBox(minimum=2, maximum=200, value=15)
        self.directional = QCheckBox("Yönlü")
        self.az = _spin(0.0, -360.0, 360.0, 5)
        self.dip = _spin(0.0, -90.0, 90.0, 5)
        self.tol = _spin(22.5, 1.0, 90.0, 2.5)
        self.stats = QLabel()
        run = QPushButton("Hesapla")
        run.clicked.connect(self.compute)
        form.addRow("Veri", self.source)
        form.addRow("Adım (lag) m", self.lag)
        form.addRow("Adım sayısı", self.nlags)
        form.addRow(self.directional)
        form.addRow("Azimut / eğim °", _row(self.az, self.dip))
        form.addRow("Açı toleransı °", self.tol)
        form.addRow(run)
        form.addRow(self.stats)
        left.addWidget(data)

        model = QGroupBox("Model")
        mform = QFormLayout(model)
        self.mtype = QComboBox()
        for t in ModelType:
            self.mtype.addItem(MODEL_TR[t], t)
        self.nugget = _spin(0.0, 0.0, 1e9, 0.01, 4)
        self.sill = _spin(1.0, 1e-9, 1e9, 0.01, 4)
        mform.addRow("Tip", self.mtype)
        mform.addRow("Nugget (C0)", self.nugget)
        mform.addRow("Eşik (C1)", self.sill)
        left.addWidget(model)
        self.ranges = EllipsoidBox("Erişim (range) elipsoidi")
        left.addWidget(self.ranges)
        fit = QPushButton("Otomatik uydur (küresel, izotrop)")
        fit.clicked.connect(self.fit)
        save = QPushButton("Modeli projeye kaydet...")
        save.clicked.connect(self.save)
        close = QPushButton("Kapat")
        close.clicked.connect(self.accept)
        left.addWidget(fit)
        left.addWidget(save)
        left.addWidget(close)
        left.addStretch(1)

        self.figure = Figure(figsize=(6, 5))
        self.canvas = FigureCanvasQTAgg(self.figure)  # type: ignore[no-untyped-call]
        layout.addWidget(self.canvas, 1)
        for w in (self.nugget, self.sill, *_ellipsoid_boxes(self.ranges)):
            w.valueChanged.connect(self.plot)
        self.mtype.currentIndexChanged.connect(self.plot)
        if self.source.count():
            self.compute()

    def model(self) -> VariogramModel:
        return VariogramModel(
            self.nugget.value(),
            (
                Structure(
                    ModelType(self.mtype.currentData()), self.sill.value(), self.ranges.value()
                ),
            ),
        )

    def compute(self) -> None:
        if not self.source.currentText():
            return
        self.samples = _samples(self.db, self.source.currentText())
        s = self.samples
        if self.lag.value() == 10.0 and len(s.xyz) > 1:
            # First run: a lag near the median sample spacing between holes.
            extent = float(np.linalg.norm(np.nanmax(s.xyz, 0) - np.nanmin(s.xyz, 0)))
            self.lag.setValue(max(round(extent / 30, 1), 0.5))
        self.exp = experimental(
            s.xyz,
            s.values,
            self.lag.value(),
            self.nlags.value(),
            azimuth=self.az.value() if self.directional.isChecked() else None,
            dip=self.dip.value(),
            tolerance_deg=self.tol.value(),
        )
        var = float(np.var(s.values))
        self.stats.setText(
            f"{len(s.values)} örnek, ortalama {np.mean(s.values):.4g}, varyans {var:.4g}"
        )
        self.plot()

    def fit(self) -> None:
        if self.exp is None:
            return
        m = fit_spherical(self.exp)
        self.mtype.setCurrentIndex(list(ModelType).index(ModelType.SPHERICAL))
        self.nugget.setValue(m.nugget)
        self.sill.setValue(m.structures[0].sill)
        self.ranges.set_value(m.structures[0].ranges)
        self.plot()

    def plot(self) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        if self.exp is not None and self.samples is not None:
            ok = self.exp.pairs > 0
            ax.scatter(self.exp.mean_distance[ok], self.exp.gamma[ok], color="#1565c0", zorder=3)
            for d, g, n in zip(
                self.exp.mean_distance[ok], self.exp.gamma[ok], self.exp.pairs[ok], strict=True
            ):
                ax.annotate(
                    str(int(n)), (d, g), fontsize=7, xytext=(3, 3), textcoords="offset points"
                )
            ax.axhline(float(np.var(self.samples.values)), color="#9e9e9e", ls="--", lw=1)
            hmax = float(self.exp.lag_centres[-1]) * 1.05
            h = np.linspace(0, hmax, 200)
            if self.directional.isChecked():
                az, dp = math.radians(self.az.value()), math.radians(self.dip.value())
                u = np.array(
                    [math.sin(az) * math.cos(dp), math.cos(az) * math.cos(dp), -math.sin(dp)]
                )
            else:
                u = np.array([0.0, 1.0, 0.0])
            try:
                g = self.model().gamma(h[:, None] * u[None, :])
                ax.plot(h, g, color="#c62828", lw=2)
            except Exception:
                pass
            ax.set_xlim(0, hmax)
        ax.set_xlabel("Mesafe (m)")
        ax.set_ylabel("gamma(h)")
        ax.set_title("Deneysel variogram (noktalar: çift sayısı) ve model")
        ax.grid(alpha=0.3)
        self.canvas.draw_idle()  # type: ignore[no-untyped-call]

    def save(self) -> None:
        default = self.source.currentText().split(" / ")[-1] + "_vg"
        name, ok = QInputDialog.getText(self, "Variogram kaydet", "Ad:", text=default)
        if not ok or not name.strip():
            return
        source = {
            "data": self.source.currentText(),
            "lag": self.lag.value(),
            "nlags": self.nlags.value(),
            "azimuth": self.az.value() if self.directional.isChecked() else None,
            "dip": self.dip.value(),
        }
        save_variogram(self.project, name.strip(), self.model(), source)
        QMessageBox.information(self, "Variogram", f"'{name.strip()}' projeye kaydedildi.")


def _row(*widgets: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    for w in widgets:
        row.addWidget(w)
    return row


def _ellipsoid_boxes(box: EllipsoidBox) -> list[Any]:
    return [box.major, box.semi, box.minor, box.azimuth, box.dip, box.rake]


class EstimationDialog(QDialog):
    """Choose method, search and variogram; estimate one attribute."""

    def __init__(self, db: DrillholeDatabase, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tenör kestirimi")
        self.db = db
        self.project = project
        self.variograms = load_variograms(project)
        form = QFormLayout(self)
        self.model = QComboBox()
        self.model.addItems(block_models(project))
        self.source = QComboBox()
        self.source.addItems(numeric_interval_fields(db))
        self.method = QComboBox()
        for m in Method:
            self.method.addItem(METHOD_TR[m], m)
        self.method.setCurrentIndex(list(Method).index(Method.ORDINARY_KRIGING))
        self.variogram = QComboBox()
        self.variogram.addItems(list(self.variograms))
        self.power = _spin(2.0, 0.5, 5.0, 0.5)
        self.search = EllipsoidBox("Arama elipsoidi", 100.0)
        from_vg = QPushButton("Arama elipsoidini variogram erişiminden al")
        from_vg.clicked.connect(self._search_from_variogram)
        self.min_s = QSpinBox(minimum=1, maximum=200, value=4)
        self.max_s = QSpinBox(minimum=1, maximum=500, value=16)
        self.per_hole = QSpinBox(minimum=0, maximum=100, value=4)
        self.disc = QSpinBox(minimum=1, maximum=6, value=2)
        self.output = QLineEdit()
        self.rock_only = QCheckBox("Yalnız kaya bloklarını kestir ('kaya' özniteliği)")
        form.addRow("Blok modeli", self.model)
        form.addRow("Veri (kompozit önerilir)", self.source)
        form.addRow("Yöntem", self.method)
        form.addRow("Variogram", self.variogram)
        form.addRow("IDW üssü", self.power)
        form.addRow(self.search)
        form.addRow(from_vg)
        form.addRow("En az / en çok örnek", _row(self.min_s, self.max_s))
        form.addRow("Kuyu başına en çok (0 = sınırsız)", self.per_hole)
        form.addRow("Blok ayrıklaştırma (n x n x n)", self.disc)
        form.addRow("Sonuç özniteliği", self.output)
        form.addRow(self.rock_only)
        self.model.currentTextChanged.connect(self._sync_rock)
        self._sync_rock(self.model.currentText())
        form.addRow(_buttons(self))
        self.method.currentIndexChanged.connect(self._sync)
        self.source.currentTextChanged.connect(self._sync)
        self._sync()
        self._search_from_variogram()

    def _sync_rock(self, name: str) -> None:
        has = bool(name) and "kaya" in BlockModel.open(self.project, name).attributes
        self.rock_only.setEnabled(has)
        self.rock_only.setChecked(has)

    def _sync(self) -> None:
        method = Method(self.method.currentData())
        self.variogram.setEnabled(method is Method.ORDINARY_KRIGING)
        self.power.setEnabled(method is Method.INVERSE_DISTANCE)
        self.disc.setEnabled(method is Method.ORDINARY_KRIGING)
        field = self.source.currentText().split(" / ")[-1]
        suffix = {
            Method.NEAREST: "nn",
            Method.INVERSE_DISTANCE: "id",
            Method.ORDINARY_KRIGING: "ok",
        }
        self.output.setText(f"{field}_{suffix[method]}")

    def _search_from_variogram(self) -> None:
        vg = self.variograms.get(self.variogram.currentText())
        if vg is not None and vg.structures:
            self.search.set_value(max(vg.structures, key=lambda s: s.ranges.major).ranges)

    def run(self) -> str:
        model = BlockModel.open(self.project, self.model.currentText())
        method = Method(self.method.currentData())
        variogram = None
        if method is Method.ORDINARY_KRIGING:
            variogram = self.variograms.get(self.variogram.currentText())
            if variogram is None:
                raise RuntimeError("Kriging için önce Variogram penceresinden bir model kaydedin.")
        samples = _samples(self.db, self.source.currentText())
        search = Search(
            self.search.value(), self.min_s.value(), self.max_s.value(), self.per_hole.value()
        )
        attribute = self.output.text().strip()
        if not attribute:
            raise RuntimeError("Sonuç özniteliğine bir ad verin.")
        n = self.disc.value()
        mask = None
        if self.rock_only.isChecked() and "kaya" in model.attributes:
            mask = model.values("kaya") > 0.5
        unestimated = _with_progress(
            self.parent(),  # type: ignore[arg-type]
            "Kestirim yapılıyor...",
            lambda progress: estimate_blocks(
                model,
                samples,
                attribute,
                method=method,
                search=search,
                variogram=variogram,
                power=self.power.value(),
                discretisation=(n, n, n),
                mask=mask,
                progress=progress,
            ),
        )
        return (
            f"'{attribute}' {model.grid.blocks:,} blok için hesaplandı; "
            f"{unestimated:,} blok arama yarıçapı dışında kaldı (kestirilmedi)."
        )


def _with_progress(parent: QWidget, title: str, job: Any) -> Any:
    dialog = QProgressDialog(title, "İptal", 0, 1000, parent)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(300)
    progress = Progress(lambda e: _progress_event(dialog, e), min_interval_s=0.05)
    dialog.canceled.connect(progress.token.cancel)
    try:
        return job(progress)
    except CancelledError:
        raise RuntimeError("İşlem iptal edildi; proje değişmedi.") from None
    finally:
        dialog.close()


def _progress_event(dialog: QProgressDialog, event: ProgressEvent) -> None:
    if event.fraction is not None:
        dialog.setValue(int(event.fraction * 1000))
    dialog.setLabelText(event.message or dialog.labelText())
    QApplication.processEvents()


class GradeTonnageDialog(QDialog):
    """Grade-tonnage table and curve with CSV export."""

    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tenör-tonaj raporu")
        self.resize(1200, 720)
        self.project = project
        self.report: GradeTonnage | None = None
        layout = QHBoxLayout(self)
        left = QVBoxLayout()
        layout.addLayout(left, 0)
        form = QFormLayout()
        left.addLayout(form)
        self.model = QComboBox()
        self.model.addItems(block_models(project))
        self.attribute = QComboBox()
        self.density_attr = QComboBox()
        self.density = _spin(2.7, 0.1, 25.0, 0.05, 3)
        self.c0 = _spin(0.0, -1e9, 1e9, 0.1, 4)
        self.c1 = _spin(5.0, -1e9, 1e9, 0.1, 4)
        self.step = _spin(0.5, 1e-6, 1e9, 0.1, 4)
        form.addRow("Blok modeli", self.model)
        form.addRow("Tenör özniteliği", self.attribute)
        form.addRow("Yoğunluk özniteliği", self.density_attr)
        form.addRow("Sabit yoğunluk (t/m³)", self.density)
        form.addRow("Sınır tenör: başlangıç / bitiş / adım", _row(self.c0, self.c1, self.step))
        run = QPushButton("Hesapla")
        run.clicked.connect(self.compute)
        export = QPushButton("CSV olarak dışa aktar...")
        export.clicked.connect(self.export)
        form.addRow(_row(run, export))
        self.note = QLabel()
        self.note.setWordWrap(True)
        left.addWidget(self.note)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Sınır tenör", "Blok", "Hacim (m³)", "Tonaj (t)", "Ort. tenör", "Metal"]
        )
        left.addWidget(self.table, 1)
        self.figure = Figure(figsize=(6, 5))
        self.canvas = FigureCanvasQTAgg(self.figure)  # type: ignore[no-untyped-call]
        layout.addWidget(self.canvas, 1)
        self.model.currentTextChanged.connect(self._fill_attributes)
        self._fill_attributes(self.model.currentText())

    def _fill_attributes(self, name: str) -> None:
        self.attribute.clear()
        self.density_attr.clear()
        self.density_attr.addItem("(sabit)")
        if not name:
            return
        attrs = BlockModel.open(self.project, name).attributes
        grades = [a for a in attrs if not a.endswith(("_n", "_dist", "_kv"))]
        self.attribute.addItems(grades)
        self.density_attr.addItems(grades)
        if grades:
            values = BlockModel.open(self.project, name).values(grades[0])
            finite = values[np.isfinite(values)]
            if finite.size:
                hi = float(np.nanpercentile(finite, 95))
                self.c1.setValue(round(hi, 2) or 1.0)
                self.step.setValue(max(round(hi / 20, 3), 1e-3))

    def compute(self) -> None:
        model = BlockModel.open(self.project, self.model.currentText())
        cutoffs = list(
            np.arange(self.c0.value(), self.c1.value() + self.step.value() / 2, self.step.value())
        )
        dattr = self.density_attr.currentText()
        density: float | str = self.density.value() if dattr == "(sabit)" else dattr
        self.report = model.grade_tonnage(
            self.attribute.currentText(), [float(c) for c in cutoffs], density=density
        )
        r = self.report
        unit = r.grade_unit or "-"
        self.table.setHorizontalHeaderLabels(
            [
                f"Sınır tenör ({unit})",
                "Blok",
                "Hacim (m³)",
                "Tonaj (t)",
                f"Ort. tenör ({unit})",
                f"Metal ({r.metal_unit})",
            ]
        )
        self.table.setRowCount(len(r.rows))
        for i, row in enumerate(r.rows):
            cells = [
                f"{row.cutoff:.4g}",
                f"{row.blocks:,}",
                f"{row.volume:,.0f}",
                f"{row.tonnes:,.0f}",
                "" if math.isnan(row.grade) else f"{row.grade:.4g}",
                f"{row.metal:,.1f}",
            ]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()
        self.note.setText(
            f"Kestirilmemiş {r.unestimated_blocks:,} blok rapora dahil edilmedi. "
            "Bu tablo bir kaynak sınıflandırması değildir; JORC/NI 43-101 uyumu yetkin kişi "
            "değerlendirmesi gerektirir."
        )
        self.plot()

    def plot(self) -> None:
        self.figure.clear()
        r = self.report
        if r is None:
            return
        ax = self.figure.add_subplot(111)
        c = [row.cutoff for row in r.rows]
        t = [row.tonnes / 1e6 for row in r.rows]
        g = [row.grade for row in r.rows]
        ax.plot(c, t, color="#1565c0", lw=2, marker="o", ms=3)
        ax.set_xlabel(f"Sınır tenör ({r.grade_unit or '-'})")
        ax.set_ylabel("Tonaj (Mt)", color="#1565c0")
        ax2 = ax.twinx()
        ax2.plot(c, g, color="#c62828", lw=2, marker="s", ms=3)
        ax2.set_ylabel(f"Ortalama tenör ({r.grade_unit or '-'})", color="#c62828")
        ax.set_title(f"Tenör-tonaj eğrisi: {r.attribute}")
        ax.grid(alpha=0.3)
        self.figure.tight_layout()
        self.canvas.draw_idle()  # type: ignore[no-untyped-call]

    def export(self) -> None:
        if self.report is None:
            self.compute()
        r = self.report
        assert r is not None
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV kaydet", f"tenor_tonaj_{r.attribute}.csv", "CSV (*.csv)"
        )
        if path:
            write_grade_tonnage_csv(Path(path), r)


def write_grade_tonnage_csv(path: Path, r: GradeTonnage) -> None:
    """Semicolon CSV with a BOM so Excel (Turkish locale) opens it directly."""
    unit = r.grade_unit or "-"
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(
            [
                f"# attribute={r.attribute}",
                f"density={r.density}",
                f"unestimated={r.unestimated_blocks}",
            ]
        )
        w.writerow(
            [
                f"cutoff_{unit}",
                "blocks",
                "volume_m3",
                "tonnes_t",
                f"grade_{unit}",
                f"metal_{r.metal_unit}",
            ]
        )
        for row in r.rows:
            w.writerow(
                [
                    f"{row.cutoff:.6g}",
                    row.blocks,
                    f"{row.volume:.3f}",
                    f"{row.tonnes:.3f}",
                    "" if math.isnan(row.grade) else f"{row.grade:.6g}",
                    f"{row.metal:.3f}",
                ]
            )


def export_block_model_csv(path: Path, model: BlockModel) -> int:
    """Write block centroids, indices and all attributes; returns the row count."""
    grid = model.grid
    xyz = grid.centroids()
    nx, ny, _ = grid.count
    index = np.arange(grid.blocks)
    i, j, k = index % nx, (index // nx) % ny, index // (nx * ny)
    table = model.table()
    names = list(model.attributes)
    columns = [np.asarray(table.column(n).to_numpy(zero_copy_only=False), float) for n in names]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["x", "y", "z", "i", "j", "k", *names])
        for b in range(grid.blocks):
            vals = ["" if math.isnan(c[b]) else f"{c[b]:.6g}" for c in columns]
            w.writerow(
                [
                    f"{xyz[b, 0]:.3f}",
                    f"{xyz[b, 1]:.3f}",
                    f"{xyz[b, 2]:.3f}",
                    i[b],
                    j[b],
                    k[b],
                    *vals,
                ]
            )
    return grid.blocks
