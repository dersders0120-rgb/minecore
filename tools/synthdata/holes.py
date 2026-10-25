from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import asdict
from math import ceil, cos, exp, log, radians, sin, sqrt

import numpy as np

from synthdata.config import (
    LITH_DESCRIPTIONS,
    Collar,
    Config,
    Dataset,
    LithInterval,
    Plane,
    Sample,
    Station,
)
from synthdata.desurvey import position_at
from synthdata.geology import make_plane, make_topography

Vec = tuple[float, float, float]


def generate(config: Config) -> Dataset:  # noqa: PLR0915 (linear pipeline)
    rng = np.random.default_rng(config.seed)
    topo = make_topography(rng)

    spacing = float(rng.uniform(25.0, 50.0))
    cols = max(1, ceil(sqrt(config.holes)))
    rows = max(1, ceil(config.holes / cols))

    collars: list[Collar] = []
    dd_count = 0
    rc_count = 0
    for i in range(config.holes):
        col = i % cols
        row = i // cols
        x = topo.xc + (col - (cols - 1) / 2.0) * spacing + float(rng.uniform(-5.0, 5.0))
        y = topo.yc + (row - (rows - 1) / 2.0) * spacing + float(rng.uniform(-5.0, 5.0))
        z = round(topo.z(x, y), 2)
        x = round(x, 3)
        y = round(y, 3)

        is_rc = bool(rng.random() < 0.25)
        if is_rc:
            rc_count += 1
            hole_id = f"RC{rc_count:04d}"
            hole_type = "RC"
        else:
            dd_count += 1
            hole_id = f"DH{dd_count:04d}"
            hole_type = "DD"
        collars.append(Collar(hole_id, x, y, z, 0.0, hole_type))

    first = collars[0]
    plane = make_plane(rng, (first.x, first.y, first.z))

    updated_collars: list[Collar] = []
    surveys: dict[str, list[Station]] = {}
    lithology: dict[str, list[LithInterval]] = {}

    for collar in collars:
        if collar.hole_type == "DD" and rng.random() < 0.10:
            az0 = 0.0
            dip0 = -90.0
        else:
            az0 = (plane.dip_dir + 180.0 + float(rng.uniform(-15.0, 15.0))) % 360.0
            dip0 = float(rng.uniform(-75.0, -50.0))
        az0, dip0 = round(az0, 1) % 360.0, round(dip0, 1)

        if collar.hole_type == "RC":
            total = _total_for_straight(collar, az0, dip0, plane, rng)
            stations = [Station(0.0, az0, dip0)]
        else:
            long_stations = _long_stations(rng, az0, dip0)
            cross = _find_crossing(collar, long_stations, plane, plane.thickness, 600.0)
            if cross is not None and cross < 450.0:
                total = cross + float(rng.uniform(20.0, 60.0))
            else:
                total = float(rng.uniform(150.0, 300.0))
            stations = _truncate_stations(long_stations, total)
        total = round(total, 2)

        new_collar = Collar(
            collar.hole_id,
            collar.x,
            collar.y,
            collar.z,
            round(total, 2),
            collar.hole_type,
        )
        updated_collars.append(new_collar)
        surveys[new_collar.hole_id] = stations
        lithology[new_collar.hole_id] = _make_lithology(
            new_collar,
            stations,
            plane,
            total,
            rng,
        )

    samples = _make_samples(updated_collars, surveys, lithology, plane, rng)

    truth: dict[str, object] = {
        "seed": config.seed,
        "holes": config.holes,
        "errors": config.errors,
        "topography": asdict(topo),
        "ore_plane": asdict(plane),
        "lithology_descriptions": LITH_DESCRIPTIONS,
        "detection_limit_au_ppm": 0.005,
        "counts": {
            "collars": len(updated_collars),
            "samples": len(samples),
            "dd": sum(1 for c in updated_collars if c.hole_type == "DD"),
            "rc": sum(1 for c in updated_collars if c.hole_type == "RC"),
        },
    }

    return Dataset(
        seed=config.seed,
        errors=config.errors,
        topo=topo,
        plane=plane,
        collars=updated_collars,
        surveys=surveys,
        lithology=lithology,
        samples=samples,
        truth=truth,
    )


