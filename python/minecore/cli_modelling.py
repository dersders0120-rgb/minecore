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
import numpy.typing as npt

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
from minecore.pitopt.benchdesign import (
    BenchGeometry,
    Ramp,
    design_from_model,
    parse_exits,
    parse_sectors,
)
from minecore.pitopt.cashflow import CashFlow, FinancialTerms, cash_flow, parse_capital
from minecore.pitopt.cutoff import cutoffs_from_model
from minecore.pitopt.design import edge_blocks, export_pit, period_surfaces
from minecore.pitopt.economics import PitOptError, parse_products, parse_routes
from minecore.pitopt.milp import OptimisedSchedule, schedule_optimised
from minecore.pitopt.optimise import PitResult, Schedule, route_tonnes, store
from minecore.pitopt.phases import best_pushbacks
from minecore.pitopt.scheduling import ConstrainedSchedule, Limits, schedule_constrained
from minecore.pitopt.sensitivity import (
    Case,
    Input,
    Plan,
    Sensitivity,
    parse_changes,
    sensitivity,
)
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
    create.add_argument(
        "--azimuth",
        type=float,
        default=0.0,
        help="model rotation: bearing of the +j (row) axis, degrees clockwise from north",
    )
    bimp = bm_cmds.add_parser("import", help="block model from CSV (centroids, attributes)")
    bimp.add_argument("project")
    bimp.add_argument("name")
    bimp.add_argument("file")
    bimp.add_argument("--units", default="", help="e.g. 'au=g/t; cu=%%'")
    bimp.add_argument(
        "--azimuth",
        type=float,
        default=0.0,
        help="model rotation: bearing of the +j (row) axis, degrees clockwise from north",
    )
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

    dec = commands.add_parser("decline", help="underground decline, level crosscuts and ore drives")
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
    dec.add_argument("--no-drives", action="store_true", help="crosscuts only, no ore drives")
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
    opt.add_argument("--payable", type=float, default=1.0, help="fraction of metal paid for")
    opt.add_argument("--royalty", type=float, default=0.0, help="fraction of sales revenue")
    opt.add_argument(
        "--coproduct",
        action="append",
        default=[],
        metavar="ATTR:PRICE:UNIT:REC[:PAYABLE[:SELLING[:R2/R3]]]",
        help="further metal valued with GRADE (repeatable), e.g. ag:24:ozt:70:95",
    )
    fin = opt.add_argument_group("project cash flow (needs --plant)")
    fin.add_argument(
        "--capex", default="", metavar="T:AMOUNT;...", help="capital by period, 0 = pre-production"
    )
    fin.add_argument("--fixed-cost", type=float, default=0.0, help="fixed cost per year")
    fin.add_argument("--closure", type=float, default=0.0, help="closure cost after the last year")
    fin.add_argument("--tax", type=float, default=0.0, help="income tax rate, fraction")
    fin.add_argument("--depreciation", type=int, default=5, help="straight-line years")
    fin.add_argument("--mid-period", action="store_true", help="discount at mid-year")
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
    opt.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="NAME:RECOVERY:COST",
        help="extra process route (e.g. leach:0.6:4); repeat for each route",
    )
    opt.add_argument(
        "--route-capacity",
        type=float,
        action="append",
        default=[],
        help="ore t/year of each extra route, in --route order (with --plant)",
    )
    opt.add_argument(
        "--grade-bins",
        type=float,
        nargs="+",
        default=[],
        metavar="EDGE",
        help="feed-grade edges of the stockpile bins, ascending (with --plant)",
    )
    opt.add_argument(
        "--stockpile-capacity",
        type=float,
        action="append",
        default=[],
        help="stockpile t of each route, main route first (with --plant; default unlimited)",
    )
    opt.add_argument("--rehandle-cost", type=float, default=0.0, help="per t reclaimed")
    opt.add_argument(
        "--schedule",
        choices=["greedy", "milp"],
        default="greedy",
        help="greedy rules or NPV-maximising MILP (with --plant)",
    )
    opt.add_argument("--time-limit", type=float, default=60.0, help="MILP time limit, s")
    opt.add_argument(
        "--best-pushbacks",
        action="store_true",
        help="group the shells into pushbacks by schedule NPV (with --plant)",
    )
    opt.add_argument("--max-pushbacks", type=int, help="at most this many pushbacks")
    opt.add_argument(
        "--sensitivity",
        nargs="*",
        choices=[i.value for i in Input],
        help="NPV sensitivity to these inputs (none listed: all; with --plant)",
    )
    opt.add_argument(
        "--changes",
        default="-20;-10;10;20",
        help="sensitivity changes in percent, e.g. --changes=-20;-10;10;20",
    )
    opt.add_argument(
        "--reoptimise", action="store_true", help="re-optimise the pit in every sensitivity case"
    )
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
    des.add_argument(
        "--ramp-exits",
        default="",
        help="exit azimuths of the 2nd, 3rd... separate pit (largest first), e.g. '180;270'",
    )
    des.add_argument("--ramp-ccw", action="store_true", help="spiral counter-clockwise going down")
    des.add_argument(
        "--switchback",
        type=float,
        default=0.0,
        metavar="SPAN",
        help="switchback ramp on one wall: arc in degrees centred on the exit (0 = spiral)",
    )
    des.add_argument("--turn-length", type=float, help="flat road at each switchback, m")
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
    cut.add_argument(
        "--coproduct",
        action="append",
        default=[],
        metavar="ATTR:PRICE:UNIT:REC[:PAYABLE[:SELLING[:R2/R3]]]",
        help="further metal valued with GRADE (repeatable), e.g. ag:24:ozt:70:95",
    )
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
            not args.no_drives,
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
            out.write(
                f"level {a.elevation:.1f}: crosscut {a.length:,.0f} m, "
                f"ore drive {a.drive_length:,.0f} m\n"
            )
        out.write(
            f"decline {dev.decline_length:,.0f} m, crosscuts {dev.crosscut_length:,.0f} m, "
            f"ore drives {dev.drive_length:,.0f} m, "
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
            grid = Grid.covering(samples.xyz, tuple(args.size), args.padding, args.azimuth)
            BlockModel.create(project, args.name, grid)
            turned = f", azimuth {grid.azimuth:g}" if grid.rotated else ""
            out.write(
                f"{args.name}: {grid.count} = {grid.blocks} blocks, origin {grid.origin}{turned}\n"
            )
            return 0
        if args.bm_command == "import":
            imported = import_block_csv(
                project,
                Path(args.file),
                args.name,
                units=parse_units(args.units),
                azimuth=args.azimuth,
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
        ramp=Ramp(
            args.ramp_width,
            args.ramp_gradient,
            args.ramp_exit,
            not args.ramp_ccw,
            args.switchback,
            args.turn_length,
            parse_exits(args.ramp_exits),
        )
        if args.ramp_width
        else None,
        sectors=parse_sectors(";".join(args.sector), args.height),
    )
    for s in r.pit.sectors:
        out.write(
            f"sector from {s.azimuth % 360.0:.0f} deg: face {s.geometry.face_angle:g}, "
            f"berm {s.geometry.berm:g}, overall {s.geometry.overall_angle:.1f} deg\n"
        )
    if r.pit.pits > 1 or r.pit.bottoms > 1:
        out.write(f"separate pits {r.pit.pits}, pit bottoms {r.pit.bottoms}\n")
    for k, road in enumerate(r.pit.ramps, 1):
        length = float(np.linalg.norm(np.diff(road, axis=0), axis=1).sum())
        out.write(f"ramp {k}: {length:,.0f} m from {road[0, 2]:.1f} to {road[-1, 2]:.1f}\n")
    if r.pit.pits_without_ramp:
        out.write(f"pits too small for the ramp (no road): {r.pit.pits_without_ramp}\n")
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


def _write_routes(result: PitResult, econ: Economics, final: int, out: TextIO) -> None:
    for p, metal in zip(econ.coproducts, result.rows[final - 1].coproduct_metal, strict=True):
        out.write(f"co-product {p.attribute}: {metal:,.1f} {p.price_unit.value} recovered\n")
    if econ.routes:
        for name, feed_t, metal in route_tonnes(result, econ, final):
            out.write(f"route {name}: {feed_t:,.0f} t feed, {metal:,.1f} metal\n")


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
        return pit_optimise(project, args, out)


def pit_optimise(project: Project, args: argparse.Namespace, out: TextIO) -> int:
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
        parse_routes(";".join(args.route)),
        args.payable,
        args.royalty,
        parse_products(";".join(args.coproduct)),
    )
    terms = FinancialTerms(
        parse_capital(args.capex),
        args.fixed_cost,
        args.closure,
        args.tax,
        args.depreciation,
        args.discount,
        args.mid_period,
        args.royalty,
    )
    financial = bool(terms.capital or args.fixed_cost or args.closure or args.tax)
    if financial and not args.plant:
        raise PitOptError("the project cash flow needs the constrained schedule: give --plant")
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
    limits = _limits(args)
    if limits is not None and args.best_pushbacks:
        choice = best_pushbacks(
            result, final, limits, min_ore_t=phase_ore, max_pushbacks=args.max_pushbacks
        )
        phase = choice.phase
        sys.stderr.write(
            f"pushbacks by NPV: shells grouped at {list(choice.boundaries)} "
            f"({choice.evaluations} designs, {'all' if choice.exhaustive else 'local search'}); "
            f"NPV {choice.start_npv:,.0f} -> {choice.npv:,.0f}\n"
        )
    else:
        phase = pushbacks(result, final, phase_ore)
    if args.min_width > 0:
        phase = apply_mining_width(model.grid, phase, slope, args.min_width).phase
    sched: Schedule | ConstrainedSchedule
    flow: CashFlow | None = None
    sens: Sensitivity | None = None
    if limits is not None:
        if args.schedule == "milp":
            sched = schedule_optimised(result, phase, limits, time_limit=args.time_limit)
        else:
            sched = schedule_constrained(result, phase, limits)
        flow = cash_flow(sched.rows, terms)
        if args.sensitivity is not None:
            plan = Plan(
                model,
                args.grade,
                econ,
                slope,
                limits,
                terms,
                tuple(factors),
                args.final_rf,
                phase_ore,
                args.min_width,
                args.rock,
            )
            sens = sensitivity(
                plan,
                [Input(i) for i in args.sensitivity] or tuple(Input),
                parse_changes(args.changes),
                reoptimise=args.reoptimise,
                base=(result, phase),
            )
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
        json.dump(_optimise_json(result, sched, flow, sens), out, indent=2)
        out.write("\n")
        return 0
    _write_optimise(result, econ, final, phase, sched, flow, sens, out)
    return 0


