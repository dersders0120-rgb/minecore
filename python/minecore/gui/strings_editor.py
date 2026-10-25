"""Plan-view string editor: digitise, move, insert and delete points of polylines.

Strings are edited in plan (x, y); each new or moved point takes its z from
the "Kot" field or, when a surface is chosen, from that surface (draped).
The editor works on a copy and writes the whole set back on save, so a set
can be loaded, changed and saved under the same or a new name.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import numpy as np
from matplotlib.backend_bases import MouseButton, MouseEvent
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from minecore import surfaces
from minecore.gui.layout import fit_to_screen, scroll_panel
from minecore.gui.resource import _row, _spin
from minecore.project import Project
from minecore.strings import String, load_strings, save_strings, string_names

MODES = ["Nokta ekle", "Nokta taşı", "Araya nokta ekle", "Nokta sil", "Çizgi seç"]
PICK_PX = 10.0


class _Line:
    def __init__(self, points: Any, closed: bool, layer: str = "") -> None:
        self.points = np.asarray(points, np.float64).reshape(-1, 3)
        self.closed = closed
        self.layer = layer


class StringEditor(QDialog):
    def __init__(self, project: Project, parent: QWidget) -> None:  # noqa: PLR0915 - form
        super().__init__(parent)
        self.setWindowTitle("Çizgi (string) düzenleyici")
        fit_to_screen(self, 1200, 800)
        self.project = project
        layout = QHBoxLayout(self)
        form = QFormLayout()
        scroll_panel(layout, form)
        self.set_pick = QComboBox()
        self.set_pick.addItem("(yeni set)")
        self.set_pick.addItems(string_names(project))
        load = QPushButton("Yükle")
        load.clicked.connect(self._guard(self.load))
        form.addRow("Çizgi seti", _row(self.set_pick, load))
        self.background = QComboBox()
        self.background.addItem("(yok)")
        self.background.addItems(string_names(project))
        self.background.currentTextChanged.connect(lambda _t: self.redraw())
        form.addRow("Arka plan seti", self.background)
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        form.addRow("Fare ile", self.mode)
        self.z = _spin(0.0, -1e5, 1e5, 1.0, 2)
        self.drape = QComboBox()
        self.drape.addItem("(sabit kot)")
        self.drape.addItems(surfaces.surface_names(project))
        form.addRow("Kot (m) / yüzeye oturt", _row(self.z, self.drape))
        buttons = [
            ("Yeni çizgi başlat", self.new_line),
            ("Seçili çizgiyi kapat / aç", self.toggle_closed),
            ("Seçili çizgiye kotu uygula", self.apply_z),
            ("Seçili çizgiyi yüzeye oturt", self.drape_current),
            ("Seçili çizgiyi sil", self.delete_line),
            ("Geri al", self.undo),
            ("Görünümü sığdır", self.fit_view),
        ]
        for text, slot in buttons:
            b = QPushButton(text)
            b.clicked.connect(self._guard(slot))
            form.addRow(b)
        self.name = QLineEdit()
        self.name.setPlaceholderText("örn. tasarim_taban")
        save = QPushButton("Kaydet")
        save.clicked.connect(self._guard(self.save))
        form.addRow("Kayıt adı", _row(self.name, save))
        self.status = QLabel(
            "Sol tık: moda göre işlem. Sağ tık: çizgiyi bitir. Yakınlaştırma için araç "
            "çubuğunu kullanın (araç seçiliyken düzenleme yapılmaz)."
        )
        self.status.setWordWrap(True)
        form.addRow(self.status)
        right = QVBoxLayout()
        self.figure = Figure(figsize=(8, 7))
        self.canvas = FigureCanvasQTAgg(self.figure)  # type: ignore[no-untyped-call]
        self.toolbar = NavigationToolbar2QT(self.canvas, self)  # type: ignore[no-untyped-call]
        right.addWidget(self.toolbar)
        right.addWidget(self.canvas, 1)
        layout.addLayout(right, 3)
        self.ax = self.figure.add_subplot(111)
        self.lines: list[_Line] = []
        self.current = -1
        self.history: list[tuple[list[_Line], int]] = []
        self.drag: tuple[int, int] | None = None
        self.canvas.mpl_connect("button_press_event", self._press)
        self.canvas.mpl_connect("motion_notify_event", self._motion)
        self.canvas.mpl_connect("button_release_event", self._release)
        self.redraw(reset=True)

    def _guard(self, slot: Callable[[], None]) -> Callable[[], None]:
        def run() -> None:
            try:
                slot()
            except Exception as exc:
                QMessageBox.critical(self, "Çizgi düzenleyici", str(exc))

        return run

    # ----- data -----------------------------------------------------------------

    def _remember(self) -> None:
        self.history.append((copy.deepcopy(self.lines), self.current))
        del self.history[:-50]

    def undo(self) -> None:
        if not self.history:
            return
        self.lines, self.current = self.history.pop()
        self.redraw()

    def load(self) -> None:
        if self.set_pick.currentIndex() == 0:
            self.lines, self.current = [], -1
            self.name.clear()
        else:
            name = self.set_pick.currentText()
            self.lines = [
                _Line(s.points, s.closed, s.layer) for s in load_strings(self.project, name)
            ]
            self.current = -1
            self.name.setText(name)
        self.history.clear()
        self.redraw(reset=True)

    def save(self) -> None:
        name = self.name.text().strip()
        if not name:
            raise RuntimeError("Bir kayıt adı yazın.")
        keep = [ln for ln in self.lines if len(ln.points) >= 2]
        if not keep:
            raise RuntimeError("Kaydedilecek çizgi yok (en az iki noktalı çizgi gerekir).")
        save_strings(
            self.project,
            name,
            [String(ln.points, ln.closed and len(ln.points) >= 3, ln.layer) for ln in keep],
        )
        for box in (self.set_pick, self.background):
            if box.findText(name) < 0:
                box.addItem(name)
        self.status.setText(f"'{name}' kaydedildi: {len(keep)} çizgi.")

    def _z_at(self, xy: np.ndarray) -> np.ndarray:
        if self.drape.currentIndex() <= 0:
            return np.full(len(xy), self.z.value())
        surface = surfaces.load_surface(self.project, self.drape.currentText())
        return surface.elevation(np.asarray(xy, np.float64).reshape(-1, 2))[0]

    def new_line(self) -> None:
        self.current = -1
        self.redraw()

    def _need_current(self) -> _Line:
        if not 0 <= self.current < len(self.lines):
            raise RuntimeError("Önce 'Çizgi seç' moduyla bir çizgi seçin.")
        return self.lines[self.current]

    def toggle_closed(self) -> None:
        line = self._need_current()
        self._remember()
        line.closed = not line.closed
        self.redraw()

    def apply_z(self) -> None:
        line = self._need_current()
        self._remember()
        line.points[:, 2] = self.z.value()
        self.redraw()

    def drape_current(self) -> None:
        line = self._need_current()
        if self.drape.currentIndex() <= 0:
            raise RuntimeError("Önce bir yüzey seçin.")
        self._remember()
        line.points[:, 2] = self._z_at(line.points[:, :2])
        self.redraw()

    def delete_line(self) -> None:
        self._need_current()
        self._remember()
        del self.lines[self.current]
        self.current = -1
        self.redraw()

    # ----- editing operations (data coordinates; also used by tests) ---------------

    def add_point(self, x: float, y: float) -> None:
        self._remember()
        z = float(self._z_at(np.array([[x, y]]))[0])
        if not 0 <= self.current < len(self.lines):
            self.lines.append(_Line([[x, y, z]], closed=False))
            self.current = len(self.lines) - 1
        else:
            line = self.lines[self.current]
            line.points = np.vstack([line.points, [x, y, z]])
        self.redraw()

    def move_point(self, line: int, vertex: int, x: float, y: float) -> None:
        pts = self.lines[line].points
        pts[vertex, :2] = (x, y)
        if self.drape.currentIndex() > 0:
            pts[vertex, 2] = float(self._z_at(np.array([[x, y]]))[0])

    def delete_point(self, line: int, vertex: int) -> None:
        self._remember()
        ln = self.lines[line]
        ln.points = np.delete(ln.points, vertex, axis=0)
        if len(ln.points) == 0:
            del self.lines[line]
            self.current = -1
        self.redraw()

    def insert_point(self, line: int, segment: int, x: float, y: float) -> None:
        """Insert after vertex ``segment`` (z interpolated along the segment unless draped)."""
        self._remember()
        ln = self.lines[line]
        a = ln.points[segment]
        b = ln.points[(segment + 1) % len(ln.points)]
        ab = b[:2] - a[:2]
        t = float(np.clip(np.dot([x, y] - a[:2], ab) / max(np.dot(ab, ab), 1e-12), 0.0, 1.0))
        z = a[2] + t * (b[2] - a[2])
        if self.drape.currentIndex() > 0:
            z = float(self._z_at(np.array([[x, y]]))[0])
        ln.points = np.insert(ln.points, segment + 1, [x, y, z], axis=0)
        self.current = line
        self.redraw()

    # ----- picking ---------------------------------------------------------------

    def _nearest_vertex(self, event: MouseEvent) -> tuple[int, int] | None:
        best, where = PICK_PX, None
        for i, ln in enumerate(self.lines):
            if not len(ln.points):
                continue
            px = self.ax.transData.transform(ln.points[:, :2])
            d = np.hypot(px[:, 0] - event.x, px[:, 1] - event.y)
            k = int(np.argmin(d))
            if d[k] < best:
                best, where = float(d[k]), (i, k)
        return where

    def _nearest_segment(self, event: MouseEvent) -> tuple[int, int] | None:
        best, where = PICK_PX, None
        p = np.array([event.x, event.y], np.float64)
        for i, ln in enumerate(self.lines):
            n = len(ln.points)
            if n < 2:
                continue
            px = self.ax.transData.transform(ln.points[:, :2])
            count = n if ln.closed else n - 1
            for k in range(count):
                a, b = px[k], px[(k + 1) % n]
                ab = b - a
                t = np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-12), 0.0, 1.0)
                d = float(np.hypot(*(a + t * ab - p)))
                if d < best:
                    best, where = d, (i, k)
        return where

    def _press(self, event: MouseEvent) -> None:
        if event.inaxes is not self.ax or self.toolbar.mode or event.xdata is None:
            return
        if event.button == MouseButton.RIGHT:
            self.new_line()
            return
        x, y = float(event.xdata), float(event.ydata)  # type: ignore[arg-type]
        mode = self.mode.currentIndex()
        try:
            if mode == 0:
                self.add_point(x, y)
            elif mode == 1:
                hit = self._nearest_vertex(event)
                if hit is not None:
                    self._remember()
                    self.drag, self.current = hit, hit[0]
            elif mode == 2:
                seg = self._nearest_segment(event)
                if seg is not None:
                    self.insert_point(seg[0], seg[1], x, y)
            elif mode == 3:
                hit = self._nearest_vertex(event)
                if hit is not None:
                    self.delete_point(*hit)
            else:
                hit = self._nearest_vertex(event) or self._nearest_segment(event)
                self.current = hit[0] if hit is not None else -1
                self.redraw()
        except Exception as exc:
            QMessageBox.critical(self, "Çizgi düzenleyici", str(exc))

    def _motion(self, event: MouseEvent) -> None:
        if self.drag is None or event.inaxes is not self.ax or event.xdata is None:
            return
        self.move_point(*self.drag, float(event.xdata), float(event.ydata))  # type: ignore[arg-type]
        self.redraw()

    def _release(self, _event: MouseEvent) -> None:
        self.drag = None

    # ----- drawing ---------------------------------------------------------------

    def _extent(self) -> tuple[float, float, float, float]:
        """Plan box of the strings, else of the project's surfaces, else 0..100."""
        pts = [ln.points[:, :2] for ln in self.lines if len(ln.points)]
        if self.background.currentIndex() > 0:
            pts += [
                s.points[:, :2] for s in load_strings(self.project, self.background.currentText())
            ]
        if not pts:
            pts = [
                surfaces.load_surface(self.project, n).points[:, :2]
                for n in surfaces.surface_names(self.project)
            ]
        if not pts:
            return (0.0, 0.0, 100.0, 100.0)
        xy = np.vstack(pts)
        lo, hi = xy.min(axis=0), xy.max(axis=0)
        pad = max(float((hi - lo).max()) * 0.05, 10.0)
        return (lo[0] - pad, lo[1] - pad, hi[0] + pad, hi[1] + pad)

    def fit_view(self) -> None:
        box = self._extent()
        self.ax.set_xlim(box[0], box[2])
        self.ax.set_ylim(box[1], box[3])
        self.redraw()

    def redraw(self, reset: bool = False) -> None:
        ax = self.ax
        limits = None if reset else (ax.get_xlim(), ax.get_ylim())
        ax.clear()
        bg = self.background.currentText() if self.background.currentIndex() > 0 else ""
        drawn = False
        if bg:
            for s in load_strings(self.project, bg):
                pts = np.vstack([s.points, s.points[:1]]) if s.closed else s.points
                ax.plot(pts[:, 0], pts[:, 1], color="#9e9e9e", lw=0.8)
                drawn = True
        for i, ln in enumerate(self.lines):
            pts = ln.points
            if not len(pts):
                continue
            show = np.vstack([pts, pts[:1]]) if ln.closed and len(pts) >= 3 else pts
            selected = i == self.current
            ax.plot(
                show[:, 0],
                show[:, 1],
                "-o",
                color="#d84315" if selected else "#1565c0",
                lw=1.8 if selected else 1.0,
                ms=4 if selected else 2.5,
            )
            drawn = True
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(alpha=0.3)
        ax.set_xlabel("Doğu (m)")
        ax.set_ylabel("Kuzey (m)")
        if limits is not None and not reset:
            ax.set_xlim(*limits[0])
            ax.set_ylim(*limits[1])
        elif not drawn:
            box = self._extent()
            ax.set_xlim(box[0], box[2])
            ax.set_ylim(box[1], box[3])
        n = len(self.lines[self.current].points) if 0 <= self.current < len(self.lines) else 0
        ax.set_title(f"{len(self.lines)} çizgi" + (f", seçili çizgi {n} nokta" if n else ""))
        self.canvas.draw_idle()  # type: ignore[no-untyped-call]
