"""Headless command-line interface.

Every engineering capability gets a CLI entry point so it can be run and
tested without the desktop workbench.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from importlib.metadata import version
from typing import TextIO

from minecore import _core


def environment_info() -> dict[str, str]:
    """Describe the running MineCore build and host environment."""
    return {
        "minecore": version("minecore"),
        "core_engine": _core.core_version(),
        "core_build_profile": _core.core_build_profile(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _write_info(out: TextIO, *, as_json: bool) -> None:
    info = environment_info()
    if as_json:
        json.dump(info, out, indent=2, sort_keys=True)
        out.write("\n")
        return
    out.write(f"MineCore {info['minecore']}\n")
    out.write(f"  core engine : {info['core_engine']} ({info['core_build_profile']})\n")
    out.write(f"  python      : {info['python']}\n")
    out.write(f"  platform    : {info['platform']}\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="minecore",
        description="MineCore mining engineering platform (headless interface).",
    )
    parser.add_argument("--version", action="version", version=f"MineCore {_core.core_version()}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    info = commands.add_parser("info", help="show build and environment information")
    info.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the CLI and return the process exit code."""
    stream = sys.stdout if out is None else out
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "info":
        _write_info(stream, as_json=args.json)
        return 0
    parser.print_help(stream)
    return 2
