"""The whole modelling chain runs headless from the CLI."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from minecore.cli import main
from minecore.gui.app import SAMPLE_DIR


def _centre_x(p: str) -> float:
    from minecore.blockmodel import BlockModel  # noqa: PLC0415
    from minecore.project import Project  # noqa: PLC0415

    with Project.open(p) as project:
        g = BlockModel.open(project, "BM").grid
        return g.origin[0] + 0.5 * g.size[0] * g.count[0]


def _run(*argv: str) -> str:
    out = io.StringIO()
    assert main(list(argv), out) == 0, out.getvalue()
    return out.getvalue()


def test_csv_to_pit_schedule(tmp_path: Path) -> None:  # noqa: PLR0915 - one end-to-end chain
    p = str(tmp_path / "c.mcproj")
    _run("project", "create", p, "--name", "c")
    assert "collar" in _run("drillholes", "import", p, str(SAMPLE_DIR))
    assert "usable holes" in _run("drillholes", "validate", p)
    assert "composites" in _run("drillholes", "composite", p, "assay", "AU_PPM", "--length", "2")
    src = "assay_komp_2m/AU_PPM"
    assert "blocks" in _run("bm", "create", p, "BM", "--source", src, "--size", "20", "20", "10")
    _run("surface", "from-collars", p, "topo")
    assert "rock blocks" in _run("surface", "flag", p, "topo", "BM")
    assert "fit:" in _run("bm", "variogram", p, src, "--lag", "20", "--save", "vg")
    out = _run(
        "bm",
        "estimate",
        p,
        "BM",
        src,
        "au_ok",
        "--method",
        "ok",
        "--variogram",
        "vg",
        "--radius",
        "150",
        "--rock-only",
        "--top-cut",
        "20",
    )
    assert "estimated" in out
    assert "values" in _run("bm", "calc", p, "BM", "au2", "au_ok * 2", "--unit", "g/t")
    assert "category 1" in _run(
        "bm", "classify", p, "BM", "au_ok", "--distance", "30", "60", "--samples", "8", "4"
    )
    sub = json.loads(
        _run(
            "bm",
            "report",
            p,
            "BM",
            "au_ok",
            "--cutoffs",
            "0",
            "--json",
            "--filter",
            "kat_au_ok",
            "1",
            "2",
        )
    )
    report = json.loads(_run("bm", "report", p, "BM", "au_ok", "--cutoffs", "0", "0.3", "--json"))
    imp = _run("geo", "implicit", p, "assay/AU_PPM", "BM", "cevher", "--threshold", "0.5")
    assert "open edges 0" in imp
    sw = _run("bm", "swath", p, "BM", "au_ok", "assay_komp_2m/AU_PPM", "--width", "50")
    assert "estimate" in sw.splitlines()[0]
    assert len(sw.splitlines()) > 2
    assert report[0]["tonnes"] >= report[1]["tonnes"] > 0
    assert 0 < sub[0]["tonnes"] <= report[0]["tonnes"]
    result = json.loads(
        _run(
            "pit",
            "optimise",
            p,
            "BM",
            "au_ok",
            "--price",
            "2000",
            "--price-unit",
            "ozt",
            "--recovery",
            "0.9",
            "--mining-cost",
            "2.5",
            "--processing-cost",
            "15",
            "--rock",
            "kaya",
            "--rate",
            "1000000",
            "--plant",
            "300000",
            "--min-width",
            "40",
            "--mining-recovery",
            "0.95",
            "--dilution",
            "0.05",
            "--json",
        )
    )
    assert len(result["shells"]) == 4
    assert "npv" in result
    assert all(p["ore_fed_t"] <= 300000 * (1 + 1e-9) for p in result["schedule"])
    periods = _run("pit", "periods", p, "BM", "--dxf", str(tmp_path / "y.dxf"))
    assert "donem_1:" in periods
    assert "Y1_CREST" in (tmp_path / "y.dxf").read_text()
    dxf = tmp_path / "pit.dxf"
    text = _run("pit", "export", p, "BM", str(dxf), "--phases", "--save-surface", "pit")
    assert "outlines" in text
    assert dxf.read_text().rstrip().endswith("EOF")
    assert "PB1_CREST" in dxf.read_text()
    des = _run(
        "pit",
        "design",
        p,
        "BM",
        "--face",
        "65",
        "--height",
        "10",
        "--berm",
        "5",
        "--flag",
        "tasarim",
        "--dxf",
        str(tmp_path / "d.dxf"),
        "--save-surface",
        "tasarim",
    )
    assert "overall slope" in des
    ramp = _run(
        "pit", "design", p, "BM", "--face", "65", "--height", "10", "--berm", "5",
        "--ramp-width", "15", "--ramp-gradient", "0.1",
        "--sector", "0:70:5", "--sector", "180:55:8",
    )  # fmt: skip
    assert "ramp " in ramp
    assert "sector from 180 deg: face 55" in ramp
    assert "DESIGN_TOE" in (tmp_path / "d.dxf").read_text()
    pdf = tmp_path / "kesit.pdf"
    text = _run(
        "plot", p, str(pdf), "--axis", "x", "--at", str(_centre_x(p)), "--width", "40",
        "--model", "BM", "--attribute", "au_ok", "--drill", src,
        "--surface", "topo", "--surface", "tasarim",
    )  # fmt: skip
    assert pdf.read_bytes().startswith(b"%PDF")
    assert " 0 blocks" not in text
    assert " 0 holes" not in text
    _run("bm", "calc", p, "BM", "nsr", "au_ok * 55")
    common = ["--mining-cost", "2.5", "--processing-cost", "15", "--rock", "kaya", "--json"]
    nsr = json.loads(_run("pit", "optimise", p, "BM", "nsr", "--nsr", *common))
    assert nsr["shells"][-1]["ore_t"] > 0
    lane = json.loads(
        _run(
            "pit", "cutoff", p, "BM", "nsr", "--nsr", "--mine", "1e6", "--mill", "3e5",
            "--fixed-cost", "1e6", *common,
        )
    )  # fmt: skip
    assert lane["years"]
    assert all(y["ore_t"] <= 3e5 * (1 + 1e-9) for y in lane["years"])
    routes = _run(
        "pit", "optimise", p, "BM", "au_ok", "--price", "2000", "--price-unit", "ozt",
        "--recovery", "0.9", "--mining-cost", "2.5", "--processing-cost", "15",
        "--rock", "kaya", "--route", "liç:0.6:4", "--plant", "3e5", "--route-capacity", "1e5",
    )  # fmt: skip
    assert "route main:" in routes
    assert "route liç:" in routes
    from minecore.blockmodel import BlockModel  # noqa: PLC0415
    from minecore.project import Project  # noqa: PLC0415

    with Project.open(Path(p)) as project:
        assert "pit_route" in BlockModel.open(project, "BM").attributes
    ug = _run(
        "ug",
        p,
        "BM",
        "au_ok",
        "--size",
        "20",
        "20",
        "10",
        "--cutoff",
        "0.5",
        "--rock",
        "kaya",
        "--dxf",
        str(tmp_path / "ug.dxf"),
    )
    assert "stopes" in ug
    dec = _run(
        "decline", p, "BM", "--portal", str(_centre_x(p) + 400), "4400000", "1200",
        "--dxf", str(tmp_path / "dec.dxf"),
    )  # fmt: skip
    assert "crosscut" in dec
    assert "ore drives" in dec
    assert "decline" in dec
    assert "DECLINE" in (tmp_path / "dec.dxf").read_text()
    assert "blocks of" in _run("bm", "reblock", p, "BM", "BM2", "--factors", "2", "2", "1")


def test_rotated_model_chain(tmp_path: Path) -> None:
    """A rotated model runs the same chain; its resource matches the unrotated one."""
    p = str(tmp_path / "r.mcproj")
    _run("project", "create", p, "--name", "r")
    _run("drillholes", "import", p, str(SAMPLE_DIR))
    _run("drillholes", "validate", p)
    _run("drillholes", "composite", p, "assay", "AU_PPM", "--length", "2")
    src = "assay_komp_2m/AU_PPM"
    _run("surface", "from-collars", p, "topo")
    totals = []
    for name, azimuth in (("BM", "0"), ("BMR", "35")):
        out = _run(
            "bm", "create", p, name, "--source", src, "--size", "10", "10", "5",
            "--padding", "20", "--azimuth", azimuth,
        )  # fmt: skip
        assert ("azimuth 35" in out) == (azimuth == "35")
        _run("surface", "flag", p, "topo", name)
        _run(
            "bm", "estimate", p, name, src, "au", "--method", "idw", "--radius", "60",
            "--rock-only",
        )  # fmt: skip
        report = json.loads(_run("bm", "report", p, name, "au", "--cutoffs", "0.5", "--json"))
        totals.append(report[0])
    # Same data, same search: the tonnage above cut-off differs only by block placement.
    assert abs(totals[1]["tonnes"] / totals[0]["tonnes"] - 1.0) < 0.02
    assert abs(totals[1]["grade"] / totals[0]["grade"] - 1.0) < 0.02
    econ = [
        "--price", "2000", "--price-unit", "ozt", "--recovery", "0.9",
        "--mining-cost", "2.5", "--processing-cost", "15", "--rock", "kaya", "--json",
    ]  # fmt: skip
    opt = json.loads(_run("pit", "optimise", p, "BMR", "au", *econ))
    assert opt["shells"][-1]["ore_t"] > 0
    des = _run("pit", "design", p, "BMR", "--face", "65", "--height", "10", "--berm", "5")
    assert "overall slope" in des
    from minecore.blockmodel import BlockModel  # noqa: PLC0415
    from minecore.project import Project  # noqa: PLC0415

    with Project.open(p) as project:
        lo, hi = BlockModel.open(project, "BMR").grid.bounds()
    pdf = tmp_path / "r.pdf"
    text = _run(
        "plot", p, str(pdf), "--axis", "x", "--at", str(0.5 * (lo[0] + hi[0])), "--width", "20",
        "--model", "BMR", "--attribute", "au",
    )  # fmt: skip
    assert pdf.read_bytes().startswith(b"%PDF")
    assert " 0 blocks" not in text


def test_project_cash_flow_from_the_cli(tmp_path: Path) -> None:
    p = str(tmp_path / "f.mcproj")
    _run("project", "create", p, "--name", "f")
    _run("drillholes", "import", p, str(SAMPLE_DIR))
    _run("drillholes", "validate", p)
    _run("drillholes", "composite", p, "assay", "AU_PPM", "--length", "2")
    src = "assay_komp_2m/AU_PPM"
    _run("bm", "create", p, "BM", "--source", src, "--size", "20", "20", "10", "--padding", "40")
    _run("bm", "estimate", p, "BM", src, "au", "--method", "idw", "--radius", "80")
    econ = [
        "pit", "optimise", p, "BM", "au", "--price", "2000", "--price-unit", "ozt",
        "--recovery", "0.9", "--mining-cost", "2.5", "--processing-cost", "15",
        "--rate", "2e6", "--plant", "5e5", "--discount", "0.1", "--json",
    ]  # fmt: skip
    plain = json.loads(_run(*econ))
    assert "cash_flow" in plain  # with --plant the cash flow is always reported
    assert plain["project"]["npv"] == pytest.approx(plain["npv"])
    taxed = json.loads(
        _run(
            *econ,
            "--royalty",
            "0.04",
            "--payable",
            "0.99",
            "--capex",
            "0:5e6;2:1e6",
            "--tax",
            "0.25",
            "--closure",
            "1e6",
            "--fixed-cost",
            "2e5",
        )
    )
    rows = taxed["cash_flow"]
    assert rows[0]["period"] == 0
    assert rows[0]["cash"] == pytest.approx(-5e6)
    assert rows[-1]["closure"] == pytest.approx(1e6)
    assert sum(r["royalty"] for r in rows) > 0
    assert taxed["project"]["npv"] < plain["project"]["npv"]
    text = _run(*econ[:-1], "--capex", "0:5e6", "--tax", "0.25")
    assert "project NPV" in text
    no_plant = [a for a in econ if a not in ("--plant", "5e5", "--json")]
    assert main([*no_plant, "--capex", "0:1"], io.StringIO()) == 1  # needs --plant
    # A co-product (here the same grade priced as a second metal) adds revenue.
    co = json.loads(_run(*econ, "--coproduct", "au:200:ozt:50:90"))
    assert co["shells"][-1]["coproduct_metal"][0] > 0
    assert co["npv"] > plain["npv"]
    assert "co-product au:" in _run(*econ[:-1], "--coproduct", "au:200:ozt:50:90")
    assert main([*econ, "--coproduct", "au:200:kg:50"], io.StringIO()) == 1
    # Stockpile grade bins, capacity and rehandling.
    piles = json.loads(
        _run(
            *econ,
            "--grade-bins",
            "1",
            "2",
            "--stockpile-capacity",
            "1e5",
            "--rehandle-cost",
            "1.5",
        )
    )
    rows = piles["schedule"]
    assert len(rows[0]["stock_by_bin"]) == 3
    assert max(r["stockpile_t"] for r in rows) <= 1e5 * (1 + 1e-9)
    assert sum(r["rehandle"] for r in rows) == pytest.approx(
        1.5 * sum(r["reclaimed_t"] for r in rows)
    )
    assert "stockpiles:" in _run(*econ[:-1], "--grade-bins", "1", "2")
    # Sensitivity: the base case is the reported project; price up raises the NPV.
    sens = json.loads(_run(*econ, "--sensitivity", "price", "mining", "--changes=-10;10"))
    s = sens["sensitivity"]
    assert s["base"]["npv"] == pytest.approx(sens["project"]["npv"])
    npv = {(c["input"], c["change"]): c["npv"] for c in s["cases"]}
    assert npv[("price", -0.1)] < s["base"]["npv"] < npv[("price", 0.1)]
    assert npv[("mining", 0.1)] < s["base"]["npv"] < npv[("mining", -0.1)]
    assert "sensitivity of the project NPV" in _run(*econ[:-1], "--sensitivity")
    no_plant = [a for a in econ if a not in ("--plant", "5e5", "--json")]
    assert main([*no_plant, "--sensitivity"], io.StringIO()) == 1
