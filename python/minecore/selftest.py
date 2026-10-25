"""Installation self-test: the whole chain on the bundled sample, headless.

Runs every major command once in a temporary project and writes a report.
Used by the Windows build (the packaged executable is started with
``--self-test REPORT``) and from the Help menu, so a broken installation
(missing native library, plotting backend or SciPy module) shows up as a
failed step instead of an error in the middle of real work.
"""

from __future__ import annotations

import io
import tempfile
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parent / "gui" / "sample"


@dataclass(frozen=True)
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str


def _steps(work: Path) -> list[tuple[str, list[str]]]:
    p = str(work / "selftest.mcproj")
    src = "assay_komp_2m/AU_PPM"
    return [
        ("project", ["project", "create", p, "--name", "selftest"]),
        ("drillholes", ["drillholes", "import", p, str(SAMPLE_DIR)]),
        ("composite", ["drillholes", "composite", p, "assay", "AU_PPM", "--length", "2"]),
        ("block model", ["bm", "create", p, "BM", "--source", src, "--size", "20", "20", "10"]),
        ("topography", ["surface", "from-collars", p, "topo"]),
        ("rock flag", ["surface", "flag", p, "topo", "BM"]),
        ("variogram", ["bm", "variogram", p, src, "--lag", "20", "--save", "vg"]),
        (
            "kriging",
            [
                "bm",
                "estimate",
                p,
                "BM",
                src,
                "au",
                "--method",
                "ok",
                "--variogram",
                "vg",
                "--radius",
                "150",
                "--rock-only",
            ],
        ),
        ("report", ["bm", "report", p, "BM", "au", "--cutoffs", "0", "0.5"]),
        ("implicit", ["geo", "implicit", p, src, "BM", "cevher", "--threshold", "0.2"]),
        (
            "pit",
            [
                "pit",
                "optimise",
                p,
                "BM",
                "au",
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
                "--plant",
                "300000",
                "--route",
                "leach:0.6:4",
                "--route-capacity",
                "100000",
            ],
        ),
        (
            "cut-off",
            [
                "pit",
                "cutoff",
                p,
                "BM",
                "au",
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
                "--mine",
                "1e6",
                "--mill",
                "3e5",
                "--fixed-cost",
                "1e6",
            ],
        ),
        (
            "design",
            [
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
                "--ramp-width",
                "15",
                "--sector",
                "0:70:5",
                "--sector",
                "180:60:6",
                "--dxf",
                str(work / "design.dxf"),
            ],
        ),
        ("periods", ["pit", "periods", p, "BM"]),
        (
            "stopes",
            ["ug", p, "BM", "au", "--size", "20", "20", "20", "--cutoff", "0.3", "--rock", "kaya"],
        ),
        (
            "decline",
            ["decline", p, "BM", "--portal", "500400", "4400040", "1210", "--level-interval", "40"],
        ),
        (
            "plot pdf",
            [
                "plot",
                p,
                str(work / "s.pdf"),
                "--axis",
                "z",
                "--at",
                "1150",
                "--model",
                "BM",
                "--attribute",
                "au",
                "--drill",
                src,
            ],
        ),
        (
            "plot png",
            [
                "plot",
                p,
                str(work / "s.png"),
                "--axis",
                "z",
                "--at",
                "1150",
                "--model",
                "BM",
                "--attribute",
                "au",
            ],
        ),
        (
            "rotated model",
            [
                "bm",
                "create",
                p,
                "BMR",
                "--source",
                src,
                "--size",
                "20",
                "20",
                "10",
                "--azimuth",
                "35",
            ],
        ),
        ("rotated rock flag", ["surface", "flag", p, "topo", "BMR"]),
        (
            "rotated kriging",
            [
                "bm",
                "estimate",
                p,
                "BMR",
                src,
                "au",
                "--method",
                "ok",
                "--variogram",
                "vg",
                "--radius",
                "150",
                "--rock-only",
            ],
        ),
        (
            "rotated plot",
            [
                "plot",
                p,
                str(work / "r.png"),
                "--axis",
                "z",
                "--at",
                "1150",
                "--model",
                "BMR",
                "--attribute",
                "au",
            ],
        ),
    ]


def run_steps(work: Path, on_step: Callable[[Step], None] | None = None) -> list[Step]:
    from minecore.cli import main  # noqa: PLC0415

    results = []
    for name, argv in _steps(work):
        out = io.StringIO()
        start = time.perf_counter()
        try:
            code = main(argv, out)
            ok, detail = code == 0, out.getvalue().strip().splitlines()[-1:] or [""]
            step = Step(name, ok, time.perf_counter() - start, detail[0][:200])
        except Exception:
            step = Step(name, False, time.perf_counter() - start, traceback.format_exc())
        results.append(step)
        if on_step is not None:
            on_step(step)
        if not step.ok:
            break
    return results


def run(report: str | Path) -> int:
    """Run all steps in a temporary folder; write ``report``; 0 when all pass."""
    import minecore  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="minecore-selftest-") as tmp:
        steps = run_steps(Path(tmp))
    ok = len(steps) == len(_steps(Path())) and all(s.ok for s in steps)
    lines = [f"MineCore {minecore.__version__} self-test: {'OK' if ok else 'FAILED'}"]
    lines += [
        f"{'ok  ' if s.ok else 'FAIL'} {s.name:12s} {s.seconds:6.1f} s  {s.detail}" for s in steps
    ]
    Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1
