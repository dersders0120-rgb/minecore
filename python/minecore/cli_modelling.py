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
from pathlib import Path
from typing import Any, TextIO

import numpy as np

from minecore import surfaces
from minecore.blockmodel import BlockModel, Grid
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
from minecore.pitopt import Economics, PriceUnit, SlopeProfile, optimise_pit, pushbacks, schedule
from minecore.pitopt.design import export_pit
from minecore.pitopt.optimise import Schedule, store
from minecore.pitopt.scheduling import ConstrainedSchedule, Limits, schedule_constrained
from minecore.pitopt.width import apply_mining_width
from minecore.project import Project

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

    bm = commands.add_parser("bm", help="block models, variograms, estimation, reports")
    bm_cmds = bm.add_subparsers(dest="bm_command", metavar="ACTION", required=True)
    create = bm_cmds.add_parser("create", help="regular model covering a data table")
    create.add_argument("project")
    create.add_argument("name")
    create.add_argument("--source", required=True, help="table/field to cover")
    create.add_argument("--size", type=float, nargs=3, required=True, metavar=("DX", "DY", "DZ"))
    create.add_argument("--padding", type=float, default=0.0)
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
    rep = bm_cmds.add_parser("report", help="grade-tonnage table")
    rep.add_argument("project")
    rep.add_argument("model")
    rep.add_argument("attribute")
    rep.add_argument("--cutoffs", type=float, nargs="+", required=True)
    rep.add_argument("--density", default="2.7", help="t/m3 or a density attribute")
    rep.add_argument("--json", action="store_true")

    pit = commands.add_parser("pit", help="open-pit optimisation")
    pit_cmds = pit.add_subparsers(dest="pit_command", metavar="ACTION", required=True)
    opt = pit_cmds.add_parser("optimise", help="nested shells, pushbacks, schedule")
    opt.add_argument("project")
    opt.add_argument("model")
    opt.add_argument("grade")
    opt.add_argument("--price", type=float, required=True)
    opt.add_argument("--price-unit", choices=[u.value for u in PriceUnit], required=True)
    opt.add_argument("--recovery", type=float, required=True, help="fraction, e.g. 0.9")
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
    exp = pit_cmds.add_parser("export", help="pit outlines, surface and solid to DXF")
    exp.add_argument("project")
    exp.add_argument("model")
    exp.add_argument("dxf")
    exp.add_argument("--shell", type=int, help="last shell of the pit (default: all shells)")
    exp.add_argument("--phases", action="store_true", help="one layer set per pushback")
    exp.add_argument("--topography", help="surface to complete the mined-out surface")
    exp.add_argument("--save-surface", help="store the mined-out surface under this name")
    opt.add_argument("--json", action="store_true")


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


def bm_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        if args.bm_command == "create":
            samples = interval_samples(_db(project), *_source(args.source))
            grid = Grid.covering(samples.xyz, tuple(args.size), args.padding)
            BlockModel.create(project, args.name, grid)
            out.write(f"{args.name}: {grid.count} = {grid.blocks} blocks, origin {grid.origin}\n")
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
            )
            out.write(f"{args.attribute}: estimated; {missed} blocks without enough samples\n")
            return 0
        density: float | str = _density(args.density)
        gt = model.grade_tonnage(args.attribute, args.cutoffs, density=density)
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


def pit_command(args: argparse.Namespace, out: TextIO) -> int:
    with Project.open(args.project) as project:
        if args.pit_command == "export":
            return pit_export(project, args, out)
        model = BlockModel.open(project, args.model)
        econ = Economics(
            args.price,
            PriceUnit(args.price_unit),
            args.recovery,
            args.mining_cost,
            args.processing_cost,
            args.selling_cost,
            args.bench_cost,
            _density(args.density),
            args.mining_recovery,
            args.dilution,
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


HANDLERS = {
    "drillholes": drillholes_command,
    "surface": surface_command,
    "bm": bm_command,
    "pit": pit_command,
}