def _limits(args: argparse.Namespace) -> Limits | None:
    """Schedule limits when a plant capacity is given; options that need them otherwise fail."""
    needs = {
        "--schedule milp": args.schedule == "milp",
        "--sensitivity": args.sensitivity is not None,
        "--best-pushbacks": args.best_pushbacks,
    }
    if not args.plant:
        for option, used in needs.items():
            if used:
                raise PitOptError(f"{option} needs the constrained schedule: give --plant")
        return None
    return Limits(
        args.rate,
        args.plant,
        args.lag,
        args.sinking,
        args.discount,
        route_capacities=tuple(args.route_capacity),
        grade_bins=tuple(args.grade_bins),
        stockpile_capacities=tuple(args.stockpile_capacity),
        rehandle_cost=args.rehandle_cost,
    )


def _optimise_json(
    result: PitResult,
    sched: Schedule | ConstrainedSchedule,
    flow: CashFlow | None,
    sens: Sensitivity | None,
) -> dict[str, object]:
    return {
        "shells": [r.__dict__ for r in result.rows],
        "schedule": [p.__dict__ for p in sched.rows],
        "npv": sched.npv,
        **(
            {
                "cash_flow": [r.__dict__ for r in flow.rows],
                "project": {
                    "npv": flow.npv,
                    "irr": _finite(flow.irr()),
                    "payback": _finite(flow.payback()),
                    "undiscounted": flow.undiscounted,
                },
            }
            if flow is not None
            else {}
        ),
        **({"sensitivity": _sensitivity_json(sens)} if sens is not None else {}),
        **(
            {
                "milp": {
                    "method": sched.method,
                    "status": sched.status,
                    "mip_gap": _finite(sched.mip_gap),
                    "greedy_npv": sched.greedy_npv,
                }
            }
            if isinstance(sched, OptimisedSchedule)
            else {}
        ),
    }


