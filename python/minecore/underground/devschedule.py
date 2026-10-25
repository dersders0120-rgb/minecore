"""Development schedule: when the decline reaches each level and when the
level is ready for stoping.

Rules (a rule-based schedule, not optimised):

* one decline heading advances from the portal at ``decline_rate`` m/year
  along the designed centreline; the decline reaches a level when it passes
  the centreline point of that level's crosscut;
* ``level_crews`` crews develop the levels (crosscut plus ore drive) at
  ``level_rate`` m/year each, in the order the decline reaches them; a crew
  starts a level when the decline has reached it and the crew is free;
* a level is ready for stoping ``lead_years`` after its development ends
  (drawpoints, services, cable bolting);
* development cost = metres x ``cost_per_m``, spent in the years the metres
  are driven.

No ventilation or ore-pass raises, no passing bays, no rate ramp-up, no
interaction between headings beyond the order above.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from minecore.underground.development import Development
from minecore.underground.stopes import StopeError


@dataclass(frozen=True)
class DevRates:
    decline_rate: float = 1200.0  # m/year, one heading
    level_rate: float = 1000.0  # m/year per crew
    level_crews: int = 1
    lead_years: float = 0.25
    cost_per_m: float = 6000.0  # currency per metre of development

    def __post_init__(self) -> None:
        if not (self.decline_rate > 0 and self.level_rate > 0):
            raise StopeError("development rates must be > 0")
        if self.level_crews < 1:
            raise StopeError("at least one level crew is needed")
        if not (math.isfinite(self.lead_years) and self.lead_years >= 0):
            raise StopeError("the stoping lead time must be >= 0")
        if not (math.isfinite(self.cost_per_m) and self.cost_per_m >= 0):
            raise StopeError("the development cost per metre must be >= 0")


@dataclass(frozen=True)
class LevelTiming:
    elevation: float
    metres: float  # crosscut + ore drive
    reached: float  # years from the start: decline at the level
    start: float  # level development starts
    finish: float  # level development ends
    ready: float  # ready for stoping


@dataclass(frozen=True)
class DevYear:
    year: int  # 1-based
    decline_m: float
    level_m: float
    cost: float


@dataclass(frozen=True)
class DevSchedule:
    levels: tuple[LevelTiming, ...]  # in the order the decline reaches them
    years: tuple[DevYear, ...]
    rates: DevRates

    def costs(self) -> dict[int, float]:
        return {y.year: y.cost for y in self.years}

    def ready_year(self, elevation: float) -> float:
        """Ready time of the level nearest to ``elevation`` (years)."""
        if not self.levels:
            raise StopeError("the development has no levels")
        best = min(self.levels, key=lambda lv: abs(lv.elevation - elevation))
        return best.ready


def _spread(start: float, finish: float, metres: float, into: dict[int, float]) -> None:
    """Add ``metres`` driven evenly over [start, finish) to calendar years."""
    if metres <= 0:
        return
    if finish <= start:
        into[int(start) + 1] = into.get(int(start) + 1, 0.0) + metres
        return
    rate = metres / (finish - start)
    t = start
    while t < finish - 1e-12:
        end = min(math.floor(t) + 1.0, finish)
        year = math.floor(t) + 1
        into[year] = into.get(year, 0.0) + rate * (end - t)
        t = end


def schedule_development(dev: Development, rates: DevRates) -> DevSchedule:
    line = dev.decline
    along = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(line, axis=0), axis=1))])
    reach = []
    for a in dev.accesses:
        k = int(np.argmin(np.linalg.norm(line - a.start, axis=1)))
        reach.append((float(along[k]) / rates.decline_rate, a))
    reach.sort(key=lambda r: r[0])
    free = [0.0] * rates.level_crews
    levels = []
    level_m: dict[int, float] = {}
    for t, a in reach:
        crew = int(np.argmin(free))
        metres = a.length + a.drive_length
        start = max(t, free[crew])
        finish = start + metres / rates.level_rate
        free[crew] = finish
        _spread(start, finish, metres, level_m)
        levels.append(LevelTiming(a.elevation, metres, t, start, finish, finish + rates.lead_years))
    decline_m: dict[int, float] = {}
    _spread(0.0, dev.decline_length / rates.decline_rate, dev.decline_length, decline_m)
    years = []
    for y in range(1, max([*decline_m, *level_m, 1]) + 1):
        d, lv = decline_m.get(y, 0.0), level_m.get(y, 0.0)
        years.append(DevYear(y, d, lv, (d + lv) * rates.cost_per_m))
    return DevSchedule(tuple(levels), tuple(years), rates)