def _long_stations(rng: np.random.Generator, az0: float, dip0: float) -> list[Station]:
    # Stations carry exactly the precision written to survey.csv, so the truth
    # computed from them is reproducible from the files.
    stations = [Station(0.0, round(az0, 1) % 360.0, round(dip0, 1))]
    depth = 0.0
    while depth < 600.0:
        step = 30.0 + float(rng.uniform(-3.0, 3.0))
        depth = round(min(600.0, depth + step), 2)
        last = stations[-1]
        if abs(last.dip + 90.0) < 1e-9:
            az = last.azimuth
            dip = -90.0
        else:
            az = (last.azimuth + float(rng.uniform(-1.5, 1.5))) % 360.0
            dip = max(-89.9, min(-5.0, last.dip + float(rng.uniform(-1.5, 1.5))))
        stations.append(Station(depth, round(az, 1) % 360.0, round(dip, 1)))
    return stations


def _truncate_stations(stations: list[Station], total: float) -> list[Station]:
    total = round(total, 2)
    kept = [s for s in stations if s.depth <= total + 1e-9]
    if not kept:
        kept = [Station(0.0, stations[0].azimuth, stations[0].dip)]
    if kept[-1].depth < total - 1e-9:
        last = kept[-1]
        kept.append(Station(total, last.azimuth, last.dip))
    return kept


def _total_for_straight(
    collar: Collar,
    azimuth: float,
    dip: float,
    plane: Plane,
    rng: np.random.Generator,
) -> float:
    d = _direction(azimuth, dip)
    s0 = plane.signed(collar.x, collar.y, collar.z)
    rate = plane.normal[0] * d[0] + plane.normal[1] * d[1] + plane.normal[2] * d[2]
    if abs(rate) > 1e-12:
        cross = (plane.thickness - s0) / rate
        if 0.0 < cross < 500.0:
            return cross + float(rng.uniform(20.0, 60.0))
    return float(rng.uniform(150.0, 300.0))


def _direction(azimuth: float, dip: float) -> Vec:
    a = radians(azimuth)
    d = radians(dip)
    return (cos(d) * sin(a), cos(d) * cos(a), sin(d))


def _find_crossing(
    collar: Collar,
    stations: Sequence[Station],
    plane: Plane,
    target: float,
    total: float,
) -> float | None:
    depths = sorted({0.0, total} | {s.depth for s in stations if s.depth <= total})
    prev_d = depths[0]
    prev_f = plane.signed(*position_at(stations, collar, prev_d)) - target
    if abs(prev_f) < 1e-9:
        return prev_d
    for depth in depths[1:]:
        f = plane.signed(*position_at(stations, collar, depth)) - target
        if abs(f) < 1e-9:
            return depth
        if prev_f * f < 0.0:
            lo = prev_d
            hi = depth
            flo = prev_f
            for _ in range(60):
                if hi - lo <= 0.0005:
                    break
                mid = (lo + hi) / 2.0
                fm = plane.signed(*position_at(stations, collar, mid)) - target
                if flo * fm <= 0.0:
                    hi = mid
                else:
                    lo = mid
                    flo = fm
            return (lo + hi) / 2.0
        prev_d = depth
        prev_f = f
    return None


