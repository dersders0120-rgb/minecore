"""Validation issues and their stable catalogue (ADR-0008 §5).

Codes are part of MineCore's public contract: users filter reports by them,
scripts test for them and the documentation explains them. Never rename or
reuse a code; retire it instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Effect(StrEnum):
    """What an issue does to downstream processing. Only errors exclude."""

    NONE = "none"
    EXCLUDE_ROW = "exclude_row"
    EXCLUDE_HOLE = "exclude_hole"


@dataclass(frozen=True)
class IssueType:
    code: str
    severity: Severity
    effect: Effect
    title_en: str
    title_tr: str


_E, _W, _I = Severity.ERROR, Severity.WARNING, Severity.INFO
_NONE, _ROW, _HOLE = Effect.NONE, Effect.EXCLUDE_ROW, Effect.EXCLUDE_HOLE

CATALOGUE: dict[str, IssueType] = {
    t.code: t
    for t in (
        IssueType("COLLAR_DUPLICATE", _E, _HOLE, "Duplicate collar", "Mükerrer kuyu ağzı"),
        IssueType(
            "COLLAR_COORD_INVALID",
            _E,
            _HOLE,
            "Invalid collar coordinate",
            "Geçersiz kuyu ağzı koordinatı",
        ),
        IssueType(
            "COLLAR_MAX_DEPTH_INVALID", _E, _HOLE, "Invalid hole depth", "Geçersiz kuyu derinliği"
        ),
        IssueType("HOLE_ID_MISSING", _E, _ROW, "Missing hole ID", "Kuyu numarası eksik"),
        IssueType(
            "HOLE_ID_CASE",
            _W,
            _NONE,
            "Hole IDs differ only by case",
            "Kuyu numaraları yalnızca harf büyüklüğünde farklı",
        ),
        IssueType(
            "HOLE_ID_TRIMMED",
            _I,
            _NONE,
            "Spaces removed from hole ID",
            "Kuyu numarasındaki boşluklar silindi",
        ),
        IssueType(
            "HOLE_NO_DATA",
            _I,
            _NONE,
            "Collar without downhole data",
            "Kuyu içi verisi olmayan kuyu",
        ),
        IssueType(
            "HOLE_DEPTH_DERIVED",
            _I,
            _NONE,
            "Hole depth derived from data",
            "Kuyu derinliği verilerden türetildi",
        ),
        IssueType("ORPHAN_ROW", _E, _ROW, "Row without collar", "Kuyu ağzı olmayan satır"),
        IssueType(
            "SURVEY_DEPTH_INVALID", _E, _ROW, "Invalid survey depth", "Geçersiz ölçüm derinliği"
        ),
        IssueType(
            "SURVEY_DUPLICATE_DEPTH",
            _E,
            _HOLE,
            "Two survey stations at one depth",
            "Aynı derinlikte iki ölçüm",
        ),
        IssueType("SURVEY_AZIMUTH_INVALID", _E, _ROW, "Invalid azimuth", "Geçersiz azimut"),
        IssueType("SURVEY_DIP_INVALID", _E, _ROW, "Invalid dip", "Geçersiz eğim"),
        IssueType(
            "SURVEY_BEYOND_DEPTH",
            _W,
            _NONE,
            "Survey station below hole depth",
            "Ölçüm kuyu derinliğinin altında",
        ),
        IssueType(
            "SURVEY_DOGLEG", _W, _NONE, "Dogleg above limit", "Sapma şiddeti sınırın üzerinde"
        ),
        IssueType("SURVEY_NONE", _W, _NONE, "Hole without survey", "Sapma ölçümü olmayan kuyu"),
        IssueType(
            "INTERVAL_DEPTH_INVALID",
            _E,
            _ROW,
            "Invalid interval depth",
            "Geçersiz aralık derinliği",
        ),
        IssueType(
            "INTERVAL_REVERSED", _E, _ROW, "Interval with from >= to", "Başlangıcı sonundan derin"
        ),
        IssueType("INTERVAL_OVERLAP", _E, _ROW, "Overlapping intervals", "Çakışan aralıklar"),
        IssueType("INTERVAL_GAP", _I, _NONE, "Gap between intervals", "Aralıklar arasında boşluk"),
        IssueType(
            "INTERVAL_BEYOND_DEPTH",
            _W,
            _NONE,
            "Interval below hole depth",
            "Aralık kuyu derinliğinin altında",
        ),
        IssueType(
            "POINT_DEPTH_INVALID", _E, _ROW, "Invalid point depth", "Geçersiz nokta derinliği"
        ),
        IssueType(
            "VALUE_OUT_OF_RANGE",
            _W,
            _NONE,
            "Value outside its valid range",
            "Değer geçerli aralığın dışında",
        ),
        IssueType(
            "VALUE_UNREADABLE",
            _W,
            _NONE,
            "Unreadable value, kept as missing",
            "Okunamayan değer, eksik sayıldı",
        ),
        IssueType(
            "ROW_WIDTH",
            _W,
            _NONE,
            "Rows with a different number of fields",
            "Alan sayısı farklı satırlar",
        ),
        IssueType(
            "VALUE_QUALIFIED",
            _I,
            _NONE,
            "Qualified values (below detection, missing ...)",
            "Nitelikli değerler (tespit sınırı altı, eksik ...)",
        ),
    )
}


@dataclass(frozen=True)
class Issue:
    """One finding. ``src_line`` is the 1-based line in the table's source file."""

    code: str
    table: str
    message: str
    hole_id: str | None = None
    src_line: int | None = None
    field: str | None = None
    row: int | None = None

    def __post_init__(self) -> None:
        if self.code not in CATALOGUE:
            raise ValueError(f"unknown issue code {self.code!r}")

    @property
    def severity(self) -> Severity:
        return CATALOGUE[self.code].severity

    @property
    def effect(self) -> Effect:
        return CATALOGUE[self.code].effect

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "effect": self.effect.value,
            "table": self.table,
            "hole_id": self.hole_id,
            "src_line": self.src_line,
            "field": self.field,
            "message": self.message,
        }
