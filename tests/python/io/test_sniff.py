"""Tests for minecore.io.sniff."""

from __future__ import annotations

from pathlib import Path

import pytest

from minecore.io import (
    TableFormatError,
    TextDialect,
    parse_number,
    read_rows,
    sniff,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dialect(
    decimal: str = ".",
    thousands: str | None = None,
    delimiter: str = ",",
) -> TextDialect:
    return TextDialect(
        encoding="utf-8",
        bom=False,
        delimiter=delimiter,
        decimal=decimal,
        thousands=thousands,
        quotechar='"',
        line_terminator="\n",
        header_row=0,
        confidence=1.0,
        warnings=(),
    )


# ---------------------------------------------------------------------------
# 1. UTF-8, comma, dot
# ---------------------------------------------------------------------------


def test_utf8_comma_dot(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    p.write_bytes(b"id,x,y,z\n1,1.5,2.5,3.5\n2,4.5,5.5,6.5\n")
    d = sniff(p.read_bytes())
    assert d.encoding == "utf-8"
    assert d.bom is False
    assert d.delimiter == ","
    assert d.decimal == "."
    assert d.thousands is None
    assert d.line_terminator == "\n"
    assert d.header_row == 0
    assert d.confidence > 0.7
    _, rows = read_rows(p, d)
    assert rows[0] == ["id", "x", "y", "z"]
    assert rows[1] == ["1", "1.5", "2.5", "3.5"]


# ---------------------------------------------------------------------------
# 2. UTF-8-BOM, semicolon, comma decimal, CRLF, Turkish header
# ---------------------------------------------------------------------------


def test_utf8_bom_semicolon_crlf_turkish(tmp_path: Path) -> None:
    text = "\ufeffKUYU_NO;DOĞU;KUZEY;KOT\r\nA;1,5;2,5;3,5\r\nB;4,5;5,5;6,5\r\n"
    p = tmp_path / "b.csv"
    p.write_bytes(text.encode("utf-8"))
    d = sniff(p.read_bytes())
    assert d.encoding == "utf-8-sig"
    assert d.bom is True
    assert d.delimiter == ";"
    assert d.decimal == ","
    assert d.thousands is None
    assert d.line_terminator == "\r\n"
    assert d.header_row == 0
    _, rows = read_rows(p, d)
    assert rows[0] == ["KUYU_NO", "DOĞU", "KUZEY", "KOT"]
    assert rows[1] == ["A", "1,5", "2,5", "3,5"]


# ---------------------------------------------------------------------------
# 3. cp1254 with Turkish letters
# ---------------------------------------------------------------------------


def test_cp1254_turkish(tmp_path: Path) -> None:
    text = "KUYU_NO;AÇIKLAMA\nŞİST;ığdır\nİKİ;üç\n"
    p = tmp_path / "c.csv"
    p.write_bytes(text.encode("cp1254"))
    d = sniff(p.read_bytes())
    assert d.encoding == "cp1254"
    assert d.bom is False
    assert d.delimiter == ";"
    _, rows = read_rows(p, d)
    assert rows[1] == ["ŞİST", "ığdır"]
    assert rows[2] == ["İKİ", "üç"]


# ---------------------------------------------------------------------------
# 4. Tab-delimited
# ---------------------------------------------------------------------------


def test_tab_delimited(tmp_path: Path) -> None:
    p = tmp_path / "d.tsv"
    p.write_bytes(b"id\tx\ty\n1\t1.5\t2.5\n2\t3.5\t4.5\n")
    d = sniff(p.read_bytes())
    assert d.delimiter == "\t"
    assert d.decimal == "."
    _, rows = read_rows(p, d)
    assert rows[0] == ["id", "x", "y"]
    assert rows[1] == ["1", "1.5", "2.5"]


# ---------------------------------------------------------------------------
# 5. Quoted fields containing delimiters
# ---------------------------------------------------------------------------


def test_quoted_fields(tmp_path: Path) -> None:
    p = tmp_path / "e.csv"
    data = 'id,note,value\n1,"hello, world",2.5\n2,"x;y",3.5\n'
    p.write_bytes(data.encode("utf-8"))
    d = sniff(p.read_bytes())
    assert d.delimiter == ","
    _, rows = read_rows(p, d)
    assert rows[0] == ["id", "note", "value"]
    assert rows[1] == ["1", "hello, world", "2.5"]
    assert rows[2] == ["2", "x;y", "3.5"]


# ---------------------------------------------------------------------------
# 6. Thousands separators in both styles
# ---------------------------------------------------------------------------


def test_thousands_eu(tmp_path: Path) -> None:
    p = tmp_path / "f.csv"
    data = "a;b\n1.234,56;2.345,67\n3.456,78;4.567,89\n"
    p.write_bytes(data.encode("utf-8"))
    d = sniff(p.read_bytes())
    assert d.delimiter == ";"
    assert d.decimal == ","
    assert d.thousands == "."
    assert parse_number("1.234,56", d) == pytest.approx(1234.56)


def test_thousands_us(tmp_path: Path) -> None:
    p = tmp_path / "g.csv"
    data = 'a,b\n"1,234.56","2,345.67"\n"3,456.78","4,567.89"\n'
    p.write_bytes(data.encode("utf-8"))
    d = sniff(p.read_bytes())
    assert d.delimiter == ","
    assert d.decimal == "."
    assert d.thousands == ","
    assert parse_number("1,234.56", d) == pytest.approx(1234.56)


# ---------------------------------------------------------------------------
# 7. Ambiguous "1,234" — warning expected
# ---------------------------------------------------------------------------


def test_ambiguous_comma_three_digits(tmp_path: Path) -> None:
    p = tmp_path / "h.csv"
    data = "id;value\n1;1,234\n2;5,678\n"
    p.write_bytes(data.encode("utf-8"))
    d = sniff(p.read_bytes())
    assert d.delimiter == ";"
    assert d.decimal == ","
    assert any("ambiguous" in w.lower() for w in d.warnings)
    assert d.confidence <= 0.8


# ---------------------------------------------------------------------------
# 8. Mixed evidence — error expected
# ---------------------------------------------------------------------------


def test_mixed_evidence_raises(tmp_path: Path) -> None:
    p = tmp_path / "i.csv"
    data = "a;b\n1.234,56;1,234.56\n"
    p.write_bytes(data.encode("utf-8"))
    with pytest.raises(TableFormatError):
        sniff(p.read_bytes())


# ---------------------------------------------------------------------------
# 9. Two title lines before the header
# ---------------------------------------------------------------------------


def test_titles_before_header(tmp_path: Path) -> None:
    p = tmp_path / "j.csv"
    data = (
        "Sondaj Verileri 2024\nKuyu Raporu\nKUYU_NO;DOĞU;KUZEY;KOT\nA;1,5;2,5;3,5\nB;4,5;5,5;6,5\n"
    )
    p.write_bytes(data.encode("utf-8"))
    d = sniff(p.read_bytes())
    assert d.delimiter == ";"
    assert d.header_row == 2
    _, rows = read_rows(p, d)
    assert rows[0] == ["Sondaj Verileri 2024"]
    assert rows[2] == ["KUYU_NO", "DOĞU", "KUZEY", "KOT"]


# ---------------------------------------------------------------------------
# 10. UTF-16 LE with BOM
# ---------------------------------------------------------------------------


def test_utf16_le_with_bom(tmp_path: Path) -> None:
    text = "a;b\n1,5;2,5\n3,5;4,5\n"
    data = b"\xff\xfe" + text.encode("utf-16-le")
    p = tmp_path / "k.csv"
    p.write_bytes(data)
    d = sniff(p.read_bytes())
    assert d.encoding == "utf-16"
    assert d.bom is True
    assert d.delimiter == ";"
    assert d.decimal == ","
    _, rows = read_rows(p, d)
    assert rows[0] == ["a", "b"]
    assert rows[1] == ["1,5", "2,5"]


# ---------------------------------------------------------------------------
# 11. parse_number edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "dialect_kwargs", "expected"),
    [
        ("", {}, None),
        ("   ", {}, None),
        ("\u00a0", {}, None),
        ("1234", {}, 1234.0),
        ("1.5", {}, 1.5),
        ("-1.5", {}, -1.5),
        ("+1.5", {}, 1.5),
        ("1.5E3", {}, 1500.0),
        ("1.5e-3", {}, 0.0015),
        ("1,5", {"decimal": ","}, 1.5),
        ("1.234,56", {"decimal": ",", "thousands": "."}, 1234.56),
        ("1,234.56", {"decimal": ".", "thousands": ","}, 1234.56),
        ("1.234.567", {"decimal": ",", "thousands": "."}, 1234567.0),
        ("0", {}, 0.0),
        ("-0.5", {}, -0.5),
        ("1,5E-3", {"decimal": ","}, 0.0015),
    ],
)
def test_parse_number_ok(
    text: str,
    dialect_kwargs: dict[str, object],
    expected: float | None,
) -> None:
    d = _dialect(**dialect_kwargs)  # type: ignore[arg-type]
    assert (
        parse_number(text, d) == pytest.approx(expected)
        if expected is not None
        else (parse_number(text, d) is None)
    )


@pytest.mark.parametrize(
    ("text", "dialect_kwargs"),
    [
        ("1.2.3", {}),
        ("abc", {}),
        ("1,2,3", {"decimal": ","}),
        ("-", {}),
        ("NS", {}),
        ("<0.005", {}),
        ("1,", {"decimal": ","}),
        (".", {}),
        ("1.234,56", {}),
    ],
)
def test_parse_number_errors(text: str, dialect_kwargs: dict[str, object]) -> None:
    d = _dialect(**dialect_kwargs)  # type: ignore[arg-type]
    with pytest.raises(TableFormatError):
        parse_number(text, d)


# ---------------------------------------------------------------------------
# 12. Empty file and single-line file
# ---------------------------------------------------------------------------


def test_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_bytes(b"")
    with pytest.raises(TableFormatError):
        sniff(p.read_bytes())


def test_single_line_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "single.csv"
    p.write_bytes(b"a,b,c\n")
    with pytest.raises(TableFormatError):
        sniff(p.read_bytes())


# ---------------------------------------------------------------------------
# Architect: synthetic generator files in every dialect and header style
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", ["std", "tr", "tr1254"])
@pytest.mark.parametrize("headers", ["canonical", "short", "turkish"])
def test_synthetic_generator_files(tmp_path: Path, dialect: str, headers: str) -> None:
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    root = Path(__file__).resolve().parents[3]
    out = tmp_path / "data"
    subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "synth_drillholes.py"),
            "--holes",
            "6",
            "--out",
            str(out),
            "--dialect",
            dialect,
            "--headers",
            headers,
            "--errors",
            "all",
        ],
        check=True,
        capture_output=True,
    )
    decimal = "." if dialect == "std" else ","
    delimiter = "," if dialect == "std" else ";"
    for name in ("collar.csv", "survey.csv", "assay.csv", "lithology.csv"):
        found, rows = read_rows(out / name)
        assert (found.delimiter, found.decimal, found.header_row) == (delimiter, decimal, 0), name
        if dialect == "tr1254" and headers == "turkish":
            assert found.encoding == "cp1254"
        assert len(rows) > 1


def test_mixed_evidence_names_lines() -> None:
    data = b"a;b\n1;2,5\n2;3,5\n3;4.25\n"
    with pytest.raises(TableFormatError, match=r"line 4"):
        sniff(data)
