"""The installation self-test passes on a correct installation."""

from __future__ import annotations

from pathlib import Path

from minecore.selftest import run


def test_self_test_passes(tmp_path: Path) -> None:
    report = tmp_path / "r.txt"
    assert run(report) == 0, report.read_text()
    text = report.read_text()
    assert text.startswith("MineCore")
    assert "OK" in text.splitlines()[0]
    assert "FAIL" not in text
