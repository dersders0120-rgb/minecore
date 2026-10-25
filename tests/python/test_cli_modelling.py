"""The whole modelling chain runs headless from the CLI."""

from __future__ import annotations

import io
import json
from pathlib import Path

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
    )  # fmt: skip
    assert "ramp " in ramp
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
    assert "decline" in dec
    assert "DECLINE" in (tmp_path / "dec.dxf").read_text()
    assert "blocks of" in _run("bm", "reblock", p, "BM", "BM2", "--factors", "2", "2", "1")
