"""Table dialect detection and row parsing for drillhole importers.

Detects encoding, delimiter, decimal/thousands separators, header row and
line terminator for text-based field and lab CSV files. Conservative by
design: when the evidence is ambiguous the returned dialect carries
warnings and reduced confidence instead of guessing silently.
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from minecore._core import MineCoreError


class TableFormatError(MineCoreError):
    """Raised when a text table cannot be reliably interpreted."""


@dataclass(frozen=True)
class TextDialect:
    encoding: str
    bom: bool
    delimiter: str
    decimal: str
    thousands: str | None
    quotechar: str
    line_terminator: str
    header_row: int
    confidence: float
    warnings: tuple[str, ...]


_DELIMITER_CANDIDATES: tuple[str, ...] = (",", ";", "\t", "|")
_DELIMITER_PRIORITY = {",": 0, ";": 1, "\t": 2, "|": 3}
_CP1252_SUSPECT_CHARS = frozenset("æøåëïôûÿœ")
_PLACEHOLDER_VALUES = frozenset({"-", "?", "NS", "ns", "n/a", "N/A", "NA", "na"})

# All (decimal, thousands) combinations we consider during resolution.
_ALL_COMBOS: tuple[tuple[str, str | None], ...] = (
    (".", None),
    (".", ","),
    (",", None),
    (",", "."),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def sniff(data: bytes, *, max_lines: int = 2000) -> TextDialect:
    """Inspect the raw bytes of a text table and return its dialect."""
    warnings: list[str] = []
    confidence = 1.0

    encoding, bom, enc_warnings = _detect_encoding(data)
    warnings.extend(enc_warnings)
    if enc_warnings:
        confidence -= 0.1

    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise TableFormatError(f"cannot decode file as {encoding}") from exc

    line_terminator = "\r\n" if "\r\n" in text else "\n"
    all_lines = text.splitlines()
    if not all_lines:
        raise TableFormatError("file is empty")

    sample = [ln for ln in all_lines[:max_lines] if ln.strip()]
    if len(sample) < 2:
        raise TableFormatError("file has fewer than two non-empty lines; cannot determine dialect")

    (
        delimiter,
        consistency,
        tie,
        decimal,
        thousands,
        num_warnings,
        num_penalty,
    ) = _detect_delimiter_and_decimal(sample)
    warnings.extend(num_warnings)
    confidence -= num_penalty
    if tie:
        confidence -= 0.1

    body_count = _modal_field_count(sample, delimiter)
    header_row, header_warnings = _detect_header(all_lines[:max_lines], delimiter, body_count)
    warnings.extend(header_warnings)
    if header_warnings:
        confidence -= 0.1

    confidence -= 0.3 * (1.0 - consistency)
    confidence = max(0.0, min(1.0, confidence))

    return TextDialect(
        encoding=encoding,
        bom=bom,
        delimiter=delimiter,
        decimal=decimal,
        thousands=thousands,
        quotechar='"',
        line_terminator=line_terminator,
        header_row=header_row,
        confidence=round(confidence, 2),
        warnings=tuple(warnings),
    )


def parse_number(text: str, dialect: TextDialect) -> float | None:
    """Parse a single numeric field according to *dialect*.

    Returns ``None`` for empty / whitespace-only input. Raises
    :class:`TableFormatError` for anything that is not a plain number;
    markers such as ``<0.005``, ``NS`` or ``-`` are the importer's job.
    """
    s = _strip_padding(text)
    if not s:
        return None

    sign = 1.0
    if s[0] in "+-":
        if s[0] == "-":
            sign = -1.0
        s = s[1:]
    if not s:
        raise TableFormatError(f"invalid number: {text!r}")

    exp = 0
    m = re.search(r"[eE]([+-]?\d+)$", s)
    if m:
        exp = int(m.group(1))
        s = s[: m.start()]
        if not s:
            raise TableFormatError(f"invalid number: {text!r}")

    dec = dialect.decimal
    thou = dialect.thousands

    if dec in s:
        idx = s.rfind(dec)
        int_part, frac_part = s[:idx], s[idx + 1 :]
    else:
        int_part, frac_part = s, None

    if not int_part:
        int_part = "0"
    if frac_part is not None and not frac_part.isdigit():
        raise TableFormatError(f"invalid fractional part: {text!r}")

    if thou is not None and thou in int_part:
        pattern = rf"^\d{{1,3}}(?:{re.escape(thou)}\d{{3}})+$"
        if not re.match(pattern, int_part):
            raise TableFormatError(f"invalid thousands grouping: {text!r}")
        int_clean = int_part.replace(thou, "")
    else:
        if not int_part.isdigit():
            raise TableFormatError(f"invalid integer part: {text!r}")
        int_clean = int_part

    s_clean = int_clean + ("." + frac_part if frac_part is not None else "")
    if exp:
        s_clean += f"e{exp}"
    try:
        value = float(s_clean)
    except ValueError as exc:
        raise TableFormatError(f"invalid number: {text!r}") from exc
    return sign * value


def read_rows(
    path: Path, dialect: TextDialect | None = None
) -> tuple[TextDialect, list[list[str]]]:
    """Read *path* using *dialect* (auto-detected if ``None``)."""
    data = path.read_bytes()
    if dialect is None:
        dialect = sniff(data)
    try:
        text = data.decode(dialect.encoding)
    except UnicodeDecodeError as exc:
        raise TableFormatError(f"cannot decode {path} as {dialect.encoding}") from exc
    try:
        reader = csv.reader(
            io.StringIO(text, newline=""),
            delimiter=dialect.delimiter,
            quotechar=dialect.quotechar,
        )
        rows = [list(r) for r in reader]
    except csv.Error as exc:
        raise TableFormatError(f"failed to parse {path}: {exc}") from exc
    while rows and not rows[-1]:
        rows.pop()
    return dialect, rows


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _detect_encoding(data: bytes) -> tuple[str, bool, list[str]]:
    warnings: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", True, warnings
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return "utf-16", True, warnings
    try:
        data.decode("utf-8")
        return "utf-8", False, warnings
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1254")
    except UnicodeDecodeError as exc:
        raise TableFormatError("cannot decode file as utf-8 or cp1254") from exc
    if any(c in _CP1252_SUSPECT_CHARS for c in text):
        warnings.append(
            "file decoded as cp1254 but contains characters typical of "
            "Western European cp1252; verify the encoding"
        )
    return "cp1254", False, warnings


# ---------------------------------------------------------------------------
# Delimiter + numeric layout
# ---------------------------------------------------------------------------


def _detect_delimiter_and_decimal(
    lines: list[str],
) -> tuple[str, float, bool, str, str | None, list[str], float]:
    per_delim: dict[str, tuple[float, set[str]]] = {}
    for d in _DELIMITER_CANDIDATES:
        raw_counts: list[int] = []
        all_fields: list[str] = []
        for line in lines:
            fields = _split_line(line, d)
            raw_counts.append(len(fields))
            all_fields.extend(fields)
        multi = [c for c in raw_counts if c >= 2]
        if not multi:
            continue
        __modal, modal_n = Counter(multi).most_common(1)[0]
        consistency = modal_n / len(lines)
        evidence: set[str] = set()
        for f in all_fields:
            evidence |= _classify_numeric(f)
        per_delim[d] = (consistency, evidence)

    if not per_delim:
        raise TableFormatError(
            "unable to determine delimiter: no candidate produced a multi-field layout"
        )

    ranked = sorted(
        per_delim.items(),
        key=lambda x: (-x[1][0], _DELIMITER_PRIORITY[x[0]]),
    )
    best_d, (best_cons, best_ev) = ranked[0]
    tie = len(ranked) > 1 and ranked[1][1][0] == ranked[0][1][0]

    # If comma "wins" only because commas are absent from the fields, but
    # a competing delimiter exposes clear comma-decimal tokens, prefer it.
    if best_d == ",":
        for d2, (cons2, ev2) in ranked[1:]:
            if d2 != "," and cons2 >= best_cons - 0.1 and "comma_dec" in ev2:
                best_d, best_cons, best_ev = d2, cons2, ev2
                break

    try:
        decimal, thousands, num_warnings, penalty = _resolve_decimal_thousands(best_ev)
    except TableFormatError as exc:
        raise TableFormatError(f"{exc}; {_conflict_lines(lines, best_d)}") from None
    return best_d, best_cons, tie, decimal, thousands, num_warnings, penalty


def _conflict_lines(lines: list[str], delimiter: str) -> str:
    """Name the first line (1-based among non-empty lines) showing each number style."""
    styles = {
        "dot_dec": "'.' decimal",
        "comma_dec": "',' decimal",
        "dot_thou": "'.' thousands",
        "comma_thou": "',' thousands",
    }
    first: dict[str, int] = {}
    for number, line in enumerate(lines, start=1):
        for f in _split_line(line, delimiter):
            for tag in _classify_numeric(f):
                if tag in styles:
                    first.setdefault(tag, number)
    found = ", ".join(
        f"{styles[t]} first on non-empty line {n}"
        for t, n in sorted(first.items(), key=lambda item: item[1])
    )
    return found or "no line shows a clear number style"


def _modal_field_count(lines: list[str], delimiter: str) -> int:
    counts = [len(_split_line(line, delimiter)) for line in lines]
    multi = [c for c in counts if c >= 2]
    if not multi:
        return 0
    return Counter(multi).most_common(1)[0][0]


def _resolve_decimal_thousands(
    evidence: set[str],
) -> tuple[str, str | None, list[str], float]:
    combos = _filter_combos(evidence)
    if not combos:
        raise TableFormatError(
            "mixed decimal/thousands evidence: file contains incompatible numeric formats"
        )
    if len(combos) == 1:
        return combos[0][0], combos[0][1], [], 0.0

    decimal_ambiguous = "dot_dec" not in evidence and "comma_dec" not in evidence

    preferred: tuple[str, str | None]
    if decimal_ambiguous:
        if "comma3" in evidence and "dot3" not in evidence:
            preferred = (",", None)
            msg = "ambiguous numeric evidence (e.g. '1,234'); assuming decimal ','"
            penalty = 0.3
        elif "dot3" in evidence and "comma3" not in evidence:
            preferred = (".", None)
            msg = "ambiguous numeric evidence (e.g. '1.234'); assuming decimal '.'"
            penalty = 0.3
        elif "dot3" in evidence and "comma3" in evidence:
            preferred = (".", None)
            msg = "ambiguous numeric evidence; assuming decimal '.'"
            penalty = 0.3
        elif evidence == {"int"}:
            preferred = (".", None)
            msg = "only integer values found; assuming decimal '.'"
            penalty = 0.1
        else:
            preferred = (".", None)
            msg = "no numeric fields detected; assuming decimal '.'"
            penalty = 0.1
    else:
        preferred = (".", None) if "dot_dec" in evidence else (",", None)
        msg = ""
        penalty = 0.0

    if preferred not in combos:
        preferred = combos[0]
    if msg:
        return preferred[0], preferred[1], [msg], penalty
    return preferred[0], preferred[1], [], penalty


def _filter_combos(evidence: set[str]) -> list[tuple[str, str | None]]:
    combos = list(_ALL_COMBOS)
    if "dot_dec" in evidence:
        combos = [c for c in combos if c[0] == "."]
    if "comma_dec" in evidence:
        combos = [c for c in combos if c[0] == ","]
    if "dot_thou" in evidence:
        combos = [c for c in combos if c[1] == "."]
    if "comma_thou" in evidence:
        combos = [c for c in combos if c[1] == ","]
    if "dot3" in evidence:
        combos = [c for c in combos if c[0] == "." or c[1] == "."]
    if "comma3" in evidence:
        combos = [c for c in combos if c[0] == "," or c[1] == ","]
    return combos


def _classify_numeric(text: str) -> set[str]:
    """Return evidence tags for a numeric-looking string (empty if none)."""
    s = _strip_padding(text)
    if not s:
        return set()
    if s[0] in "+-":
        s = s[1:]
    if not s:
        return set()
    m = re.match(r"^(.+?)[eE]([+-]?\d+)$", s)
    if m:
        s = m.group(1)
    if not s:
        return set()
    if not all(c.isdigit() or c in ".," for c in s):
        return set()
    if not any(c.isdigit() for c in s):
        return set()

    has_dot = "." in s
    has_comma = "," in s

    if not has_dot and not has_comma:
        return {"int"}

    if has_dot and not has_comma:
        parts = s.split(".")
        if len(parts) > 2:
            if parts[0].isdigit() and all(len(p) == 3 and p.isdigit() for p in parts[1:]):
                return {"dot_thou"}
            return set()
        left, right = parts
        if not left.isdigit() or not right.isdigit():
            return set()
        if len(right) == 3:
            return {"dot3"}
        return {"dot_dec"}

    if has_comma and not has_dot:
        parts = s.split(",")
        if len(parts) > 2:
            if parts[0].isdigit() and all(len(p) == 3 and p.isdigit() for p in parts[1:]):
                return {"comma_thou"}
            return set()
        left, right = parts
        if not left.isdigit() or not right.isdigit():
            return set()
        if len(right) == 3:
            return {"comma3"}
        return {"comma_dec"}

    # both separators present — rightmost is decimal
    dot_last = s.rfind(".") > s.rfind(",")
    if dot_last:
        before, after = s.rsplit(".", 1)
        if not after.isdigit():
            return set()
        if "," in before:
            groups = before.split(",")
            if not groups[0].isdigit() or not all(len(g) == 3 and g.isdigit() for g in groups[1:]):
                return set()
            return {"dot_dec", "comma_thou"}
        if not before.isdigit():
            return set()
        return {"dot_dec"}
    before, after = s.rsplit(",", 1)
    if not after.isdigit():
        return set()
    if "." in before:
        groups = before.split(".")
        if not groups[0].isdigit() or not all(len(g) == 3 and g.isdigit() for g in groups[1:]):
            return set()
        return {"comma_dec", "dot_thou"}
    if not before.isdigit():
        return set()
    return {"comma_dec"}


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------


def _detect_header(lines: list[str], delimiter: str, body_count: int) -> tuple[int, list[str]]:
    warnings: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = _split_line(line, delimiter)
        if len(fields) != body_count:
            continue
        if _looks_like_header(fields):
            return i, warnings
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(_split_line(line, delimiter)) == body_count:
            warnings.append("no header row detected; assuming first body line is header")
            return i, warnings
    warnings.append("no header row detected")
    return 0, warnings


def _looks_like_header(fields: list[str]) -> bool:
    total_nonempty = 0
    total_numeric = 0
    total_real_text = 0
    for raw in fields:
        f = raw.strip()
        if not f:
            continue
        total_nonempty += 1
        if _classify_numeric(f):
            total_numeric += 1
        elif f not in _PLACEHOLDER_VALUES:
            total_real_text += 1
    if total_nonempty == 0:
        return False
    return total_real_text > 0 and total_numeric <= total_nonempty / 2


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_padding(s: str) -> str:
    return s.replace("\u00a0", " ").strip().replace(" ", "")


def _split_line(line: str, delimiter: str, quotechar: str = '"') -> list[str]:
    """Quote-aware split. Outer quotes are stripped from fields."""
    fields: list[str] = []
    cur: list[str] = []
    in_quote = False
    at_field_start = True
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_quote:
            if ch == quotechar:
                if i + 1 < n and line[i + 1] == quotechar:
                    cur.append(quotechar)
                    i += 2
                    continue
                in_quote = False
            else:
                cur.append(ch)
            i += 1
            continue
        if ch == delimiter:
            fields.append("".join(cur))
            cur = []
            at_field_start = True
            i += 1
            continue
        if ch == quotechar and at_field_start:
            in_quote = True
            at_field_start = False
            i += 1
            continue
        cur.append(ch)
        at_field_start = False
        i += 1
    fields.append("".join(cur))
    return fields
