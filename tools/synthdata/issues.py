from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from synthdata.config import Dataset
from synthdata.dialects import fmt_float, fmt_special, get_dialect

Kind = Literal["error", "special_value"]


@dataclass(frozen=True)
class Issue:
    code: str
    kind: Kind
    file: str
    line: int
    hole_id: str
    detail: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "code": self.code,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "hole_id": self.hole_id,
            "detail": self.detail,
        }


def inject(  # noqa: PLR0915 (one flat table of injections)
    dataset: Dataset,
    files: dict[str, list[list[str]]],
    dialect_name: str,
    seed: int,
) -> list[Issue]:
    if dataset.errors == "none":
        return []

    dialect = get_dialect(dialect_name)
    decimal = dialect.decimal
    issues: list[Issue] = []

    def base(file_name: str) -> list[str]:
        rows = files[file_name]
        if len(rows) > 1:
            return list(rows[1])
        return ["" for _ in rows[0]]

    def append(  # noqa: PLR0913, PLR0917
        file_name: str,
        row: list[str],
        code: str,
        kind: Kind,
        hole_id: str,
        detail: str,
    ) -> None:
        rows = files[file_name]
        rows.append(row)
        issues.append(Issue(code, kind, file_name, len(rows), hole_id, detail))

    total_by_hole = {c.hole_id: c.total_depth for c in dataset.collars}

    # OVERLAP: append a normal assay row, then an overlapping row.
    first = base("assay.csv")
    first[0] = first[0] or "DH0001"
    files["assay.csv"].append(first)
    overlap = list(first)
    overlap[1] = first[1]
    overlap[2] = first[2]
    append(
        "assay.csv",
        overlap,
        "OVERLAP",
        "error",
        overlap[0],
        "assay interval overlaps previous",
    )

    row = base("assay.csv")
    row[2] = row[1]
    append("assay.csv", row, "FROM_GE_TO", "error", row[0], "from_m >= to_m")

    row = base("assay.csv")
    total = total_by_hole.get(row[0], 100.0)
    row[2] = fmt_float(total + 10.0, 2, decimal)
    append("assay.csv", row, "BEYOND_DEPTH", "error", row[0], "interval past total depth")

    collar_row = base("collar.csv")
    append(
        "collar.csv",
        list(collar_row),
        "DUPLICATE_COLLAR",
        "error",
        collar_row[0],
        "duplicate collar row",
    )

    row = base("assay.csv")
    row[0] = "MISSING"
    append("assay.csv", row, "NO_COLLAR", "error", row[0], "assay hole missing from collar")

    row = base("survey.csv")
    total = total_by_hole.get(row[0], 100.0)
    row[1] = fmt_float(total + 5.0, 2, decimal)
    append("survey.csv", row, "SURVEY_BEYOND_DEPTH", "error", row[0], "survey past total depth")

    row = base("survey.csv")
    row[2] = "365"
    append("survey.csv", row, "AZIMUTH_RANGE", "error", row[0], "azimuth outside [0,360)")

    row = base("survey.csv")
    row[3] = "95"
    append("survey.csv", row, "DIP_RANGE", "error", row[0], "dip outside [-90,90]")

    row = base("survey.csv")
    row[3] = "10"
    append("survey.csv", row, "DIP_SIGN", "error", row[0], "positive dip in down hole")

    first = base("survey.csv")
    files["survey.csv"].append(first)
    duplicate = list(first)
    duplicate[2] = "123.4"
    duplicate[3] = "-45.0"
    append(
        "survey.csv",
        duplicate,
        "DUPLICATE_SURVEY_DEPTH",
        "error",
        duplicate[0],
        "same survey depth with different values",
    )

    row = base("assay.csv")
    row[1] = "abc"
    append("assay.csv", row, "NON_NUMERIC", "error", row[0], "non-numeric numeric field")

    row = base("lithology.csv")
    row[3] = "XYZ"
    append("lithology.csv", row, "UNKNOWN_LITH", "error", row[0], "unknown lithology code")

    row = base("assay.csv")
    row[0] = row[0].lower()
    append("assay.csv", row, "HOLE_ID_VARIANT", "error", row[0], "hole id case variant")

    specials: list[tuple[str, str]] = [
        ("EMPTY", ""),
        ("-", "-"),
        ("NS", "NS"),
        ("IS", "IS"),
        ("LNR", "LNR"),
        ("<0.005", "<0.005"),
        ("-0.005", "-0.005"),
    ]
    for code, value in specials:
        row = base("assay.csv")
        row[3] = fmt_special(value, decimal)
        append(
            "assay.csv",
            row,
            code,
            "special_value",
            row[0],
            f"special value {value!r} in au_ppm",
        )

    return issues
