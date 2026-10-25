"""Turkish wording of validation issues for the report table, and grouping of
cascades (one bad interval that overlaps every other interval of its hole).

The validation messages stay English in the core; this module rewrites the
known message shapes. An unknown shape keeps the catalogue title plus the
original text, so nothing is ever hidden.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from minecore.drillholes import CATALOGUE, Issue, Severity

_N = r"(-?[0-9.]+(?:e[-+]?\d+)?|nan|inf|-inf)"


def _num(text: str) -> str:
    return text.replace(".", ",") if re.fullmatch(r"-?[0-9.]+", text) else text


_PATTERNS: dict[str, tuple[str, Callable[[re.Match[str]], str]]] = {
    "COLLAR_DUPLICATE": (
        r"(\d+) collars for '(.*)' \(lines (.*)\)",
        lambda m: f"'{m[2]}' için {m[1]} kuyu ağzı var (satırlar {m[3]})",
    ),
    "COLLAR_COORD_INVALID": (r"x, y and z must be finite numbers", lambda m: "X, Y, Z sayı olmalı"),
    "COLLAR_MAX_DEPTH_INVALID": (
        rf"max_depth {_N} is not > 0",
        lambda m: f"kuyu derinliği {_num(m[1])}; sıfırdan büyük olmalı",
    ),
    "HOLE_ID_MISSING": (r"hole_id is empty", lambda m: "kuyu numarası boş"),
    "ORPHAN_ROW": (r"no collar for hole '(.*)'", lambda m: f"'{m[1]}' kuyusunun kuyu ağzı yok"),
    "HOLE_ID_CASE": (
        r"IDs differ only by case: (.*)",
        lambda m: m[1],
    ),
    "SURVEY_DEPTH_INVALID": (
        rf"depth {_N} is not a finite depth >= 0",
        lambda m: f"derinlik {_num(m[1])}; sıfır veya pozitif sayı olmalı",
    ),
    "INTERVAL_DEPTH_INVALID": (
        rf"(from|to) {_N} is not a finite depth >= 0",
        lambda m: (
            f"{'başlangıç' if m[1] == 'from' else 'bitiş'} {_num(m[2])}; "
            "sıfır veya pozitif sayı olmalı"
        ),
    ),
    "POINT_DEPTH_INVALID": (
        rf"depth {_N} is not a finite depth >= 0",
        lambda m: f"derinlik {_num(m[1])}; sıfır veya pozitif sayı olmalı",
    ),
    "SURVEY_AZIMUTH_INVALID": (
        rf"azimuth {_N} is not in \[0, 360\]",
        lambda m: f"azimut {_num(m[1])}; 0-360 arasında olmalı",
    ),
    "SURVEY_DIP_INVALID": (
        rf"dip {_N} is not in \[-90, 90\]",
        lambda m: f"eğim {_num(m[1])}; -90 ile 90 arasında olmalı",
    ),
    "SURVEY_DUPLICATE_DEPTH": (
        rf"stations at lines (\d+) and (\d+) share depth {_N}",
        lambda m: f"{m[1]}. ve {m[2]}. satırlar aynı derinlikte ({_num(m[3])} m)",
    ),
    "SURVEY_BEYOND_DEPTH": (
        rf"depth {_N} is below max_depth {_N}",
        lambda m: f"ölçüm {_num(m[1])} m, kuyu derinliği {_num(m[2])} m",
    ),
    "SURVEY_DOGLEG": (
        rf"dogleg ([0-9.]+)°/30 m between {_N} and {_N} m exceeds ([0-9.]+)°/30 m",
        lambda m: (
            f"{_num(m[2])}-{_num(m[3])} m arasında {_num(m[1])}°/30 m (sınır {_num(m[4])}°/30 m)"
        ),
    ),
    "INTERVAL_REVERSED": (
        rf"from {_N} >= to {_N}",
        lambda m: f"başlangıç {_num(m[1])} m, bitiş {_num(m[2])} m",
    ),
    "INTERVAL_GAP": (
        rf"gap of ([0-9.]+) m from {_N} to {_N}",
        lambda m: f"{_num(m[2])}-{_num(m[3])} m arasında {_num(m[1])} m boşluk",
    ),
    "INTERVAL_BEYOND_DEPTH": (
        rf"to {_N} is below max_depth {_N}",
        lambda m: f"bitiş {_num(m[1])} m, kuyu derinliği {_num(m[2])} m",
    ),
    "INTERVAL_OVERLAP": (
        rf"\[{_N}, {_N}\) overlaps line (\d+) \[{_N}, {_N}\)",
        lambda m: (
            f"{_num(m[1])}-{_num(m[2])} m, {m[3]}. satırla ({_num(m[4])}-{_num(m[5])} m) çakışıyor"
        ),
    ),
    "VALUE_QUALIFIED": (
        r"(\d+) values flagged (\w+)",
        lambda m: f"{m[1]} değer işaretli ({ {'missing': 'eksik'}.get(m[2], m[2]) })",
    ),
    "VALUE_OUT_OF_RANGE": (
        r"(.*) = (.*) is outside \[(.*), (.*)\]",
        lambda m: f"{m[1]} = {_num(m[2])}; {_num(m[3])}-{_num(m[4])} aralığı dışında",
    ),
    "VALUE_UNREADABLE": (
        r"(.*) = '(.*)' is not a (?:number|value)",
        lambda m: f"{m[1]} = '{m[2]}'",
    ),
    "HOLE_ID_TRIMMED": (r"'(.*)' -> '(.*)'", lambda m: f"'{m[1]}' → '{m[2]}' (boşluk silindi)"),
    "HOLE_NO_DATA": (r"collar has no downhole data", lambda m: "kuyu içi veri yok"),
    "SURVEY_NONE": (
        r"no survey stations; (collar orientation|a vertical hole) is used",
        lambda m: (
            "sapma ölçümü yok; "
            + ("kuyu ağzı yönelimi" if m[1] == "collar orientation" else "düşey kuyu")
            + " kabul edildi"
        ),
    ),
    "HOLE_DEPTH_DERIVED": (
        rf"no max_depth; deepest data at {_N} m is used",
        lambda m: f"kuyu derinliği yok; en derin veri ({_num(m[1])} m) kullanıldı",
    ),
}


def describe(issue: Issue) -> str:
    """Turkish description: catalogue title plus the reworded detail."""
    title = CATALOGUE[issue.code].title_tr if issue.code in CATALOGUE else issue.code
    pattern = _PATTERNS.get(issue.code)
    if pattern is not None:
        match = re.fullmatch(pattern[0], issue.message)
        if match is not None:
            return f"{title}: {pattern[1](match)}"
    return f"{title}: {issue.message}"


@dataclass(frozen=True)
class Row:
    """One line of the report table."""

    severity: Severity
    code: str
    table: str
    hole: str
    line: str
    text: str


_OVERLAP = re.compile(rf"\[{_N}, {_N}\) overlaps line (\d+) \[{_N}, {_N}\)")
GROUP_MIN = 3  # an interval overlapping this many others is shown as one line


def rows(issues: Sequence[Issue]) -> list[Row]:
    """Report rows; one interval overlapping many others becomes a single row."""
    partners: dict[tuple[str, str, str], list[Issue]] = defaultdict(list)
    for issue in issues:
        if issue.code == "INTERVAL_OVERLAP":
            match = _OVERLAP.fullmatch(issue.message)
            if match is not None:
                partners[(issue.table, issue.hole_id or "", match[3])].append(issue)
    grouped = {k: v for k, v in partners.items() if len(v) >= GROUP_MIN}
    hidden = {id(i) for v in grouped.values() for i in v}
    hidden |= {  # the partner's own mirror lines ("partner overlaps line X")
        id(i)
        for i in issues
        if i.code == "INTERVAL_OVERLAP"
        and (i.table, i.hole_id or "", "" if i.src_line is None else str(i.src_line)) in grouped
    }
    out: list[Row] = []
    done: set[tuple[str, str, str]] = set()
    for issue in issues:
        line = "" if issue.src_line is None else str(issue.src_line)
        key = (issue.table, issue.hole_id or "", line)
        if issue.code == "INTERVAL_OVERLAP" and key in grouped and key not in done:
            done.add(key)
            group = grouped[key]
            match = _OVERLAP.fullmatch(group[0].message)
            assert match is not None
            title = CATALOGUE[issue.code].title_tr
            out.append(
                Row(
                    issue.severity,
                    issue.code,
                    issue.table,
                    issue.hole_id or "",
                    line,
                    f"{title}: {line}. satır ({_num(match[4])}-{_num(match[5])} m) "
                    f"{len(group)} aralıkla çakışıyor; önce bu satırı düzeltin",
                )
            )
            continue
        if id(issue) in hidden:
            continue
        out.append(
            Row(issue.severity, issue.code, issue.table, issue.hole_id or "", line, describe(issue))
        )
    for key, group in grouped.items():  # partner line had no issue of its own
        if key not in done:
            first = group[0]
            match = _OVERLAP.fullmatch(first.message)
            assert match is not None
            out.append(
                Row(
                    first.severity,
                    first.code,
                    first.table,
                    first.hole_id or "",
                    key[2],
                    f"{CATALOGUE[first.code].title_tr}: {key[2]}. satır "
                    f"({_num(match[4])}-{_num(match[5])} m) {len(group)} aralıkla çakışıyor; "
                    "önce bu satırı düzeltin",
                )
            )
    return out
