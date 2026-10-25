"""Import drillhole tables from delimited text files (CSV, TSV, ...).

The importer turns one source file into one canonical table:

1. The dialect is detected (:mod:`minecore.io.sniff`), or given.
2. Columns are mapped to the role's key columns, from an explicit mapping or
   from header aliases (English, Turkish, common software abbreviations).
3. Every other column becomes a field. A column is numeric when its values
   are numbers or assay qualifiers; units are read from header suffixes such
   as ``AU_PPM`` or ``CU_PCT``.
4. Assay text is decoded into value and flag (ADR-0008 §3). Text that is
   neither a number nor a declared code is kept as missing and reported.

Nothing is repaired. The import issues (trimmed IDs, unreadable values) are
stored with the table and appear in every validation report.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from minecore.drillholes.model import (
    SRC_LINE,
    DrillholeDatabase,
    DrillholeError,
    FieldSpec,
    FieldType,
    Flag,
    Role,
    TableInfo,
    key_columns,
)
from minecore.io import TableFormatError, TextDialect, parse_number, read_rows

# Header aliases, compared after folding (lower case, no accents, no separators).
ALIASES: dict[str, tuple[str, ...]] = {
    "hole_id": ("holeid", "hole", "bhid", "dhid", "hid", "kuyuno", "kuyu", "sondajno", "kuyuadi"),
    "x": ("x", "east", "easting", "xcollar", "dogu", "x_m", "xm", "sag", "saga"),
    "y": ("y", "north", "northing", "ycollar", "kuzey", "y_m", "ym", "yukari"),
    "z": ("z", "rl", "elev", "elevation", "zcollar", "kot", "rakim", "z_m", "zm"),
    "max_depth": (
        "maxdepth",
        "totaldepth",
        "eoh",
        "td",
        "depth",
        "length",
        "derinlik",
        "toplamderinlik",
        "kuyuderinligi",
        "sondajderinligi",
    ),
    "depth": ("depth", "at", "distance", "derinlik", "mesafe", "olcumderinligi"),
    "azimuth": ("azimuth", "azi", "az", "brg", "bearing", "azimut"),
    "dip": ("dip", "incl", "inclination", "egim", "dalim"),
    "from": ("from", "fromm", "depthfrom", "start", "baslangic", "den", "itibaren"),
    "to": ("to", "tom", "depthto", "end", "bitis", "kadar", "son"),
}

# Unit suffixes in headers: AU_PPM, CU_PCT, AG_GPT, CU_YUZDE ...
_UNIT_SUFFIX: dict[str, str] = {
    "ppm": "ppm",
    "ppb": "ppb",
    "pct": "%",
    "percent": "%",
    "perc": "%",
    "yuzde": "%",
    "gpt": "g/t",
    "gt": "g/t",
    "g/t": "g/t",
}

DEFAULT_MISSING = frozenset({"", "-", "--", "na", "n/a", "nan", "null", "*", "x"})
DEFAULT_NOT_SAMPLED = frozenset({"ns", "nr", "lnr", "nosample", "örneklenmedi"})
DEFAULT_INSUFFICIENT = frozenset({"is", "lost", "kayip", "yetersiz"})


def fold(text: str) -> str:
    """Lower case, Turkish-aware, without accents or separators: ``KUYU_NO`` -> ``kuyuno``."""
    text = text.replace("İ", "i").replace("I", "ı").lower().replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s_\-.()\[\]/]+", "", text)


@dataclass(frozen=True)
class ImportOptions:
    """Decisions the user states for one file. Defaults suit most assay files."""

    mapping: Mapping[str, str] | None = None
    """Canonical column -> source header. Unmapped key columns use aliases."""
    dialect: TextDialect | None = None
    dip_convention: str = "negative_down"
    """``negative_down`` or ``positive_down`` (survey and collar dips)."""
    depth_unit: str = "m"
    negative_is_below_detection: bool = False
    """Read ``-0.005`` as below a detection limit of 0.005 (old lab convention)."""
    missing_codes: frozenset[str] = DEFAULT_MISSING
    not_sampled_codes: frozenset[str] = DEFAULT_NOT_SAMPLED
    insufficient_codes: frozenset[str] = DEFAULT_INSUFFICIENT
    numeric_codes: Mapping[str, Flag] = field(default_factory=dict)
    """Numbers that are codes, e.g. ``{"-99": Flag.MISSING}``."""
    skip_columns: frozenset[str] = frozenset()
    units: Mapping[str, str] = field(default_factory=dict)
    """Field name -> unit, overriding header suffixes."""


@dataclass
class _Column:
    name: str
    values: list[str]


def _find_mapping(
    headers: Sequence[str], role: Role, given: Mapping[str, str] | None
) -> dict[str, int]:
    required, optional = key_columns(role)
    index = {h: i for i, h in enumerate(headers)}
    folded = {fold(h): i for i, h in reversed(list(enumerate(headers)))}
    result: dict[str, int] = {}
    for canonical in (*required, *optional):
        if given and canonical in given:
            source = given[canonical]
            if source not in index:
                raise DrillholeError(f"mapped column {source!r} for {canonical} is not in the file")
            result[canonical] = index[source]
            continue
        for alias in (fold(canonical), *ALIASES.get(canonical, ())):
            if alias in folded and folded[alias] not in result.values():
                result[canonical] = folded[alias]
                break
    missing = [c for c in required if c not in result]
    if missing:
        raise DrillholeError(
            f"cannot find the {', '.join(missing)} column(s) of a {role} table among "
            f"{list(headers)}; map them explicitly"
        )
    return result


def _unit_from_header(header: str) -> str | None:
    parts = re.split(r"[\s_\-()\[\]]+", header.strip())
    if len(parts) < 2:
        return None
    return _UNIT_SUFFIX.get(fold(parts[-1]))


class _Decoder:
    def __init__(self, dialect: TextDialect, options: ImportOptions) -> None:
        self.dialect = dialect
        self.options = options
        self.codes: dict[str, Flag] = {}
        for codes, flag in (
            (options.missing_codes, Flag.MISSING),
            (options.not_sampled_codes, Flag.NOT_SAMPLED),
            (options.insufficient_codes, Flag.INSUFFICIENT),
        ):
            self.codes.update({c.casefold(): flag for c in codes})

    def number(self, text: str) -> float | None:
        try:
            return parse_number(text, self.dialect)
        except TableFormatError:
            return None

    def value(self, text: str) -> tuple[float, Flag] | None:  # noqa: PLR0911
        """Decode assay text; ``None`` when it is not a number, qualifier or code."""
        s = text.strip()
        if s.casefold() in self.codes:
            return math.nan, self.codes[s.casefold()]
        if s in self.options.numeric_codes:
            return math.nan, self.options.numeric_codes[s]
        if s[:1] in "<>":
            limit = self.number(s[1:])
            if limit is None or not limit > 0:
                return None
            return limit, Flag.BELOW_DETECTION if s[0] == "<" else Flag.ABOVE_LIMIT
        number = self.number(s)
        if number is None:
            return None
        if number < 0 and self.options.negative_is_below_detection:
            return -number, Flag.BELOW_DETECTION
        return number, Flag.MEASURED


@dataclass(frozen=True)
class ImportResult:
    table: TableInfo
    dialect: TextDialect
    mapping: dict[str, str]
    issues: tuple[dict[str, Any], ...]


def _issue(code: str, line: int, message: str, **kw: Any) -> dict[str, Any]:
    return {"code": code, "src_line": line, "message": message, **kw}


def import_table(  # noqa: PLR0912, PLR0915 - one linear pass over the columns
    db: DrillholeDatabase,
    name: str,
    role: Role,
    path: Path,
    options: ImportOptions | None = None,
) -> ImportResult:
    """Read ``path`` and put it into ``db`` as table ``name`` (one undoable step)."""
    options = options or ImportOptions()
    if options.dip_convention not in ("negative_down", "positive_down"):
        raise DrillholeError(f"unknown dip convention {options.dip_convention!r}")
    dialect, rows = read_rows(path, options.dialect)
    header_at = dialect.header_row
    if len(rows) <= header_at:
        raise DrillholeError(f"{path.name} has no header row")
    headers = [h.strip() for h in rows[header_at]]
    body = rows[header_at + 1 :]
    lines = [header_at + 2 + i for i in range(len(body))]
    keep = [i for i, r in enumerate(body) if any(c.strip() for c in r)]
    body = [body[i] for i in keep]
    lines = [lines[i] for i in keep]
    width = len(headers)

    def cell(r: list[str], i: int) -> str:
        return r[i] if i < len(r) else ""

    mapping = _find_mapping(headers, role, options.mapping)
    decoder = _Decoder(dialect, options)
    depth_factor = 1.0
    if options.depth_unit != "m":
        from minecore.units import Unit  # noqa: PLC0415

        depth_factor = Unit(options.depth_unit).factor_to("m")

    issues: list[dict[str, Any]] = []
    columns: dict[str, Any] = {}
    ids: list[str | None] = []
    for r, line in zip(body, lines, strict=True):
        raw = cell(r, mapping["hole_id"])
        stripped = raw.strip()
        if stripped != raw and stripped:
            issues.append(
                _issue("HOLE_ID_TRIMMED", line, f"{raw!r} -> {stripped!r}", hole_id=stripped)
            )
        ids.append(stripped or None)
    columns["hole_id"] = pa.array(ids, pa.string())

    for canonical, index in mapping.items():
        if canonical == "hole_id":
            continue
        values = np.full(len(body), np.nan)
        for k, r in enumerate(body):
            text = cell(r, index).strip()
            number = decoder.number(text)
            if number is None and text and text.casefold() not in decoder.codes:
                issues.append(
                    _issue(
                        "VALUE_UNREADABLE",
                        lines[k],
                        f"{headers[index]} = {text!r} is not a number",
                        hole_id=ids[k],
                        field=canonical,
                    )
                )
            values[k] = np.nan if number is None else number
        if canonical in ("depth", "from", "to", "max_depth"):
            values *= depth_factor
        if canonical == "dip" and options.dip_convention == "positive_down":
            values = -values
        if canonical == "azimuth":
            values = np.where(values == 360.0, 0.0, values)
        columns[canonical] = values
    columns[SRC_LINE] = pa.array(lines, pa.int64())

    fields: list[FieldSpec] = []
    used = set(mapping.values())
    taken = {fold(c) for c in columns}
    for index, header in enumerate(headers):
        if index in used or header in options.skip_columns or not header:
            continue
        fname = header
        if fold(fname) in taken or fname.endswith("__flag"):
            fname = f"{header}_src"
        taken.add(fold(fname))
        texts = [cell(r, index) for r in body]
        decoded = [decoder.value(t) for t in texts]
        nonblank = [d for d, t in zip(decoded, texts, strict=True) if t.strip()]
        readable = [d for d in nonblank if d is not None]
        numbers = [d for d in readable if d[1] is not Flag.MISSING or not math.isnan(d[0])]
        if (
            nonblank
            and len(readable) >= 0.9 * len(nonblank)
            and any(f is Flag.MEASURED for _, f in numbers)
        ):
            unit = options.units.get(fname) or _unit_from_header(header)
            spec = FieldSpec(
                fname,
                FieldType.NUMERIC,
                unit=unit,
                quantity="grade" if unit else None,
                source_column=header,
            )
            values = np.full(len(body), np.nan)
            flags = np.full(len(body), int(Flag.MISSING), np.uint8)
            for k, d in enumerate(decoded):
                if d is None:
                    if texts[k].strip():
                        issues.append(
                            _issue(
                                "VALUE_UNREADABLE",
                                lines[k],
                                f"{header} = {texts[k]!r} is not a value",
                                hole_id=ids[k],
                                field=fname,
                            )
                        )
                    continue
                values[k], flags[k] = d[0], int(d[1])
            columns[fname] = values
            columns[spec.flag_column] = flags
        else:
            distinct = {t.strip() for t in texts if t.strip()}
            kind = (
                FieldType.CATEGORY if len(distinct) <= max(50, len(body) // 10) else FieldType.TEXT
            )
            spec = FieldSpec(fname, kind, source_column=header)
            columns[fname] = pa.array([t.strip() or None for t in texts], pa.string())
        fields.append(spec)
    short = [ln for r, ln in zip(body, lines, strict=True) if len(r) != width]
    if short:
        issues.append(
            _issue(
                "ROW_WIDTH",
                short[0],
                f"{len(short)} rows have a different number of fields than the header "
                f"({width}); first on line {short[0]}",
            )
        )

    table = pa.table(columns)
    info: dict[str, Any] = {
        "dialect": {
            "encoding": dialect.encoding,
            "delimiter": dialect.delimiter,
            "decimal": dialect.decimal,
            "thousands": dialect.thousands,
            "header_row": dialect.header_row,
        },
        "mapping": {c: headers[i] for c, i in mapping.items()},
        "options": {
            "dip_convention": options.dip_convention,
            "depth_unit": options.depth_unit,
            "negative_is_below_detection": options.negative_is_below_detection,
        },
    }
    result = db.put_table(
        name, role, table, tuple(fields), source=path, source_info=info, import_issues=issues
    )
    return ImportResult(result, dialect, info["mapping"], tuple(issues))


def import_folder(db: DrillholeDatabase, folder: Path) -> list[ImportResult]:
    """Import collar, survey and interval files from ``folder`` by file name.

    ``collar*``/``kuyu*`` is the collar table, ``survey*``/``sapma*`` the
    survey table, every other ``*.csv``/``*.txt`` an interval table named
    after the file. Files whose name contains ``truth`` are skipped.
    """
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".csv", ".txt", ".tsv"))
    files = [p for p in files if "truth" not in p.stem.lower()]

    def role_of(p: Path) -> Role:
        stem = fold(p.stem)
        if stem.startswith(("collar", "kuyuagzi", "kuyu", "header", "ust")):
            return Role.COLLAR
        if stem.startswith(("survey", "sapma", "olcum", "deviation")):
            return Role.SURVEY
        return Role.INTERVAL

    ordered = sorted(files, key=lambda p: (list(Role).index(role_of(p)), p.name))
    return [import_table(db, p.stem, role_of(p), p) for p in ordered]
