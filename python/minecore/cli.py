"""Headless command-line interface.

Every engineering capability gets a CLI entry point so it can be run and
tested without the desktop workbench.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
from collections.abc import Sequence
from importlib.metadata import version
from typing import Any, TextIO

from minecore import _core, cli_modelling, coords
from minecore._core import MineCoreError
from minecore.jobs import CancelledError
from minecore.project import Project

EXIT_CANCELLED = 130


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

    hist = project_cmds.add_parser("history", help="list the operation log")
    hist.add_argument("path", help="package directory")
    hist.add_argument("--json", action="store_true", help="print machine-readable JSON")
    for action in ("undo", "redo"):
        step = project_cmds.add_parser(action, help=f"{action} the last operation")
        step.add_argument("path", help="package directory")

    set_crs = project_cmds.add_parser("set-crs", help="set a projected working CRS")
    set_crs.add_argument("path", help="package directory")
    target = set_crs.add_mutually_exclusive_group(required=True)
    target.add_argument("--crs", help="projected CRS in metres, e.g. EPSG:5254")
    target.add_argument("--unreferenced", metavar="DESCRIPTION", help="no georeference")

    set_grid = project_cmds.add_parser("set-local-grid", help="set a local mine grid")
    set_grid.add_argument("path", help="package directory")
    set_grid.add_argument("--name", required=True, help="grid name")
    set_grid.add_argument("--base", required=True, help="base projected CRS, e.g. EPSG:5254")
    source = set_grid.add_mutually_exclusive_group(required=True)
    source.add_argument("--control", metavar="CSV", help="fit from control points")
    source.add_argument(
        "--params",
        nargs=5,
        type=float,
        metavar=("E0", "N0", "X0", "Y0", "ROTATION_DEG"),
        help="base origin, local origin and rotation (azimuth of local north)",
    )
    set_grid.add_argument("--scale", type=float, help="scale factor (fixed when fitting)")
    set_grid.add_argument("--z-offset", type=float, help="local z = base Z + offset")

    crs = commands.add_parser("crs", help="inspect coordinate reference systems")
    crs_cmds = crs.add_subparsers(dest="crs_command", metavar="ACTION", required=True)
    crs_info = crs_cmds.add_parser("info", help="describe a CRS")
    crs_info.add_argument("crs", help="EPSG code, WKT, PROJJSON or PROJ string")
    crs_info.add_argument("--json", action="store_true", help="print machine-readable JSON")

    grid = commands.add_parser("grid", help="local mine grid tools")
    grid_cmds = grid.add_subparsers(dest="grid_command", metavar="ACTION", required=True)
    fit = grid_cmds.add_parser("fit", help="fit a local grid to control points and report")
    fit.add_argument("control", help="CSV: name,base_e,base_n,local_x,local_y[,base_z,local_z]")
    fit.add_argument("--scale", type=float, help="hold the scale (1 = rigid)")
    fit.add_argument("--z-offset", type=float, help="hold the vertical offset")
    fit.add_argument("--json", action="store_true", help="print machine-readable JSON")
    cli_modelling.add_parsers(commands)
    return parser


def _dump(out: TextIO, data: object) -> None:
    json.dump(data, out, indent=2, sort_keys=True, ensure_ascii=False)
    out.write("\n")


def _write_fit(out: TextIO, report: dict[str, Any]) -> None:
    p = report["parameters"]
    out.write(f"base origin  : {p['base_origin'][0]:.4f} {p['base_origin'][1]:.4f}\n")
    out.write(f"local origin : {p['local_origin'][0]:.4f} {p['local_origin'][1]:.4f}\n")
    out.write(f"rotation     : {p['rotation_deg']:.8f} deg (azimuth of local north)\n")
    out.write(f"scale        : {p['scale']:.10f}\n")
    out.write(f"z offset     : {p['z_offset']:.4f}\n")
    sigma0 = report["sigma0_horizontal_m"]
    out.write(
        f"horizontal   : rms {report['rms_horizontal_m']:.4f} m, max "
        f"{report['max_horizontal_m']:.4f} m, sigma0 "
        f"{'n/a (no redundancy)' if sigma0 is None else f'{sigma0:.4f} m'}\n"
    )
    if report["rms_vertical_m"] is not None:
        out.write(f"vertical     : rms {report['rms_vertical_m']:.4f} m\n")
    out.write(f"{'point':16} {'dx':>10} {'dy':>10} {'dz':>10}\n")
    for r in report["residuals"]:
        dz = "" if r["dz"] is None else f"{r['dz']:.4f}"
        out.write(f"{r['name']:16} {r['dx']:10.4f} {r['dy']:10.4f} {dz:>10}\n")


def _crs_command(args: argparse.Namespace, out: TextIO) -> int:
    crs = coords.CrsDefinition.from_user_input(args.crs)
    try:
        coords.require_working_crs(crs)
        usable = "yes"
    except coords.CrsError as exc:
        usable = f"no: {exc}"
    area = crs.area_of_use()
    summary: dict[str, Any] = {
        "name": crs.name,
        "authority": crs.authority,
        "kind": crs.kind.value,
        "area_of_use": None if area is None else list(area),
        "axes": [f"{a.direction} ({a.unit_name})" for a in crs.to_pyproj().axis_info],
        "working_crs": usable,
    }
    if args.json:
        _dump(out, summary)
    else:
        for key, value in summary.items():
            out.write(f"{key:12}: {value}\n")
    return 0


def _grid_command(args: argparse.Namespace, out: TextIO) -> int:
    points = coords.read_control_points(args.control)
    fit = coords.fit_control_points(points, scale=args.scale, z_offset=args.z_offset)
    report = coords.fit_report(points, fit)
    if args.json:
        _dump(out, report)
    else:
        _write_fit(out, report)
    return 0


def _set_local_grid(args: argparse.Namespace, out: TextIO) -> coords.WorkingSystem:
    base = coords.CrsDefinition.from_user_input(args.base)
    if args.control is not None:
        points = coords.read_control_points(args.control)
        fit = coords.fit_control_points(points, scale=args.scale, z_offset=args.z_offset)
        _write_fit(out, coords.fit_report(points, fit))
        grid = fit.grid
    else:
        e0, n0, x0, y0, rotation = args.params
        grid = coords.LocalGrid(
            base_origin=(e0, n0),
            local_origin=(x0, y0),
            rotation_deg=rotation,
            scale=1.0 if args.scale is None else args.scale,
            z_offset=0.0 if args.z_offset is None else args.z_offset,
        )
    return coords.LocalGridSystem(args.name, base, grid)


def _history_command(args: argparse.Namespace, out: TextIO) -> int:
    if args.project_command in ("undo", "redo"):
        with Project.open(args.path) as opened:
            done = opened.undo() if args.project_command == "undo" else opened.redo()
        out.write(
            f"nothing to {args.project_command}\n"
            if done is None
            else f"{args.project_command}: #{done.seq} {done.description}\n"
        )
        return 0
    if args.project_command == "history":
        with Project.open(args.path, read_only=True) as opened:
            records = opened.history()
        if args.json:
            _dump(out, [dataclasses.asdict(r) for r in records])
        else:
            for r in records:
                mark = " (undone)" if r.state == "undone" else ""
                out.write(f"#{r.seq:<4} {r.utc}  {r.user_name:12} {r.description}{mark}\n")
        return 0
    raise AssertionError(args.project_command)


def _project_command(args: argparse.Namespace, out: TextIO) -> int:
    if args.project_command == "create":
        with Project.create(args.path, args.name) as created:
            out.write(f"created {created.path} ({created.info.project_id})\n")
        return 0
    if args.project_command in ("undo", "redo", "history"):
        return _history_command(args, out)
    if args.project_command in ("set-crs", "set-local-grid"):
        system: coords.WorkingSystem
        if args.project_command == "set-local-grid":
            system = _set_local_grid(args, out)
        elif args.crs is not None:
            system = coords.ProjectedSystem(coords.CrsDefinition.from_user_input(args.crs))
        else:
            system = coords.UnreferencedSystem(args.unreferenced)
        with Project.open(args.path) as opened:
            opened.set_coordinate_system(system)
        out.write(f"coordinate system: {system.describe()}\n")
        return 0
    with Project.open(args.path, read_only=True) as opened:
        info = opened.info
        system_now = opened.coordinate_system()
        summary = {
            "project_id": info.project_id,
            "name": info.name,
            "created_utc": info.created_utc,
            "created_by": info.created_by,
            "schema_version": str(info.schema_version),
            "objects": str(len(opened.objects())),
            "coordinates": "not set" if system_now is None else system_now.describe(),
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
    handlers = {
        "project": _project_command,
        "crs": _crs_command,
        "grid": _grid_command,
        **cli_modelling.HANDLERS,
    }
    if args.command in handlers:
        try:
            return handlers[args.command](args, stream)
        except (CancelledError, KeyboardInterrupt):
            sys.stderr.write("cancelled; nothing was changed\n")
            return EXIT_CANCELLED
        except MineCoreError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
    parser.print_help(stream)
    return 2
