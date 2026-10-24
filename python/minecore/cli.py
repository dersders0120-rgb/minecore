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
from minecore._core import MineCoreError
from minecore.project import Project


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

    project = commands.add_parser("project", help="create or inspect project packages")
    project_cmds = project.add_subparsers(dest="project_command", metavar="ACTION", required=True)
    create = project_cmds.add_parser("create", help="create a new .mcproj package")
    create.add_argument("path", help="package directory, ending in .mcproj")
    create.add_argument("--name", required=True, help="project name")
    show = project_cmds.add_parser("info", help="show project identity and catalogue size")
    show.add_argument("path", help="package directory")
    show.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


def _project_command(args: argparse.Namespace, out: TextIO) -> int:
    if args.project_command == "create":
        with Project.create(args.path, args.name) as created:
            out.write(f"created {created.path} ({created.info.project_id})\n")
        return 0
    with Project.open(args.path, read_only=True) as opened:
        info = opened.info
        summary = {
            "project_id": info.project_id,
            "name": info.name,
            "created_utc": info.created_utc,
            "created_by": info.created_by,
            "schema_version": str(info.schema_version),
            "objects": str(len(opened.objects())),
        }
    if args.json:
        json.dump(summary, out, indent=2, sort_keys=True)
        out.write("\n")
    else:
        for key, value in summary.items():
            out.write(f"{key:15}: {value}\n")
    return 0


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the CLI and return the process exit code."""
    stream = sys.stdout if out is None else out
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "info":
        _write_info(stream, as_json=args.json)
        return 0
    if args.command == "project":
        try:
            return _project_command(args, stream)
        except MineCoreError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
    parser.print_help(stream)
    return 2
