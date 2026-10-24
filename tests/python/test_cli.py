"""Smoke tests for the headless CLI and the Rust-Python bridge."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from importlib.metadata import version

import pytest

import minecore
from minecore import cli


def test_core_engine_is_reachable_from_python() -> None:
    assert minecore.core_version() == version("minecore")
    assert minecore.core_build_profile() in {"debug", "release"}


def test_info_json_reports_matching_versions() -> None:
    out = io.StringIO()
    assert cli.main(["info", "--json"], out=out) == 0
    info = json.loads(out.getvalue())
    assert set(info) == {"minecore", "core_engine", "core_build_profile", "python", "platform"}
    assert info["minecore"] == info["core_engine"]


def test_info_text_output() -> None:
    out = io.StringIO()
    assert cli.main(["info"], out=out) == 0
    text = out.getvalue()
    assert text.startswith(f"MineCore {minecore.__version__}\n")
    assert "core engine" in text


def test_missing_command_prints_help_and_fails() -> None:
    out = io.StringIO()
    assert cli.main([], out=out) == 2
    assert "usage: minecore" in out.getvalue()


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["no-such-command"], out=io.StringIO())
    assert excinfo.value.code == 2


def test_module_entry_point_runs_in_subprocess() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "minecore", "info", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["core_engine"] == minecore.__version__