def _write_optimise(  # noqa: PLR0913, PLR0917 - the parts of one report
    result: PitResult,
    econ: Economics,
    final: int,
    phase: npt.NDArray[np.int64],
    sched: Schedule | ConstrainedSchedule,
    flow: CashFlow | None,
    sens: Sensitivity | None,
    out: TextIO,
) -> None:
    out.write(
        f"{'shell':>5} {'RF':>5} {'ore t':>14} {'waste t':>14} {'best NPV':>14} {'worst NPV':>14}\n"
    )
    for r in result.rows:
        out.write(
            f"{r.shell:5d} {r.revenue_factor:5.2f} {r.ore_t:14,.0f} {r.waste_t:14,.0f} "
            f"{r.best_npv:14,.0f} {r.worst_npv:14,.0f}\n"
        )
    _write_routes(result, econ, final, out)
    out.write(
        f"pushbacks: {int(phase.max())}; schedule: {len(sched.rows)} years; NPV {sched.npv:,.0f}\n"
    )
    if isinstance(sched, OptimisedSchedule):
        out.write(
            f"schedule method: {sched.method} ({sched.status}); "
            f"greedy NPV {sched.greedy_npv:,.0f}\n"
        )
    if isinstance(sched, ConstrainedSchedule):
        reclaimed = sum(p.reclaimed_t for p in sched.rows)
        lost = sum(p.discarded_t for p in sched.rows)
        if reclaimed > 0 or lost > 0:
            out.write(f"stockpiles: {reclaimed:,.0f} t reclaimed, {lost:,.0f} t ore to waste\n")
    if flow is not None:
        _write_cash_flow(flow, out)
    if sens is not None:
        _write_sensitivity(sens, out)


