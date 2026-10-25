from __future__ import annotations

import csv
import json
from pathlib import Path

from synthdata.config import Dataset, HeaderStyle, Sample
from synthdata.dialects import Dialect, fmt_float, fmt_special, get_dialect
from synthdata.issues import inject

HEADERS: dict[HeaderStyle, dict[str, list[str]]] = {
    "canonical": {
        "collar": ["hole_id", "x", "y", "z", "total_depth", "hole_type"],
        "survey": ["hole_id", "depth", "azimuth", "dip"],
        "assay": ["hole_id", "from_m", "to_m", "au_ppm", "cu_pct", "sample_id"],
        "lithology": ["hole_id", "from_m", "to_m", "lith_code", "description"],
    },
    "short": {
        "collar": ["HOLEID", "EAST", "NORTH", "RL", "EOH", "TYPE"],
        "survey": ["HOLEID", "DEPTH", "AZI", "DIP"],
        "assay": ["HOLEID", "FROM", "TO", "AU_PPM", "CU_PCT", "SAMPLE"],
        "lithology": ["HOLEID", "FROM", "TO", "LITH", "DESC"],
    },
    "turkish": {
        "collar": ["KUYU_NO", "DOĞU", "KUZEY", "KOT", "DERİNLİK", "KUYU_TİPİ"],
        "survey": ["KUYU_NO", "DERİNLİK", "AZİMUT", "EĞİM"],
        "assay": ["KUYU_NO", "BAŞLANGIÇ", "BİTİŞ", "AU_PPM", "CU_YÜZDE", "NUMUNE_NO"],
        "lithology": ["KUYU_NO", "BAŞLANGIÇ", "BİTİŞ", "LİTOLOJİ", "AÇIKLAMA"],
    },
}


def write(
    dataset: Dataset,
    out_dir: Path,
    *,
    dialect: str = "std",
    header_style: str = "canonical",
) -> list[Path]:
    d = get_dialect(dialect)
    if header_style not in HEADERS:
        msg = f"unknown header style: {header_style}"
        raise ValueError(msg)
    style: HeaderStyle = header_style
    out_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, list[list[str]]] = {}
    files["collar.csv"] = [list(HEADERS[style]["collar"])]
    for collar in dataset.collars:
        files["collar.csv"].append(
            [
                collar.hole_id,
                fmt_float(collar.x, 3, d.decimal),
                fmt_float(collar.y, 3, d.decimal),
                fmt_float(collar.z, 3, d.decimal),
                fmt_float(collar.total_depth, 2, d.decimal),
                collar.hole_type,
            ]
        )

    files["survey.csv"] = [list(HEADERS[style]["survey"])]
    for collar in dataset.collars:
        for station in dataset.surveys[collar.hole_id]:
            files["survey.csv"].append(
                [
                    collar.hole_id,
                    fmt_float(station.depth, 2, d.decimal),
                    fmt_float(station.azimuth, 1, d.decimal),
                    fmt_float(station.dip, 1, d.decimal),
                ]
            )

    files["assay.csv"] = [list(HEADERS[style]["assay"])]
    for sample in dataset.samples:
        files["assay.csv"].append(
            [
                sample.hole_id,
                fmt_float(sample.from_m, 3, d.decimal),
                fmt_float(sample.to_m, 3, d.decimal),
                _fmt_au(sample, d),
                fmt_float(sample.cu_pct, 4, d.decimal),
                sample.sample_id,
            ]
        )

    files["lithology.csv"] = [list(HEADERS[style]["lithology"])]
    for collar in dataset.collars:
        for lith in dataset.lithology[collar.hole_id]:
            files["lithology.csv"].append(
                [
                    collar.hole_id,
                    fmt_float(lith.from_m, 3, d.decimal),
                    fmt_float(lith.to_m, 3, d.decimal),
                    lith.lith_code,
                    lith.description,
                ]
            )

    issues = inject(dataset, files, dialect, dataset.seed)

    paths: list[Path] = []
    for name in ("collar.csv", "survey.csv", "assay.csv", "lithology.csv"):
        path = out_dir / name
        _write_csv(path, files[name], d)
        paths.append(path)

    truth_path = out_dir / "truth.json"
    truth_path.write_text(
        json.dumps(dataset.truth, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    paths.append(truth_path)

    samples_path = out_dir / "samples_truth.csv"
    with samples_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=",", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(
            [
                "hole_id",
                "sample_id",
                "from_m",
                "to_m",
                "mid_x",
                "mid_y",
                "mid_z",
                "inside",
                "true_au_ppm",
                "true_cu_pct",
            ]
        )
        for sample in dataset.samples:
            writer.writerow(
                [
                    sample.hole_id,
                    sample.sample_id,
                    f"{sample.from_m:.2f}",
                    f"{sample.to_m:.2f}",
                    f"{sample.mid_x:.3f}",
                    f"{sample.mid_y:.3f}",
                    f"{sample.mid_z:.3f}",
                    "1" if sample.inside else "0",
                    f"{sample.au_ppm:.6f}",
                    f"{sample.cu_pct:.6f}",
                ]
            )
    paths.append(samples_path)

    issues_path = out_dir / "issues.json"
    issues_path.write_text(
        json.dumps([issue.as_dict() for issue in issues], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    paths.append(issues_path)
    return paths


def _fmt_au(sample: Sample, d: Dialect) -> str:
    if sample.au_ppm < 0.005:
        return fmt_special("<0.005", d.decimal)
    return fmt_float(sample.au_ppm, 3, d.decimal)


def _write_csv(path: Path, rows: list[list[str]], d: Dialect) -> None:
    with path.open("w", encoding=d.encoding, newline="") as handle:
        writer = csv.writer(
            handle,
            delimiter=d.delimiter,
            lineterminator=d.lineterminator,
            quoting=csv.QUOTE_MINIMAL,
        )
        writer.writerows(rows)
