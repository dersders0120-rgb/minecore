"""Truck and loader fleet by schedule period (first-pass equipment sizing).

For each period the haul is: an in-pit leg, the ramp (vertical lift from the
mean mining elevation of that period to the pit exit, divided by the ramp
gradient) and a surface leg to the dump/plant. Cycle time = spot + load +
loaded travel + dump + empty travel, with separate loaded/empty speeds on the
ramp and on flat roads. Truck hours = trips x cycle; the fleet is the hours
divided by the effective hours per truck (calendar hours x availability x
utilisation), rounded up. Loaders are sized from their effective rate.

It is a deterministic average-cycle estimate (no queueing/match-factor
simulation, no rimpull curves, one material destination per period).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult
from minecore.pitopt.scheduling import ConstrainedSchedule


@dataclass(frozen=True)
class Fleet:
    payload_t: float = 90.0
    ramp_gradient: float = 0.10
    in_pit_m: float = 300.0  # flat haul inside the pit, per trip one way
    surface_m: float = 1500.0  # pit exit to dump/plant, one way
    loaded_ramp_kmh: float = 11.0
    empty_ramp_kmh: float = 25.0
    loaded_flat_kmh: float = 30.0
    empty_flat_kmh: float = 40.0
    spot_load_min: float = 3.5  # spotting + loading
    dump_min: float = 1.5  # manoeuvre + dump
    availability: float = 0.85
    utilisation: float = 0.80
    hours_per_year: float = 8760.0
    truck_cost_h: float = 180.0
    loader_tph: float = 1800.0  # nominal loader rate
    loader_cost_h: float = 400.0

    def __post_init__(self) -> None:
        if min(self.payload_t, self.ramp_gradient, self.loader_tph) <= 0:
            raise PitOptError("payload, ramp gradient and loader rate must be > 0")
        speeds = (self.loaded_ramp_kmh, self.empty_ramp_kmh, self.loaded_flat_kmh)
        if min(*speeds, self.empty_flat_kmh) <= 0:
            raise PitOptError("speeds must be > 0")
        if not (0 < self.availability <= 1 and 0 < self.utilisation <= 1):
            raise PitOptError("availability and utilisation must be in (0, 1]")

    @property
    def effective_hours(self) -> float:
        return self.hours_per_year * self.availability * self.utilisation

    def cycle_min(self, lift_m: float) -> float:
        ramp = max(lift_m, 0.0) / self.ramp_gradient * math.sqrt(1 + self.ramp_gradient**2)
        flat = self.in_pit_m + self.surface_m

        def minutes(metres: float, kmh: float) -> float:
            return metres / 1000.0 / kmh * 60.0

        travel = minutes(ramp, self.loaded_ramp_kmh) + minutes(ramp, self.empty_ramp_kmh)
        travel += minutes(flat, self.loaded_flat_kmh) + minutes(flat, self.empty_flat_kmh)
        return self.spot_load_min + self.dump_min + travel


@dataclass(frozen=True)
class FleetRow:
    period: int
    mined_t: float
    lift_m: float
    haul_m: float  # one-way haul length
    cycle_min: float
    trucks: int
    loaders: int
    truck_hours: float
    cost: float

    @property
    def cost_per_t(self) -> float:
        return self.cost / self.mined_t if self.mined_t > 0 else math.nan


def period_lifts(
    result: PitResult,
    schedule: ConstrainedSchedule,
    bench_height: float,
    exit_bench: float = 0.0,
) -> dict[int, float]:
    """Tonnage-weighted mean vertical lift (m) to the pit exit of the blocks
    whose increment starts in each period (bench 0 = top of the model)."""
    period: npt.NDArray[np.int64] = schedule.period
    out: dict[int, float] = {}
    for p in np.unique(period[period > 0]):
        sel = period == p
        t = result.tonnes[sel]
        depth = (result.bench[sel] + 0.5 - exit_bench) * bench_height
        out[int(p)] = float((depth * t).sum() / t.sum()) if t.sum() > 0 else 0.0
    return out


def fleet_by_period(
    schedule: ConstrainedSchedule, lifts: dict[int, float], fleet: Fleet
) -> list[FleetRow]:
    rows = []
    last = 0.0
    for r in schedule.rows:
        lift = lifts.get(r.period, last)
        last = lift
        cycle = fleet.cycle_min(lift)
        trips = r.mined_t / fleet.payload_t
        hours = trips * cycle / 60.0
        trucks = math.ceil(hours / fleet.effective_hours - 1e-9) if hours > 0 else 0
        loader_hours = r.mined_t / fleet.loader_tph
        loaders = math.ceil(loader_hours / fleet.effective_hours - 1e-9) if loader_hours else 0
        ramp = lift / fleet.ramp_gradient * math.sqrt(1 + fleet.ramp_gradient**2)
        cost = hours * fleet.truck_cost_h + loader_hours * fleet.loader_cost_h
        rows.append(
            FleetRow(
                r.period,
                r.mined_t,
                lift,
                fleet.in_pit_m + ramp + fleet.surface_m,
                cycle,
                trucks,
                loaders,
                hours,
                cost,
            )
        )
    return rows
