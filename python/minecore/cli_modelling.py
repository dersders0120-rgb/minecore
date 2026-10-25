"""CLI commands for the modelling chain: drillholes, surfaces, block models, pits.

Each command calls the same public API as the workbench, so the whole chain
from CSV files to a pit schedule runs headless:

    minecore drillholes import P.mcproj FOLDER
    minecore drillholes composite P.mcproj assay AU_PPM --length 2
    minecore surface from-collars P.mcproj topo
    minecore bm create P.mcproj BM --source assay_komp_2m/AU_PPM --size 10 10 5
    minecore surface flag P.mcproj topo BM
    minecore bm variogram P.mcproj assay_komp_2m/AU_PPM --lag 15 --save au_vg
    minecore bm estimate P.mcproj BM assay_komp_2m/AU_PPM au_ok --method ok --variogram au_vg
    minecore bm report P.mcproj BM au_ok --cutoffs 0 0.2 0.5 1
    minecore pit optimise P.mcproj BM au_ok --price 2000 --price-unit ozt ...
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from minecore import strings, surfaces
from minecore.blockmodel import BlockModel, BlockModelError, Grid
from minecore.blockmodel.calc import calculate
from minecore.blockmodel.classify import Criteria, classify
from minecore.blockmodel.importer import import_block_csv, parse_units
from minecore.blockmodel.reblock import reblock
from minecore.blockmodel.validation import swath
from minecore.blockmodel.workflow import (
    estimate_blocks,
    interval_samples,
    load_variograms,
    save_variogram,
)
from minecore.drillholes import DrillholeDatabase, Severity, validate
from minecore.drillholes.importer import import_folder
from minecore.drillholes.workflow import composite_table, hole_geometry, save_composites
from minecore.estimation import Ellipsoid, Method, Search, experimental, fit_spherical
from minecore.geology.implicit import interval_contacts, model_domain
from minecore.pitopt import Economics, PriceUnit, SlopeProfile, optimise_pit, pushbacks, schedule
from minecore.pitopt.benchdesign import BenchGeometry, Ramp, design_from_model, parse_sectors
from minecore.pitopt.cutoff import cutoffs_from_model
from minecore.pitopt.design import edge_blocks, export_pit, period_surfaces
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import Schedule, store
from minecore.pitopt.scheduling import ConstrainedSchedule, Limits, schedule_constrained
from minecore.pitopt.width import apply_mining_width
from minecore.plots import SectionSpec, section_plot
from minecore.project import Project
from minecore.solids import flag_blocks, load_solid, read_solid, save_solid
from minecore.underground.development import DeclineDesign, development_from_model
from minecore.underground.stopes import stopes_from_model

METHODS = {"nn": Method.NEAREST, "idw": Method.INVERSE_DISTANCE, "ok": Method.ORDINARY_KRIGING}


def add_parsers(commands: Any) -> None:  # noqa: PLR0915 - argument tables
    dh = commands.add_parser("drillholes", help="import, validate and composite drillholes")
    dh_cmds = dh.add_subparsers(dest="dh_command", metavar="ACTION", required=True)
    imp = dh_cmds.add_parser("import", help="import collar/survey/interval files from a folder")
    imp.add_argument("project")
    imp.add_argument("folder")
    val = dh_cmds.add_parser("validate", help="print the validation summary")
    val.add_argument("project")
    comp = dh_cmds.add_parser("composite", help="fixed-length composites of numeric fields")
    comp.add_argument("project")
    comp.add_argument("table")
    comp.add_argument("fields", nargs="+")
    comp.add_argument("--length", type=float, default=2.0)
    comp.add_argument("--domain")
    comp.add_argument("--min-coverage", type=float, default=0.5)

    sf = commands.add_parser("surface", help="topography surfaces")
    sf_cmds = sf.add_subparsers(dest="sf_command", metavar="ACTION", required=True)
    s_imp = sf_cmds.add_parser("import", help="x, y, z points file")
    s_imp.add_argument("project")
    s_imp.add_argument("name")
    s_imp.add_argument("file")
    s_col = sf_cmds.add_parser("from-collars", help="surface through the collars")
    s_col.add_argument("project")
    s_col.add_argument("name")
    s_flag = sf_cmds.add_parser("flag", help="write kaya=1 below the surface into a block model")
    s_flag.add_argument("project")
    s_flag.add_argument("surface")
    s_flag.add_argument("model")

    stg = commands.add_parser("strings", help="polylines and section linking")
    stg_cmds = stg.add_subparsers(dest="st_command", metavar="ACTION", required=True)
    st_imp = stg_cmds.add_parser("import", help="DXF LWPOLYLINE/POLYLINE")
    st_imp.add_argument("project")
    st_imp.add_argument("name")
    st_imp.add_argument("file")
    st_link = stg_cmds.add_parser("link", help="link closed section outlines into a solid")
    st_link.add_argument("project")
    st_link.add_argument("name")
    st_link.add_argument("solid")

    sol = commands.add_parser("solid", help="wireframe solids: import, flag blocks")
    sol_cmds = sol.add_subparsers(dest="solid_command", metavar="ACTION", required=True)
    so_imp = sol_cmds.add_parser("import", help="OBJ, STL or DXF 3DFACE")
    so_imp.add_argument("project")
    so_imp.add_argument("name")
    so_imp.add_argument("file")
    so_flag = sol_cmds.add_parser("flag", help="dom_<solid> (and _pct fractions) in a model")
    so_flag.add_argument("project")
    so_flag.add_argument("solid")
    so_flag.add_argument("model")
    so_flag.add_argument("--subdivisions", type=int, default=1, help="partial blocks: n^3 samples")

    geo = commands.add_parser("geo", help="implicit domain modelling")
    geo_cmds = geo.add_subparsers(dest="geo_command", metavar="ACTION", required=True)
    imp_dom = geo_cmds.add_parser("implicit", help="domain solid from drillhole intervals (RBF)")
    imp_dom.add_argument("project")
    imp_dom.add_argument("source", help="table/field")
    imp_dom.add_argument("model", help="block model giving the grid")
    imp_dom.add_argument("name")
    imp_dom.add_argument("--threshold", type=float, help="numeric field: inside if >= threshold")
    imp_dom.add_argument("--codes", nargs="+", default=[], help="category field: inside codes")
    imp_dom.add_argument("--max-distance", type=float, default=10.0)
    imp_dom.add_argument("--smoothing", type=float, default=0.0)
    imp_dom.add_argument(
        "--anisotropy",
        type=float,
        nargs=6,
        metavar=("MAJOR", "SEMI", "MINOR", "AZ", "DIP", "RAKE"),
    )

    plot = commands.add_parser("plot", help="printable section/plan plots (PDF, PNG, SVG)")
    plot.add_argument("project")
    plot.add_argument("out", help="output file: .pdf, .png or .svg")
    plot.add_argument("--axis", choices=["x", "y", "z"], required=True, help="x: N-S section")
    plot.add_argument("--at", type=float, required=True, help="plane coordinate, m")
    plot.add_argument("--width", type=float, default=20.0, help="slab thickness, m")
    plot.add_argument("--model")
    plot.add_argument("--attribute")
    plot.add_argument("--drill", help="table/field of samples to colour")
    plot.add_argument("--surface", action="append", default=[])
    plot.add_argument("--solid", action="append", default=[])
    plot.add_argument("--title", default="")

    bm = commands.add_parser("bm", help="block models, variograms, estimation, reports")
    bm_cmds = bm.add_subparsers(dest="bm_command", metavar="ACTION", required=True)
    create = bm_cmds.add_parser("create", help="regular model covering a data table")
    create.add_argument("project")
    create.add_argument("name")
    create.add_argument("--source", required=True, help="table/field to cover")
    create.add_argument("--size", type=float, nargs=3, required=True, metavar=("DX", "DY", "DZ"))
    create.add_argument("--padding", type=float, default=0.0)
    bimp = bm_cmds.add_parser("import", help="block model from CSV (centroids, attributes)")
    bimp.add_argument("project")
    bimp.add_argument("name")
    bimp.add_argument("file")
    bimp.add_argument("--units", default="", help="e.g. 'au=g/t; cu=%%'")
    vg = bm_cmds.add_parser("variogram", help="omnidirectional variogram and spherical fit")
    vg.add_argument("project")
    vg.add_argument("source", help="table/field")
    vg.add_argument("--lag", type=float, required=True)
    vg.add_argument("--nlags", type=int, default=15)
    vg.add_argument("--save", help="store the fitted model under this name")
    est = bm_cmds.add_parser("estimate", help="estimate an attribute")
    est.add_argument("project")
    est.add_argument("model")
    est.add_argument("source", help="table/field")
    est.add_argument("attribute")
    est.add_argument("--method", choices=sorted(METHODS), default="ok")
    est.add_argument("--variogram")
    est.add_argument("--radius", type=float, nargs="+", default=[100.0], help="1 or 3 ranges")
    est.add_argument("--angles", type=float, nargs=3, default=[0.0, 0.0, 0.0])
    est.add_argument("--min", type=int, default=4)
    est.add_argument("--max", type=int, default=16)
    est.add_argument("--per-hole", type=int, default=0)
    est.add_argument("--power", type=float, default=2.0)
    est.add_argument("--rock-only", action="store_true", help="only blocks with kaya=1")
    est.add_argument("--top-cut", type=float, help="cap sample values before estimation")
    sw = bm_cmds.add_parser("swath", help="swath plot: block estimate vs samples per slice")
    sw.add_argument("project")
    sw.add_argument("model")
    sw.add_argument("attribute")
    sw.add_argument("source", help="table/field")
    sw.add_argument("--axis", choices=["x", "y", "z"], default="x")
    sw.add_argument("--width", type=float, required=True, help="slice width, m")
    sw.add_argument("--json", action="store_true")
    rep = bm_cmds.add_parser("report", help="grade-tonnage table")
    rep.add_argument("project")
    rep.add_argument("model")
    rep.add_argument("attribute")
    rep.add_argument("--cutoffs", type=float, nargs="+", required=True)
    rep.add_argument("--density", default="2.7", help="t/m3 or a density attribute")
    rep.add_argument("--json", action="store_true")
    rep.add_argument("--filter", nargs=3, metavar=("ATTR", "MIN", "MAX"), help="report subset")
    rep.add_argument("--weight", help="block fraction attribute (e.g. dom_ore_pct)")
    reb = bm_cmds.add_parser("reblock", help="combine blocks into larger blocks")
    reb.add_argument("project")
    reb.add_argument("model")
    reb.add_argument("name")
    reb.add_argument("--factors", type=int, nargs=3, required=True, metavar=("FX", "FY", "FZ"))
    reb.add_argument("--density", default="2.7")
    calc = bm_cmds.add_parser("calc", help="new attribute from an expression")
    calc.add_argument("project")
    calc.add_argument("model")
    calc.add_argument("attribute")
    calc.add_argument("expression")
    calc.add_argument("--unit")
    cls = bm_cmds.add_parser(
        "classify", help="confidence categories (not a JORC/NI 43-101 classification)"
    )
    cls.add_argument("project")
    cls.add_argument("model")
    cls.add_argument("attribute")
    cls.add_argument("--distance", type=float, nargs="+", required=True, help="per category")
    cls.add_argument("--samples", type=int, nargs="+", required=True, help="per category")
    cls.add_argument("--kv", type=float, nargs="+", help="max KV / sill per category (0 = none)")

    ug = commands.add_parser("ug", help="underground: stope envelope and stopes")
    ug.add_argument("project")
    ug.add_argument("model")
    ug.add_argument("grade")
    ug.add_argument("--size", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"))
    ug.add_argument("--cutoff", type=float, required=True)
    ug.add_argument("--density", default="2.7")
    ug.add_argument("--rock")
    ug.add_argument("--dxf")

    dec = commands.add_parser("decline", help="underground decline and level crosscuts")
    dec.add_argument("project")
    dec.add_argument("model")
    dec.add_argument("--portal", type=float, nargs=3, required=True, metavar=("X", "Y", "Z"))
    dec.add_argument("--ore", default="ug_pano", help="ore flag attribute (default ug_pano)")
    dec.add_argument("--levels", type=float, nargs="+", help="level elevations")
    dec.add_argument("--level-interval", type=float, default=25.0)
    dec.add_argument("--gradient", type=float, default=1 / 7)
    dec.add_argument("--radius", type=float, default=25.0)
    dec.add_argument("--standoff", type=float, default=40.0)
    dec.add_argument("--drive", type=float, nargs=2, default=[5.0, 5.5], metavar=("W", "H"))
    dec.add_argument("--density", type=float, default=2.7)
    dec.add_argument("--anticlockwise", action="store_true")
    dec.add_argument("--dxf")
    dec.add_argument("--save", default="dekline", help="store centrelines as a string set")

    pit = commands.add_parser("pit", help="open-pit optimisation")
    pit_cmds = pit.add_subparsers(dest="pit_command", metavar="ACTION", required=True)
    opt = pit_cmds.add_parser("optimise", help="nested shells, pushbacks, schedule")
    opt.add_argument("project")
    opt.add_argument("model")
    opt.add_argument("grade")
    opt.add_argument("--price", type=float)
    opt.add_argument("--price-unit", choices=[u.value for u in PriceUnit])
    opt.add_argument("--recovery", type=float, help="fraction, e.g. 0.9")
    opt.add_argument("--nsr", action="store_true", help="GRADE is an NSR value per tonne")
    opt.add_argument("--mining-cost", type=float, required=True)
    opt.add_argument("--processing-cost", type=float, required=True)
    opt.add_argument("--selling-cost", type=float, default=0.0)
    opt.add_argument("--bench-cost", type=float, default=0.0)
    opt.add_argument("--density", default="2.7")
    opt.add_argument("--mining-recovery", type=float, default=1.0, help="fraction of ore mined")
    opt.add_argument("--dilution", type=float, default=0.0, help="zero-grade dilution fraction")
    opt.add_argument("--slope", type=float, default=45.0)
    opt.add_argument("--revenue-factors", type=float, nargs="+", default=[0.5, 0.75, 1.0, 1.25])
    opt.add_argument("--final-rf", type=float, default=1.0)
    opt.add_argument("--rate", type=float, default=10e6, help="total mining t/year")
    opt.add_argument("--discount", type=float, default=0.08)
    opt.add_argument("--phase-ore", type=float, help="minimum ore tonnes per pushback")
    opt.add_argument("--rock", help="rock/air attribute (kaya)")
    opt.add_argument("--plant", type=float, help="plant capacity, ore t/year (enables limits)")
    opt.add_argument("--lag", type=int, default=2, help="bench lag between pushbacks")
    opt.add_argument("--sinking", type=int, default=6, help="max benches per pushback per year")
    opt.add_argument("--min-width", type=float, default=0.0, help="minimum mining width, m")
    des = pit_cmds.add_parser("design", help="bench design around the optimised pit")
    des.add_argument("project")
    des.add_argument("model")
    des.add_argument("--face", type=float, required=True, help="bench face angle, degrees")
    des.add_argument("--height", type=float, required=True, help="bench height, m")
    des.add_argument("--berm", type=float, required=True, help="berm width, m")
    des.add_argument("--shell", type=int, help="last shell of the pit (default: all)")
    des.add_argument("--topography")
    des.add_argument("--save-surface")
    des.add_argument("--flag", help="attribute for blocks inside the design")
    des.add_argument("--dxf")
    des.add_argument("--ramp-width", type=float, help="haul road width, m (adds a spiral ramp)")
    des.add_argument("--ramp-gradient", type=float, default=0.10, help="e.g. 0.10 for 10 %%")
    des.add_argument("--ramp-exit", type=float, default=0.0, help="exit azimuth, degrees")
    des.add_argument("--ramp-ccw", action="store_true", help="spiral counter-clockwise going down")
    des.add_argument(
        "--sector",
        action="append",
        default=[],
        metavar="AZ:FACE:BERM",
        help="geotechnical sector from azimuth AZ (0 = north wall); repeat for each sector",
    )
    per = pit_cmds.add_parser("periods", help="end-of-period surfaces from the schedule")
    per.add_argument("project")
    per.add_argument("model")
    per.add_argument("--topography")
    per.add_argument("--prefix", default="donem")
    per.add_argument("--dxf")
    exp = pit_cmds.add_parser("export", help="pit outlines, surface and solid to DXF")
    exp.add_argument("project")
    exp.add_argument("model")
    exp.add_argument("dxf")
    exp.add_argument("--shell", type=int, help="last shell of the pit (default: all shells)")
    exp.add_argument("--phases", action="store_true", help="one layer set per pushback")
    exp.add_argument("--topography", help="surface to complete the mined-out surface")
    exp.add_argument("--save-surface", help="store the mined-out surface under this name")
    opt.add_argument("--json", action="store_true")
    cut = pit_cmds.add_parser("cutoff", help="Lane cut-off grades per year inside the pit")
    cut.add_argument("project")
    cut.add_argument("model")
    cut.add_argument("grade")
    cut.add_argument("--price", type=float)
    cut.add_argument("--price-unit", choices=[u.value for u in PriceUnit])
    cut.add_argument("--recovery", type=float)
    cut.add_argument("--nsr", action="store_true", help="GRADE is an NSR value per tonne")
    cut.add_argument("--mining-cost", type=float, required=True)
    cut.add_argument("--processing-cost", type=float, required=True)
    cut.add_argument("--selling-cost", type=float, default=0.0)
    cut.add_argument("--fixed-cost", type=float, default=0.0, help="fixed cost per year")
    cut.add_argument("--mine", type=float, required=True, help="mining capacity, t/year")
    cut.add_argument("--mill", type=float, required=True, help="plant capacity, ore t/year")
    cut.add_argument("--market", type=float, help="sales capacity, metal units/year")
    cut.add_argument("--discount", type=float, default=0.10)
    cut.add_argument("--density", default="2.7")
    cut.add_argument("--shell", type=int, help="last shell of the pit (default: all)")
    cut.add_argument("--rock", help="rock/air attribute (kaya)")
    cut.add_argument("--json", action="store_true")


def _db(project: Project) -> DrillholeDatabase:
    dbs = project.objects("drillhole_db")
    return (
        DrillholeDatabase(project, dbs[0].object_id)
        if dbs
        else DrillholeDatabase.create(project, "Sondajlar")
    )


def _source(text: str) -> tuple[str, str]:
    if "/" not in text:
        raise SystemExit("source must be table/field")
    table, field = text.split("/", 1)
    return table, field


def strings_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        if args.st_command == "import":
            found = strings.read_dxf_strings(Path(args.file))
            strings.save_strings(project, args.name, found)
            out.write(f"{args.name}: {len(found)} strings\n")
            return 0
        ordered = strings.section_order(strings.load_strings(project, args.name))
        solid = strings.link_sections([s.points for s in ordered])
        save_solid(project, args.solid, solid)
        out.write(
            f"{args.solid}: {len(ordered)} sections, volume {solid.volume():,.0f} m3, "
            f"open edges {solid.open_edges()}\n"
        )
    return 0


def solid_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        if args.solid_command == "import":
            solid = read_solid(Path(args.file))
            save_solid(project, args.name, solid)
            out.write(
                f"{args.name}: {len(solid.triangles):,} triangles, volume "
                f"{solid.volume():,.0f} m3, open edges {solid.open_edges()}\n"
            )
            return 0
        model = BlockModel.open(project, args.model)
        attribute = f"dom_{args.solid}"
        n = flag_blocks(model, load_solid(project, args.solid), attribute, args.subdivisions)
        out.write(f"{attribute}: {n:,} blocks inside\n")
        if args.subdivisions > 1:
            pct = model.values(f"{attribute}_pct")
            out.write(f"{attribute}_pct: volume {pct.sum() * model.grid.block_volume:,.0f} m3\n")
    return 0


def geo_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        data = interval_contacts(
            _db(project),
            *_source(args.source),
            threshold=args.threshold,
            codes=tuple(args.codes),
            max_distance=args.max_distance,
        )
        r = model_domain(
            BlockModel.open(project, args.model),
            data,
            args.name,
            anisotropy=Ellipsoid(*args.anisotropy) if args.anisotropy else None,
            smoothing=args.smoothing,
        )
        save_solid(project, args.name, r.solid)
        out.write(
            f"{args.name}: {data.contacts} contacts, volume {r.solid.volume():,.0f} m3, "
            f"open edges {r.solid.open_edges()}, {r.blocks_inside} blocks inside "
            f"(dom_{args.name}, imp_{args.name})\n"
        )
    return 0


def plot_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        spec = SectionSpec(
            "xyz".index(args.axis),
            args.at,
            args.width,
            model=args.model,
            attribute=args.attribute,
            drill=_source(args.drill) if args.drill else None,
            surfaces=tuple(args.surface),
            solids=tuple(args.solid),
            title=args.title,
        )
        r = section_plot(project, spec, Path(args.out))
        out.write(
            f"{args.out}: {r.blocks} blocks, {r.holes} holes, {r.samples} samples, "
            f"surfaces {r.surface_lines}, solids {r.solid_segments}\n"
        )
    return 0


def decline_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        design = DeclineDesign(
            args.gradient,
            args.radius,
            args.standoff,
            args.drive[0],
            args.drive[1],
            args.density,
            not args.anticlockwise,
        )
        dev = development_from_model(
            BlockModel.open(project, args.model),
            (args.portal[0], args.portal[1], args.portal[2]),
            design,
            ore_attribute=args.ore,
            level_interval=args.level_interval,
            levels=args.levels,
            dxf=Path(args.dxf) if args.dxf else None,
            project=project,
            save_as=args.save,
        )
        for a in dev.accesses:
            out.write(f"level {a.elevation:.1f}: crosscut {a.length:,.0f} m\n")
        out.write(
            f"decline {dev.decline_length:,.0f} m, crosscuts {dev.crosscut_length:,.0f} m, "
            f"total {dev.total_length:,.0f} m, {dev.volume:,.0f} m3, {dev.tonnes:,.0f} t\n"
        )
    return 0


def ug_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        r = stopes_from_model(
            BlockModel.open(project, args.model),
            args.grade,
            tuple(args.size),
            args.cutoff,
            density=_density(args.density),
            rock_attribute=args.rock,
            dxf=Path(args.dxf) if args.dxf else None,
        )
        tonnes = sum(s.tonnes for s in r.stopes)
        metal = sum(s.metal for s in r.stopes)
        out.write(
            f"envelope {r.envelope_tonnes:,.0f} t at {r.envelope_grade:.4g}; "
            f"{len(r.stopes)} stopes {tonnes:,.0f} t at {metal / tonnes if tonnes else 0:.4g}\n"
        )
    return 0


def drillholes_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        db = _db(project)
        if args.dh_command == "import":
            for r in import_folder(db, Path(args.folder)):
                out.write(f"{r.table.name}: {r.table.role.value}, {r.table.rows} rows\n")
            return 0
        if args.dh_command == "validate":
            report = validate(db)
            out.write(
                f"{len(report.holes)} usable holes, "
                f"{report.count(severity=Severity.ERROR)} errors, "
                f"{report.count(severity=Severity.WARNING)} warnings\n"
            )
            return 0
        result = composite_table(
            db,
            args.table,
            args.fields,
            args.length,
            domain_field=args.domain,
            min_coverage=args.min_coverage,
            report=validate(db),
        )
        name = f"{args.table}_komp_{args.length:g}m"
        save_composites(db, name, result, args.table, args.fields)
        out.write(f"{result.table.num_rows} composites written to {name}\n")
        return 0


def surface_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        if args.sf_command == "import":
            s = surfaces.read_points_csv(Path(args.file))
            surfaces.save_surface(project, args.name, s)
            out.write(f"surface {args.name}: {len(s.points)} points\n")
        elif args.sf_command == "from-collars":
            db = _db(project)
            geo = hole_geometry(db, validate(db))
            s = surfaces.Surface(np.array([g.collar for g in geo.values()]))
            surfaces.save_surface(project, args.name, s)
            out.write(f"surface {args.name}: {len(s.points)} collars\n")
        else:
            model = BlockModel.open(project, args.model)
            rock, outside = surfaces.flag_below(model, surfaces.load_surface(project, args.surface))
            out.write(
                f"kaya: {rock} rock blocks of {model.grid.blocks}; {outside} columns outside\n"
            )
    return 0


def _bm_swath(project: Project, model: BlockModel, args: argparse.Namespace, out: TextIO) -> int:
    samples = interval_samples(_db(project), *_source(args.source))
    rows = swath(samples, model, args.attribute, "xyz".index(args.axis), args.width)
    if args.json:
        json.dump([r.__dict__ for r in rows], out, indent=2)
        out.write("\n")
        return 0
    out.write(f"{'centre':>10} {'samples':>8} {'mean':>10} {'blocks':>8} {'estimate':>10}\n")
    for r in rows:
        out.write(
            f"{r.centre:10.1f} {r.samples:8d} {r.sample_mean:10.4g} "
            f"{r.blocks:8d} {r.block_mean:10.4g}\n"
        )
    return 0


def _bm_derive(project: Project, model: BlockModel, args: argparse.Namespace, out: TextIO) -> int:
    if args.bm_command == "swath":
        return _bm_swath(project, model, args, out)
    if args.bm_command == "reblock":
        new = reblock(
            project, model, args.name, tuple(args.factors), density=_density(args.density)
        )
        out.write(f"{args.name}: {new.grid.count} blocks of {new.grid.size}\n")
        return 0
    if args.bm_command == "calc":
        values = calculate(model, args.attribute, args.expression, args.unit)
        known = int(np.isfinite(values).sum())
        out.write(f"{args.attribute}: {known} values, {values.size - known} missing\n")
        return 0
    if args.bm_command == "classify":
        n = len(args.distance)
        kv = args.kv or [0.0] * n
        if len(args.samples) != n or len(kv) != n:
            raise BlockModelError("give one --distance, --samples (and --kv) per category")
        criteria = [
            Criteria(d, k, v if v > 0 else None)
            for d, k, v in zip(args.distance, args.samples, kv, strict=True)
        ]
        counts = classify(model, args.attribute, criteria)
        for cat, count in counts.items():
            out.write(f"category {cat}: {count} blocks\n")
        out.write("note: not a JORC/NI 43-101 classification; needs a Competent Person\n")
        return 0
    raise AssertionError(args.bm_command)


def bm_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        if args.bm_command == "create":
            samples = interval_samples(_db(project), *_source(args.source))
            grid = Grid.covering(samples.xyz, tuple(args.size), args.padding)
            BlockModel.create(project, args.name, grid)
            out.write(f"{args.name}: {grid.count} = {grid.blocks} blocks, origin {grid.origin}\n")
            return 0
        if args.bm_command == "import":
            imported = import_block_csv(
                project, Path(args.file), args.name, units=parse_units(args.units)
            )
            out.write(
                f"{args.name}: {imported.rows} rows -> {imported.model.grid.count}, "
                f"{imported.blocks_filled} blocks, "
                f"{imported.merged_rows} merged sub-blocks; "
                f"attributes {', '.join(imported.attributes)}\n"
            )
            return 0
        if args.bm_command == "variogram":
            samples = interval_samples(_db(project), *_source(args.source))
            exp = experimental(samples.xyz, samples.values, args.lag, args.nlags)
            fitted = fit_spherical(exp)
            for h, g, n in zip(exp.mean_distance, exp.gamma, exp.pairs, strict=True):
                out.write(f"lag {h:9.2f}  gamma {g:12.6g}  pairs {n}\n")
            s = fitted.structures[0]
            out.write(
                f"fit: nugget {fitted.nugget:.6g}, sill {s.sill:.6g}, range {s.ranges.major:.2f}\n"
            )
            if args.save:
                save_variogram(project, args.save, fitted, {"data": args.source, "lag": args.lag})
                out.write(f"saved variogram {args.save}\n")
            return 0
        model = BlockModel.open(project, args.model)
        if args.bm_command == "estimate":
            samples = interval_samples(_db(project), *_source(args.source))
            r = args.radius * 3 if len(args.radius) == 1 else args.radius
            r = sorted(r, reverse=True)
            search = Search(
                Ellipsoid(r[0], r[1], r[2], *args.angles), args.min, args.max, args.per_hole
            )
            variogram = load_variograms(project).get(args.variogram) if args.variogram else None
            mask = model.values("kaya") > 0.5 if args.rock_only else None
            missed = estimate_blocks(
                model,
                samples,
                args.attribute,
                method=METHODS[args.method],
                search=search,
                variogram=variogram,
                power=args.power,
                mask=mask,
                top_cut=args.top_cut,
            )
            out.write(f"{args.attribute}: estimated; {missed} blocks without enough samples\n")
            return 0
        if args.bm_command in ("calc", "classify", "reblock", "swath"):
            return _bm_derive(project, model, args, out)
        density: float | str = _density(args.density)
        mask = None
        if args.filter:
            f = model.values(args.filter[0])
            mask = np.isfinite(f) & (f >= float(args.filter[1])) & (f <= float(args.filter[2]))
        weight = model.values(args.weight) if args.weight else None
        gt = model.grade_tonnage(
            args.attribute, args.cutoffs, density=density, mask=mask, weight=weight
        )
        if args.json:
            json.dump([r.__dict__ for r in gt.rows], out, indent=2)
            out.write("\n")
        else:
            out.write(
                f"{'cutoff':>10} {'tonnes':>14} {'grade':>10} {'metal':>14} ({gt.metal_unit})\n"
            )
            for row in gt.rows:
                out.write(
                    f"{row.cutoff:10.4g} {row.tonnes:14,.0f} {row.grade:10.4g} {row.metal:14,.1f}\n"
                )
            out.write(f"unestimated blocks excluded: {gt.unestimated_blocks}\n")
        return 0


def _density(text: str) -> float | str:
    try:
        return float(text)
    except ValueError:
        return text


def pit_export(project: Project, args: argparse.Namespace, out: TextIO) -> int:
    e = export_pit(
        project,
        BlockModel.open(project, args.model),
        Path(args.dxf),
        last_shell=args.shell,
        phases=args.phases,
        topography=args.topography,
        save_surface=args.save_surface,
    )
    out.write(
        f"pit to shell {e.last_shell}: {e.blocks} blocks, solid {e.volume:,.0f} m3, "
        f"{e.outlines} outlines -> {args.dxf}\n"
    )
    return 0


def pit_design(project: Project, args: argparse.Namespace, out: TextIO) -> int:
    geometry = BenchGeometry(args.face, args.height, args.berm)
    r = design_from_model(
        project,
        BlockModel.open(project, args.model),
        geometry,
        last_shell=args.shell,
        topography=args.topography,
        save_surface=args.save_surface,
        flag_attribute=args.flag,
        dxf=Path(args.dxf) if args.dxf else None,
        ramp=Ramp(args.ramp_width, args.ramp_gradient, args.ramp_exit, not args.ramp_ccw)
        if args.ramp_width
        else None,
        sectors=parse_sectors(";".join(args.sector), args.height),
    )
    for s in r.pit.sectors:
        out.write(
            f"sector from {s.azimuth % 360.0:.0f} deg: face {s.geometry.face_angle:g}, "
            f"berm {s.geometry.berm:g}, overall {s.geometry.overall_angle:.1f} deg\n"
        )
    if r.pit.ramp is not None:
        out.write(
            f"ramp {r.pit.ramp_length:,.0f} m from {r.pit.ramp[0, 2]:.1f} "
            f"to {r.pit.ramp[-1, 2]:.1f}\n"
        )
    out.write(
        f"overall slope {geometry.overall_angle:.1f} deg; volume {r.pit.volume():,.0f} m3; "
        f"blocks {r.shell_blocks} in shell, {r.design_blocks} in design; "
        f"value {r.shell_value:,.0f} -> {r.design_value:,.0f}\n"
    )
    outside = r.pit.volume(outside_model_only=True)
    if outside > 0:
        out.write(f"warning: {outside:,.0f} m3 of the design lies outside the block model\n")
    if r.pit.reaches_edge:
        out.write("warning: the design reaches the edge of the model area\n")
    return 0


def pit_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        if args.pit_command == "export":
            return pit_export(project, args, out)
        if args.pit_command == "design":
            return pit_design(project, args, out)
        if args.pit_command == "cutoff":
            return pit_cutoff(project, args, out)
        if args.pit_command == "periods":
            ends = period_surfaces(
                project,
                BlockModel.open(project, args.model),
                topography=args.topography,
                prefix=args.prefix,
                dxf=Path(args.dxf) if args.dxf else None,
            )
            for e in ends:
                out.write(f"{e.name}: {e.blocks} blocks (cumulative {e.cumulative_blocks})\n")
            return 0
        model = BlockModel.open(project, args.model)
        if not args.nsr and None in (args.price, args.price_unit, args.recovery):
            raise PitOptError("--price, --price-unit and --recovery are required without --nsr")
        econ = Economics(
            args.price or 1.0,
            PriceUnit(args.price_unit or "t"),
            args.recovery or 1.0,
            args.mining_cost,
            args.processing_cost,
            args.selling_cost,
            args.bench_cost,
            _density(args.density),
            args.mining_recovery,
            args.dilution,
            args.nsr,
        )
        slope = SlopeProfile(args.slope)
        factors = sorted(set(args.revenue_factors) | {args.final_rf})
        result = optimise_pit(
            model,
            args.grade,
            econ,
            slope,
            factors,
            mining_rate=args.rate,
            discount_rate=args.discount,
            rock_attribute=args.rock,
        )
        final = factors.index(args.final_rf) + 1
        phase_ore = args.phase_ore or max(result.rows[final - 1].ore_t / 3, 1.0)
        phase = pushbacks(result, final, phase_ore)
        if args.min_width > 0:
            phase = apply_mining_width(model.grid, phase, slope, args.min_width).phase
        sched: Schedule | ConstrainedSchedule
        if args.plant:
            limits = Limits(args.rate, args.plant, args.lag, args.sinking, args.discount)
            sched = schedule_constrained(result, phase, limits)
        else:
            sched = schedule(result, phase, args.rate, args.discount)
        store(model, result, econ, slope, phase, sched)
        edge = edge_blocks(model.grid, result.shell > 0)
        if edge:
            sys.stderr.write(
                f"warning: the largest shell touches the model side with {edge} blocks; "
                "it may be truncated (create the model with padding)\n"
            )
        if args.json:
            json.dump(
                {
                    "shells": [r.__dict__ for r in result.rows],
                    "schedule": [p.__dict__ for p in sched.rows],
                    "npv": sched.npv,
                },
                out,
                indent=2,
            )
            out.write("\n")
            return 0
        out.write(
            f"{'shell':>5} {'RF':>5} {'ore t':>14} {'waste t':>14} "
            f"{'best NPV':>14} {'worst NPV':>14}\n"
        )
        for r in result.rows:
            out.write(
                f"{r.shell:5d} {r.revenue_factor:5.2f} {r.ore_t:14,.0f} {r.waste_t:14,.0f} "
                f"{r.best_npv:14,.0f} {r.worst_npv:14,.0f}\n"
            )
        out.write(
            f"pushbacks: {int(phase.max())}; schedule: {len(sched.rows)} years; "
            f"NPV {sched.npv:,.0f}\n"
        )
    return 0


def pit_cutoff(project: Project, args: argparse.Namespace, out: TextIO) -> int:
    model = BlockModel.open(project, args.model)
    if not args.nsr and None in (args.price, args.price_unit, args.recovery):
        raise PitOptError("--price, --price-unit and --recovery are required without --nsr")
    econ = Economics(
        args.price or 1.0,
        PriceUnit(args.price_unit or "t"),
        args.recovery or 1.0,
        args.mining_cost,
        args.processing_cost,
        0.0 if args.nsr else args.selling_cost,
        density=_density(args.density),
        nsr=args.nsr,
    )
    result = cutoffs_from_model(
        model,
        args.grade,
        econ,
        mine_capacity=args.mine,
        mill_capacity=args.mill,
        fixed=args.fixed_cost,
        market_capacity=args.market or math.inf,
        discount=args.discount,
        last_shell=args.shell,
        rock_attribute=args.rock,
    )
    if args.json:
        json.dump({"years": [y.__dict__ for y in result.years], "npv": result.npv}, out, indent=2)
        out.write("\n")
        return 0
    out.write(
        f"{'year':>4} {'cut-off':>8} {'material t':>13} {'ore t':>13} {'grade':>8} "
        f"{'metal':>12} {'cash flow':>14} {'limit':>6}\n"
    )
    for y in result.years:
        out.write(
            f"{y.year:4d} {y.cutoff:8.3f} {y.material_t:13,.0f} {y.ore_t:13,.0f} {y.grade:8.3f} "
            f"{y.metal:12,.1f} {y.cash:14,.0f} {y.limit:>6}\n"
        )
    out.write(f"NPV {result.npv:,.0f} ({result.iterations} iterations)\n")
    return 0


HANDLERS = {
    "drillholes": drillholes_command,
    "surface": surface_command,
    "bm": bm_command,
    "pit": pit_command,
    "ug": ug_command,
    "strings": strings_command,
    "solid": solid_command,
    "geo": geo_command,
    "plot": plot_command,
    "decline": decline_command,
}