def _make_lithology(
    collar: Collar,
    stations: list[Station],
    plane: Plane,
    total: float,
    rng: np.random.Generator,
) -> list[LithInterval]:
    ob = float(rng.uniform(2.0, 15.0))
    breaks = [0.0, min(ob, total), total]
    for target in (0.0, plane.thickness):
        depth = _find_crossing(collar, stations, plane, target, total)
        if depth is not None and 1e-9 < depth < total - 1e-9:
            breaks.append(depth)
    breaks = sorted(breaks)
    unique: list[float] = []
    for value in breaks:
        if not unique or abs(value - unique[-1]) > 1e-9:
            unique.append(value)

    intervals: list[LithInterval] = []
    for a, b in itertools.pairwise(unique):
        if b - a < 1e-9:
            continue
        mid = (a + b) / 2.0
        pos = position_at(stations, collar, mid)
        s = plane.signed(*pos)
        if mid < ob - 1e-9:
            code = "OB"
        elif s < 0.0:
            code = "AND"
        elif s <= plane.thickness:
            code = "QV"
        else:
            code = "SCH"
        desc = LITH_DESCRIPTIONS[code]
        if intervals and intervals[-1].lith_code == code:
            prev = intervals[-1]
            intervals[-1] = LithInterval(collar.hole_id, prev.from_m, b, code, desc)
        else:
            intervals.append(LithInterval(collar.hole_id, a, b, code, desc))
    return intervals


def _make_samples(
    collars: list[Collar],
    surveys: dict[str, list[Station]],
    lithology: dict[str, list[LithInterval]],
    plane: Plane,
    rng: np.random.Generator,
) -> list[Sample]:
    raw: list[tuple[str, float, float]] = []
    collar_by_id = {c.hole_id: c for c in collars}

    for collar in collars:
        hole_id = collar.hole_id
        total = collar.total_depth
        liths = lithology[hole_id]
        start = liths[0].to_m if liths and liths[0].lith_code == "OB" else 0.0
        if start >= total - 1e-9:
            continue
        length = 2.0 if collar.hole_type == "RC" else 1.0
        for lith in liths:
            lf = max(lith.from_m, start)
            lt = lith.to_m
            if lt <= lf + 1e-9:
                continue
            cur = lf
            while cur < lt - 1e-9:
                nxt = min(cur + length, lt)
                if nxt - cur < 0.3 and raw and raw[-1][0] == hole_id:
                    h, f, _ = raw[-1]
                    raw[-1] = (h, f, nxt)
                    cur = nxt
                    continue
                raw.append((hole_id, cur, nxt))
                cur = nxt

    i = len(raw) - 1
    while i > 0:
        h, f, t = raw[i]
        if t - f < 0.3 and raw[i - 1][0] == h:
            ph, pf, _ = raw[i - 1]
            raw[i - 1] = (ph, pf, t)
            del raw[i]
        i -= 1

    samples: list[Sample] = []
    for index, (hole_id, from_m, to_m) in enumerate(raw, start=1):
        mid_depth = (from_m + to_m) / 2.0
        collar = collar_by_id[hole_id]
        x, y, z = position_at(surveys[hole_id], collar, mid_depth)
        inside = plane.inside(x, y, z)
        au, cu = _grades(rng, inside)
        samples.append(
            Sample(
                hole_id=hole_id,
                from_m=from_m,
                to_m=to_m,
                sample_id=f"S{index:06d}",
                au_ppm=au,
                cu_pct=cu,
                mid_x=x,
                mid_y=y,
                mid_z=z,
                inside=inside,
            )
        )
    return samples


def _grades(rng: np.random.Generator, inside: bool) -> tuple[float, float]:
    if inside:
        mu_au = log(2.5)
        mu_cu = log(0.4)
        sd_au = 0.8
        sd_cu = 0.5
    else:
        mu_au = log(0.05)
        mu_cu = log(0.02)
        sd_au = 0.5
        sd_cu = 0.5
    cov = 0.6 * sd_au * sd_cu
    mean = [mu_au, mu_cu]
    matrix = [[sd_au * sd_au, cov], [cov, sd_cu * sd_cu]]
    values = rng.multivariate_normal(mean, matrix)
    return exp(float(values[0])), exp(float(values[1]))
