"""The whole modelling chain runs headless from the CLI."""

from __future__ import annotations

import io
import json
from pathlib import Path

from minecore.cli import main
from minecore.gui.app import SAMPLE_DIR


def _run(*argv: str) -> str:
    out = io.StringIO()
    assert main(list(argv), out) == 0, out.getvalue()
    return out.getvalue()


def test_csv_to_pit_schedule(tmp_path: Path) -> None:
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
    dxf = tmp_path / "pit.dxf"
    text = _run("pit", "export", p, "BM", str(dxf), "--phases", "--save-surface", "pit")
    assert "outlines" in text
    assert dxf.read_text().rstrip().endswith("EOF")
    assert "PB1_CREST" in dxf.read_text()
    _run("bm", "calc", p, "BM", "nsr", "au_ok * 55")
    common = ["--mining-cost", "2.5", "--processing-cost", "15", "--rock", "kaya", "--json"]
    nsr = json.loads(_run("pit", "optimise", p, "BM", "nsr", "--nsr", *common))
    assert nsr["shells"][-1]["ore_t"] > 0
