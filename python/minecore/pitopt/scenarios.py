"""Saved pit scenarios: the inputs and headline results of one pit study, kept in
the project so several cases can be compared side by side.

Scenarios live in the project settings under ``pit_scenarios`` (a JSON list),
so saving one is an undoable operation and needs no schema change. Inputs
are stored as the form values that produced the case (enough to reload it);
results are plain numbers, so a later change of algorithm does not alter a
saved case.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minecore.pitopt.cashflow import CashFlow
from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult
from minecore.pitopt.scheduling import ConstrainedSchedule
from minecore.project import Project

KEY = "pit_scenarios"

# (result key, label, scale divisor, decimals) in comparison order.
METRICS: tuple[tuple[str, str, float, int], ...] = (
    ("final_shell", "Final kabuk", 1.0, 0),
    ("revenue_factor", "Gelir çarpanı", 1.0, 2),
    ("ore_t", "Cevher (Mt)", 1e6, 2),
    ("waste_t", "Pasa (Mt)", 1e6, 2),
    ("strip_ratio", "Dekapaj oranı", 1.0, 2),
    ("grade", "Ortalama tenör", 1.0, 3),
    ("metal", "Metal", 1.0, 0),
    ("pushbacks", "Pushback sayısı", 1.0, 0),
    ("years", "Ömür (yıl)", 1.0, 0),
    ("schedule_npv", "Çizelge NPV (M$)", 1e6, 1),
    ("project_npv", "Proje NPV (M$)", 1e6, 1),
    ("irr", "İVO (IRR) %", 0.01, 1),
    ("payback", "Geri ödeme (yıl)", 1.0, 1),
    ("undiscounted", "İskontosuz nakit (M$)", 1e6, 1),
    ("schedule_method", "Çizelge yöntemi", 1.0, 0),
)


@dataclass(frozen=True)
class Scenario:
    name: str
    saved_at: str
    model: str
    inputs: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "saved_at": self.saved_at,
            "model": self.model,
            "inputs": self.inputs,
            "results": self.results,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Scenario:
        return cls(
            str(data["name"]),
            str(data.get("saved_at", "")),
            str(data.get("model", "")),
            dict(data.get("inputs", {})),
            dict(data.get("results", {})),
        )


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def results_of(
    pit: PitResult,
    final_shell: int,
    phases: int,
    schedule: ConstrainedSchedule | None,
    flow: CashFlow | None,
) -> dict[str, Any]:
    """Headline numbers of a pit study (JSON-ready; missing values are None)."""
    row = pit.rows[final_shell - 1]
    out: dict[str, Any] = {
        "final_shell": final_shell,
        "revenue_factor": row.revenue_factor,
        "ore_t": row.ore_t,
        "waste_t": row.waste_t,
        "strip_ratio": _finite(row.strip_ratio),
        "grade": _finite(row.grade),
        "metal": row.metal,
        "pushbacks": phases,
        "years": None,
        "schedule_npv": None,
        "project_npv": None,
        "irr": None,
        "payback": None,
        "undiscounted": None,
        "schedule_method": None,
    }
    if schedule is not None:
        out["years"] = len(schedule.rows)
        out["schedule_npv"] = schedule.npv
        out["schedule_method"] = getattr(schedule, "method", "greedy")
    if flow is not None:
        out["project_npv"] = flow.npv
        out["irr"] = _finite(flow.irr())
        out["payback"] = _finite(flow.payback())
        out["undiscounted"] = flow.undiscounted
    return out


def list_scenarios(project: Project) -> list[Scenario]:
    return [Scenario.from_json(d) for d in project.get_setting(KEY, [])]


def save_scenario(project: Project, scenario: Scenario) -> None:
    """Add a scenario, replacing one of the same name."""
    if not scenario.name.strip():
        raise PitOptError("a scenario needs a name")
    kept = [s for s in list_scenarios(project) if s.name != scenario.name]
    kept.append(scenario)
    project.set_setting(KEY, [s.to_json() for s in kept])


def delete_scenario(project: Project, name: str) -> None:
    kept = [s for s in list_scenarios(project) if s.name != name]
    if len(kept) == len(list_scenarios(project)):
        raise PitOptError(f"no scenario named {name!r}")
    project.set_setting(KEY, [s.to_json() for s in kept])


def format_value(key: str, value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    for k, _, scale, decimals in METRICS:
        if k == key:
            return f"{float(value) / scale:,.{decimals}f}"
    return f"{value}"


def comparison(scenarios: list[Scenario]) -> list[list[str]]:
    """Rows of a side-by-side table: header row, then one row per metric."""
    table = [["", *(s.name for s in scenarios)]]
    table.append(["Model", *(s.model for s in scenarios)])
    for key, label, _, _ in METRICS:
        table.append([label, *(format_value(key, s.results.get(key)) for s in scenarios)])
    return table


def write_comparison(path: Path, scenarios: list[Scenario]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(comparison(scenarios))
