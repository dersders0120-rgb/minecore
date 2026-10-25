"""Implicit domain modelling dialog."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from minecore.blockmodel import BlockModel
from minecore.drillholes import DrillholeDatabase, FieldType, Role
from minecore.geology.implicit import ImplicitResult, interval_contacts, model_domain
from minecore.gui.resource import EllipsoidBox, _spin, block_models
from minecore.project import Project
from minecore.solids import save_solid


def interval_fields(db: DrillholeDatabase) -> list[tuple[str, FieldType]]:
    """``table / field`` of every interval field usable for a domain."""
    out = []
    for info in db.tables_with_role(Role.INTERVAL):
        for spec in info.fields:
            if spec.name.startswith("mid_") or spec.name.endswith("_coverage"):
                continue
            out.append((f"{info.name} / {spec.name}", spec.type))
    return out


class ImplicitDialog(QDialog):
    """Domain solid from drillhole intervals (grade threshold or rock codes)."""

    def __init__(self, project: Project, db: DrillholeDatabase, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Örtük (implicit) domen modeli")
        self.project = project
        self.db = db
        self.result_: ImplicitResult | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.fields = interval_fields(db)
        self.source = QComboBox()
        self.source.addItems([name for name, _ in self.fields])
        self.threshold = _spin(0.5, -1e9, 1e9, 0.1, 4)
        self.codes = QLineEdit()
        self.codes.setPlaceholderText("ör. GRANIT, SKARN")
        self.model = QComboBox()
        self.model.addItems(block_models(project))
        self.name = QLineEdit("cevher")
        self.max_distance = _spin(10.0, 0.5, 1000.0, 1.0, 1)
        self.smoothing = _spin(0.0, 0.0, 1000.0, 0.5, 2)
        self.use_aniso = QCheckBox("Anizotropi kullan (yassı/uzun kütleler)")
        self.aniso = EllipsoidBox("Anizotropi (oranlar önemli, örn. 4 / 4 / 1)", 1.0)
        form.addRow("Sondaj verisi (aralık tablosu / alan)", self.source)
        form.addRow("Tenör eşiği (sayısal alan: ≥ eşik içeride)", self.threshold)
        form.addRow("Kodlar (kategori alanı, virgülle)", self.codes)
        form.addRow("Blok modeli (çözünürlük)", self.model)
        form.addRow("Domen adı", self.name)
        form.addRow("En büyük mesafe değeri (m)", self.max_distance)
        form.addRow("Yumuşatma", self.smoothing)
        form.addRow(self.use_aniso)
        layout.addLayout(form)
        layout.addWidget(self.aniso)
        run = QPushButton("Domen katısını oluştur ve blokları işaretle")
        run.clicked.connect(self._guard)
        layout.addWidget(run)
        self.text = QLabel(
            "Sondaj aralıkları içeride/dışarıda olarak sınıflanır, dokanaklar arasında "
            "işaretli uzaklık RBF ile enterpole edilir. Sonuç bir jeolojik yorum değil, "
            "sondaj dokanaklarının sayısal enterpolasyonudur."
        )
        self.text.setWordWrap(True)
        layout.addWidget(self.text)

    def _guard(self) -> None:
        try:
            self.run()
        except Exception as exc:
            QMessageBox.critical(self, "Örtük model", str(exc))

    def run(self) -> None:
        choice = self.source.currentText()
        kind = dict(self.fields)[choice]
        table, field = choice.split(" / ", 1)
        codes = tuple(c.strip() for c in self.codes.text().split(",") if c.strip())
        data = interval_contacts(
            self.db,
            table,
            field,
            threshold=self.threshold.value() if kind is FieldType.NUMERIC else None,
            codes=codes,
            max_distance=self.max_distance.value(),
        )
        name = self.name.text().strip() or "domen"
        model = BlockModel.open(self.project, self.model.currentText())
        self.result_ = r = model_domain(
            model,
            data,
            name,
            anisotropy=self.aniso.value() if self.use_aniso.isChecked() else None,
            smoothing=self.smoothing.value(),
        )
        save_solid(self.project, name, r.solid)
        self.text.setText(
            f"'{name}' katısı: hacim {r.solid.volume():,.0f} m³, açık kenar "
            f"{r.solid.open_edges()}. Dokanak {data.contacts}, içeride {data.inside}, dışarıda "
            f"{data.outside} nokta. Blok modelinde 'dom_{name}' (1/0) ve 'imp_{name}' "
            f"(işaretli uzaklık) yazıldı; {r.blocks_inside:,} blok içeride."
        )
