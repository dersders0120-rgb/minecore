"""Layout helpers that keep tall dialogs usable on laptop screens."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QBoxLayout, QFrame, QLayout, QScrollArea, QWidget


class _PanelScroll(QScrollArea):
    """Vertical scroll area as wide as its content, so forms are never squeezed."""

    def _width(self) -> int:
        inner = self.widget()
        bar = self.verticalScrollBar().sizeHint().width()
        return (inner.sizeHint().width() if inner is not None else 0) + bar + 4

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._width(), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._width(), 120)


def scroll_panel(parent: QBoxLayout, panel: QLayout, stretch: int = 0) -> QScrollArea:
    """Add ``panel`` to ``parent`` inside a vertical scroll area."""
    holder = QWidget()
    holder.setLayout(panel)
    area = _PanelScroll()
    area.setWidget(holder)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    parent.addWidget(area, stretch)
    return area


def fit_to_screen(widget: QWidget, width: int, height: int) -> None:
    """Resize to ``width`` x ``height`` but never beyond the screen's free area."""
    screen = widget.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        widget.resize(width, height)
        return
    free = screen.availableGeometry()
    widget.resize(min(width, free.width() - 40), min(height, free.height() - 60))
