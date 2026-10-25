"""Creating and opening projects from the main window (offscreen Qt)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog

from minecore.gui.app import MainWindow, find_projects, new_project_path


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


def test_new_project_path_is_a_safe_package_name(tmp_path: Path) -> None:
    assert new_project_path(tmp_path, "Kışla Au") == tmp_path / "Kışla Au.mcproj"
    assert new_project_path(tmp_path, ' a:b/c?"d ') == tmp_path / "a_b_c__d.mcproj"
    assert new_project_path(tmp_path, "deneme.mcproj") == tmp_path / "deneme.mcproj"
    assert new_project_path(tmp_path, "...") == tmp_path / "Proje.mcproj"


def test_find_projects_from_package_inside_or_parent(tmp_path: Path) -> None:
    a = tmp_path / "a.mcproj"
    b = tmp_path / "b.mcproj"
    a.mkdir()
    b.mkdir()
    (a / "tables").mkdir()
    (tmp_path / "not.mcproj").write_text("file, not a package")
    assert find_projects(a) == [a]
    assert find_projects(a / "tables") == [a]
    assert find_projects(tmp_path) == [a, b]
    assert find_projects(tmp_path / "empty_dir_missing") == []


def test_new_then_open_from_parent_folder(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Deneme Sahası", True))
    window = MainWindow()
    window.new_project()
    assert window.project is not None
    assert window.project.info.name == "Deneme Sahası"
    assert (tmp_path / "Deneme Sahası.mcproj").is_dir()

    with pytest.raises(RuntimeError, match="zaten var"):
        window.new_project()

    window.project.close()
    window.project = None
    window.open_project()  # the user picks the folder that holds the package
    assert window.project is not None
    assert window.project.info.name == "Deneme Sahası"
    window.project.close()


def test_open_project_without_any_package_explains(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    with pytest.raises(RuntimeError, match=r"\.mcproj\) yok"):
        MainWindow().open_project()
    assert not any(tmp_path.iterdir())


def test_warm_up_leaves_an_empty_ready_window(qapp: QApplication) -> None:
    window = MainWindow()
    window.warm_up()  # offscreen: no 3D view, must be a quiet no-op
    window.warm_up()
    assert window.project is None


def test_application_icon_is_shipped_and_loads(qapp: QApplication) -> None:
    from PySide6.QtGui import QIcon  # noqa: PLC0415

    from minecore.gui.app import ICON_PATH  # noqa: PLC0415

    assert ICON_PATH.is_file()
    assert ICON_PATH.with_suffix(".ico").is_file()
    assert not QIcon(str(ICON_PATH)).isNull()


def test_tall_dialog_panel_scrolls_and_fits_the_screen(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget  # noqa: PLC0415

    from minecore.gui.layout import fit_to_screen, scroll_panel  # noqa: PLC0415

    dialog = QWidget()
    outer = QHBoxLayout(dialog)
    panel = QVBoxLayout()
    for i in range(80):
        panel.addWidget(QLabel(f"satır {i} uzun bir form etiketi"))
    area = scroll_panel(outer, panel)
    fit_to_screen(dialog, 5000, 5000)
    screen = dialog.screen().availableGeometry()
    assert dialog.width() <= screen.width()
    assert dialog.height() <= screen.height()
    dialog.show()
    qapp.processEvents()
    assert area.verticalScrollBar().maximum() > 0  # the rest of the form is reachable
    assert area.width() >= panel.sizeHint().width()


def test_user_messages_show_without_traceback(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(("w", a[2])))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append(("c", a[2])))
    window = MainWindow()

    def user_error() -> None:
        raise RuntimeError("Önce bir model kaydedin.")

    def bug() -> None:
        raise ValueError("boom")

    window._guard(user_error)
    window._guard(bug)
    assert shown[0] == ("w", "Önce bir model kaydedin.")
    assert shown[1][0] == "c"
    assert "Traceback" in shown[1][1]


def test_free_name_and_duplicate_object_message(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

    from minecore.gui.app import free_name  # noqa: PLC0415
    from minecore.project import ProjectError  # noqa: PLC0415

    assert free_name("BM", set()) == "BM_1"
    assert free_name("BM", {"BM_1", "BM_2"}) == "BM_3"
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))

    def duplicate() -> None:
        raise ProjectError(
            "cannot register block_model 'BM_1': UNIQUE constraint failed: "
            "objects.kind, objects.name"
        )

    MainWindow()._guard(duplicate)
    assert shown == ["Bu adla bir nesne zaten var. Başka bir ad seçin."]