def _case_json(c: Case) -> dict[str, object]:
    return {
        "input": None if c.input is None else c.input.value,
        "change": c.change,
        "npv": _finite(c.npv),
        "irr": _finite(c.irr),
        "ore_t": _finite(c.ore_t),
        "metal": _finite(c.metal),
        "years": c.years,
        "pit_t": _finite(c.pit_t),
    }


def _sensitivity_json(s: Sensitivity) -> dict[str, object]:
    return {
        "reoptimised": s.reoptimised,
        "base": _case_json(s.base),
        "cases": [_case_json(c) for c in s.cases],
    }


def _write_sensitivity(s: Sensitivity, out: TextIO) -> None:
    changes = sorted({c.change for c in s.cases})
    mode = "re-optimised pit" if s.reoptimised else "fixed pit design"
    out.write(f"sensitivity of the project NPV ({mode}; base {s.base.npv:,.0f})\n")
    out.write(f"{'input':<11}" + "".join(f"{c:>+15.0%}" for c in changes) + "\n")
    for which in dict.fromkeys(c.input for c in s.cases):
        cells = []
        for change in changes:
            npv = next(c.npv for c in s.cases if c.input == which and c.change == change)
            cells.append(f"{npv:>15,.0f}" if math.isfinite(npv) else f"{'-':>15}")
        out.write(f"{which.value if which else '':<11}" + "".join(cells) + "\n")


def _finite(value: float) -> float | None:
    return value if math.isfinite(value) else None


def _write_cash_flow(flow: CashFlow, out: TextIO) -> None:
    out.write(
        f"{'year':>4} {'revenue':>14} {'royalty':>12} {'opex':>14} {'tax':>12} "
        f"{'capital':>13} {'cash flow':>14} {'discounted':>14}\n"
    )
    for r in flow.rows:
        opex = r.processing + r.mining + r.fixed + r.closure
        out.write(
            f"{r.period:4d} {r.revenue:14,.0f} {r.royalty:12,.0f} {opex:14,.0f} {r.tax:12,.0f} "
            f"{r.capital:13,.0f} {r.cash:14,.0f} {r.discounted:14,.0f}\n"
        )
    irr, payback = flow.irr(), flow.payback()
    out.write(
        f"project NPV {flow.npv:,.0f}; IRR "
        + (f"{irr:.1%}" if math.isfinite(irr) else "none")
        + "; payback "
        + (f"{payback:.1f} years" if math.isfinite(payback) else "not reached")
        + f"; undiscounted {flow.undiscounted:,.0f}\n"
    )


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
        coproducts=parse_products(";".join(args.coproduct)),
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
    if result.policy == "constant":
        out.write("a constant cut-off beats Lane's policy here; it is reported instead\n")
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
