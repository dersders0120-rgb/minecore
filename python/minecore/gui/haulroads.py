"""Haul road dialog: shortest haul route along road strings, saved as a string."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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

from minecore.gui.layout import fit_to_screen
from minecore.gui.resource import _row, _spin
from minecore.haulroads import RoadNetwork, haul_lengths, parse_points
from minecore.project import Project
from minecore.strings import String, load_strings, save_strings, string_names


class HaulRouteDialog(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Taşıma yolu mesafesi (yol ağı)")
        fit_to_screen(self, 640, 620)
        self.project = project
        form = QFormLayout(self)

        self.strings = QComboBox()
        self.strings.addItems(string_names(project))
        form.addRow("Yol çizgileri (çizgi seti)", self.strings)

        self.start = [_spin(0.0, -1e8, 1e8, 10.0, 1) for _ in range(3)]
        form.addRow("Başlangıç (ocak çıkışı / yükleme) X / Y / Z", _row(*self.start))
        self.end = [_spin(0.0, -1e8, 1e8, 10.0, 1) for _ in range(3)]
        form.addRow("Varış (döküm / tesis) X / Y / Z", _row(*self.end))

        self.snap = _spin(1.0, 0.0, 50.0, 0.5, 1)
        form.addRow("Birleştirme toleransı (m)", self.snap)
        self.grade = _spin(3.0, 0.5, 20.0, 0.5, 1)
        form.addRow("Rampa sayılan eğim (%)", self.grade)

        self.loaded_ramp = _spin(11, 1, 80, 1, 0)
        self.loaded_flat = _spin(30, 1, 80, 1, 0)
        form.addRow("Dolu hız rampa / düz (km/s)", _row(self.loaded_ramp, self.loaded_flat))
        self.empty_ramp = _spin(25, 1, 80, 1, 0)
        self.empty_flat = _spin(40, 1, 80, 1, 0)
        form.addRow("Boş hız rampa / düz (km/s)", _row(self.empty_ramp, self.empty_flat))

        self.name = QLineEdit("tasima_guzergahi")
        form.addRow("Kayıt adı (güzergâh çizgisi)", self.name)

        run = QPushButton("En kısa güzergâhı bul ve kaydet")
        run.clicked.connect(self._guard(self.run_route))
        form.addRow(run)

        self.summary = QLabel()
        self.summary.setWordWrap(True)
        form.addRow(self.summary)

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Taşıma yolu mesafesi (yol ağı)", str(exc))

        return run

    def _need_strings(self) -> str:
        if not self.strings.currentText():
            raise RuntimeError("Önce yol çizgilerini çizin veya içe aktarın.")
        return self.strings.currentText()

    def _out(self) -> str:
        name = self.name.text().strip()
        if not name:
            raise RuntimeError("Kayıt adı yazın.")
        return name

    @staticmethod
    def _xyz(boxes: list[Any]) -> tuple[float, float, float]:
        x, y, z = (float(b.value()) for b in boxes)
        return x, y, z

    def run_route(self) -> None:
        source = self._need_strings()
        out = self._out()
        network = RoadNetwork(load_strings(self.project, source), self.snap.value())
        route = network.route(
            self._xyz(self.start), self._xyz(self.end), self.grade.value() / 100.0
        )
        save_strings(self.project, out, [String(route.points, False)])
        loaded = route.travel_minutes(self.loaded_ramp.value(), self.loaded_flat.value())
        empty = route.travel_minutes(self.empty_ramp.value(), self.empty_flat.value())
        self.summary.setText(
            f"'{out}' kaydedildi. Toplam uzunluk {route.length:,.0f} m "
            f"(rampa {route.ramp_m:,.0f} m, düz {route.flat_m:,.0f} m). "
            f"Tırmanış {route.climb:,.0f} m, iniş {route.descent:,.0f} m. "
            f"Dolu tek yön süresi {loaded:,.1f} dk, boş dönüş süresi {empty:,.1f} dk. "
            f"Başlangıç ağa uzaklığı {route.snap_start:,.2f} m, varış ağa uzaklığı "
            f"{route.snap_end:,.2f} m. "
            "Bu mesafeyi filo / döküm alanlarındaki taşıma mesafesi alanına yazabilirsiniz."
        )


class RoadDistanceDialog(QDialog):
    """Pit exit -> plant and dump distances along a road network, for the fleet page.

    Dump points are matched by name to the dumps already listed on the fleet page.
    """

    def __init__(self, project: Project, parent: QWidget, dump_names: list[str]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mesafeleri yol ağından al")
        self.project = project
        form = QFormLayout(self)
        self.strings = QComboBox()
        self.strings.addItems(string_names(project))
        form.addRow("Yol çizgileri (çizgi seti)", self.strings)
        self.exit = [_spin(0.0, -1e8, 1e8, 10.0, 1) for _ in range(3)]
        form.addRow("Ocak çıkışı X / Y / Z", _row(*self.exit))
        self.plant = [_spin(0.0, -1e8, 1e8, 10.0, 1) for _ in range(3)]
        form.addRow("Tesis (cevher boşaltma) X / Y / Z", _row(*self.plant))
        self.dumps = QLineEdit("; ".join(f"{n}:0,0,0" for n in dump_names))
        self.dumps.setPlaceholderText("örn. kuzey:1200,3400,850; guney:900,2100,820 (ad:x,y,z)")
        form.addRow("Döküm noktaları", self.dumps)
        self.snap = _spin(1.0, 0.0, 50.0, 0.5, 1)
        form.addRow("Birleştirme toleransı (m)", self.snap)
        ok = QPushButton("Hesapla ve filo alanlarına yaz")
        ok.clicked.connect(self._run)
        form.addRow(ok)
        self.plant_m = 0.0
        self.dump_m: dict[str, float] = {}

    def _run(self) -> None:
        try:
            name = self.strings.currentText()
            if not name:
                raise RuntimeError("Önce yol çizgilerini çizin veya içe aktarın.")
            net = RoadNetwork(load_strings(self.project, name), self.snap.value())
            targets = {"_tesis": tuple(s.value() for s in self.plant)}
            targets.update(parse_points(self.dumps.text()))
            routes = haul_lengths(net, tuple(s.value() for s in self.exit), targets)
        except Exception as exc:
            QMessageBox.critical(self, "Yol ağı", str(exc))
            return
        self.plant_m = routes.pop("_tesis").length
        self.dump_m = {n: r.length for n, r in routes.items()}
        self.accept()
