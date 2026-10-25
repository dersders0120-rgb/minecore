> **Provenance (architect, 2026-10-24).** Produced by ChatGPT for task T-009 and
> committed unchanged below this note. Independently checked by the architect
> with a different formulation (industry ratio-factor formula on the partial arc,
> partial tangent by Rodrigues rotation, stdlib only) and, for C03, closed-form
> circle geometry: maximum deviation 7.8e-7 m over all 55 points, which is the
> rounding of the published 1e-6 m values. The check runs in CI as
> `tests/python/test_reference_desurvey.py`. Kernel tests must use a tolerance
> of at least 1e-6 m against this file.

# T-009 — Independent desurvey reference values

## 1. Scope and coordinate conventions

This report supplies independent reference coordinates for the MineCore desurvey kernel. All calculations use the conventions supplied in the task:

- X is east, Y is north, Z is up; coordinates are metres.
- Azimuth is measured clockwise from north in degrees, in the range [0, 360).
- Dip is in degrees; negative values point below horizontal, and -90° is vertically down.
- Measured depth (MD) is distance along the hole from the collar.
- Between survey stations the hole follows the circular arc defined by minimum curvature.
- Before the first survey station, the first station direction applies from the collar. Beyond the last station, the final direction continues straight.

`cases.csv` contains one row per sampled depth (55 rows in total), with the requested columns. The pipe character (`|`) separates values in station arrays. Collar coordinates and output XYZ coordinates are rendered to 1e-6 m. The method-comparison errors below are calculated before coordinate rounding.

## 2. Test input inventory

| Case | Collar (X, Y, Z) m | Station depths MD (m) | Azimuths (°) | Dips (°) | Sample depths MD (m) |
|---|---|---|---|---|---|
| `C01_VERTICAL` | `500000.000, 4500000.000, 1000.000` | `0 | 100 | 200` | `0 | 0 | 0` | `-90 | -90 | -90` | `0, 25, 100, 150, 200, 250` |
| `C02_CONSTANT_INCLINE` | `500120.000, 4500120.000, 985.000` | `0 | 80 | 200` | `45 | 45 | 45` | `-60 | -60 | -60` | `0, 40, 80, 120, 200, 240` |
| `C03_PURE_BUILD` | `500250.000, 4500250.000, 1020.000` | `0 | 100` | `90 | 90` | `-90 | -60` | `0, 25, 50, 75, 100, 125` |
| `C04_PURE_TURN` | `499900.000, 4499900.000, 995.000` | `0 | 150` | `0 | 90` | `-45 | -45` | `0, 37.5, 75, 112.5, 150, 180` |
| `C05_BUILD_TURN_4_STATIONS` | `500500.000, 4500500.000, 1035.000` | `0 | 37 | 103 | 181` | `20 | 38 | 72 | 110` | `-85 | -78 | -67 | -72` | `0, 18.5, 37, 70, 103, 142, 181, 210` |
| `C06_AZIMUTH_WRAP` | `500750.000, 4500750.000, 1015.000` | `0 | 120` | `350 | 10` | `-70 | -70` | `0, 30, 60, 90, 120, 145` |
| `C07_ZERO_DOGLEG_INTERVALS` | `499750.000, 4499750.000, 1005.000` | `0 | 40 | 90 | 140` | `30 | 30 | 30 | 65` | `-60 | -60 | -60 | -60` | `0, 20, 40, 65, 90, 115, 140, 160` |
| `C08_STATION_BOUNDARIES` | `501000.000, 4501000.000, 990.000` | `20 | 80 | 130` | `0 | 45 | 60` | `-90 | -70 | -65` | `0, 10, 20, 45, 80, 100, 130, 150, 175` |

The case definitions intentionally include UTM-like collars in all eight cases. C05 has four survey stations with irregular spacing (0, 37, 103, and 181 m). C08 has its first station at 20 m so the pre-first-station rule can be exercised, and samples at exact station depths, between stations, and beyond the last station.

## 3. Derivation

### 3.1 Direction vector

For azimuth `A` and dip `D`, convert angles to radians and form the unit tangent in the required east/north/up coordinate system:

```text
t(A,D) = (cos(D) sin(A), cos(D) cos(A), sin(D))
```

The implementation normalizes this vector against floating-point roundoff and handles a vertical dip explicitly so that a vertical hole has exactly zero X and Y tangent components.

### 3.2 Dogleg and ratio factor

For consecutive tangents `t0` and `t1`, the smaller included dogleg angle is

```text
theta = acos( clamp(dot(t0, t1), -1, 1) )
RF = tan(theta / 2) / (theta / 2)
```

The zero-dogleg limit is `RF = 1`. The vector formulation naturally handles an azimuth wrap from 350° to 10°; it does not subtract azimuths naively and accidentally choose a 340° rotation.

### 3.3 Closed-form partial-arc displacement

Let `L` be the MD length of a station interval. For nonzero dogleg, define the unit in-plane vector

```text
u = (t1 - cos(theta) t0) / sin(theta)
q = s / L
```

where `s` is the partial MD length from the interval start. Integrating the circular-arc tangent gives the closed-form displacement

```text
delta_r(s) = (L / theta) * [ sin(q theta) t0 + (1 - cos(q theta)) u ]
```

For zero dogleg, use the limiting straight-line result `delta_r(s) = s * t0`. At a full interval (`s = L`), the same closed form is algebraically equivalent to the standard minimum-curvature expression

```text
delta_r(L) = (L / 2) * RF * (t0 + t1)
```

Station positions are accumulated from the collar. For a query before the first station, the implementation uses `collar + depth * t_first`. For a query after the last station, it uses the final station position plus `(depth - last_MD) * t_last`.

### 3.4 Independent numerical integration

The second method constructs the spherical-linear interpolation (slerp) tangent along each station interval:

```text
t(f) = cos(f theta) t0 + sin(f theta) u,   0 <= f <= 1
```

It integrates that tangent numerically with composite Simpson integration using 20,000 (even) panels for every partial or full arc traversed by each query. The numerical position routine independently accumulates numerical arc integrals from the collar; it does not use the analytic station positions. Straight pre-first and post-last portions follow the same specified continuation rules. The closed-form and numerical position paths are compared as unrounded 3D vectors.

## 4. Dogleg angles and ratio factors

| Case | Interval MD (m) | Length (m) | Dogleg (°) | Ratio factor |
|---|---:|---:|---:|---:|
| `C01_VERTICAL` | 0.000000-100.000000 | 100.000000 | 0.000000000000 | 1.000000000000000 |
| `C01_VERTICAL` | 100.000000-200.000000 | 100.000000 | 0.000000000000 | 1.000000000000000 |
| `C02_CONSTANT_INCLINE` | 0.000000-80.000000 | 80.000000 | 0.000000000000 | 1.000000000000000 |
| `C02_CONSTANT_INCLINE` | 80.000000-200.000000 | 120.000000 | 0.000000000000 | 1.000000000000000 |
| `C03_PURE_BUILD` | 0.000000-100.000000 | 100.000000 | 30.000000000000 | 1.023490523349472 |
| `C04_PURE_TURN` | 0.000000-150.000000 | 150.000000 | 60.000000000000 | 1.102657790843584 |
| `C05_BUILD_TURN_4_STATIONS` | 0.000000-37.000000 | 37.000000 | 7.405292296179 | 1.001394389158946 |
| `C05_BUILD_TURN_4_STATIONS` | 37.000000-103.000000 | 66.000000 | 14.593306540705 | 1.005441362804770 |
| `C05_BUILD_TURN_4_STATIONS` | 103.000000-181.000000 | 78.000000 | 13.928093806169 | 1.004953715246265 |
| `C06_AZIMUTH_WRAP` | 0.000000-120.000000 | 120.000000 | 6.809734642322 | 1.001178820798222 |
| `C07_ZERO_DOGLEG_INTERVALS` | 0.000000-40.000000 | 40.000000 | 0.000000000000 | 1.000000000000000 |
| `C07_ZERO_DOGLEG_INTERVALS` | 40.000000-90.000000 | 50.000000 | 0.000000000000 | 1.000000000000000 |
| `C07_ZERO_DOGLEG_INTERVALS` | 90.000000-140.000000 | 50.000000 | 17.294756325045 | 1.007662632052397 |
| `C08_STATION_BOUNDARIES` | 20.000000-80.000000 | 60.000000 | 20.000000000000 | 1.010279180887973 |
| `C08_STATION_BOUNDARIES` | 80.000000-130.000000 | 50.000000 | 7.576595603876 | 1.001459761193831 |

## 5. Numerical agreement

Metric: maximum 3D Euclidean distance `norm(position_analytic - position_simpson)` across all sampled depths in each case. These are measured outputs from running the included code.

| Case | Sampled points | Maximum disagreement (m) | Requirement (m) | Result |
|---|---:|---:|---:|---|
| `C01_VERTICAL` | 6 | 0.000000000000e+00 | `1.0e-09` | **PASS** |
| `C02_CONSTANT_INCLINE` | 6 | 0.000000000000e+00 | `1.0e-09` | **PASS** |
| `C03_PURE_BUILD` | 6 | 4.547473508865e-13 | `1.0e-09` | **PASS** |
| `C04_PURE_TURN` | 6 | 7.958078640513e-13 | `1.0e-09` | **PASS** |
| `C05_BUILD_TURN_4_STATIONS` | 8 | 2.273736754432e-13 | `1.0e-09` | **PASS** |
| `C06_AZIMUTH_WRAP` | 6 | 7.958078640513e-13 | `1.0e-09` | **PASS** |
| `C07_ZERO_DOGLEG_INTERVALS` | 8 | 2.273736754432e-13 | `1.0e-09` | **PASS** |
| `C08_STATION_BOUNDARIES` | 9 | 0.000000000000e+00 | `1.0e-09` | **PASS** |

**Overall maximum disagreement:** `7.958078640513e-13 m`. All eight cases pass the required `1e-9 m` limit. The two zero-error rows are exact under the implemented straight-line paths; the nonzero differences are at floating-point/integration-roundoff scale.

## 6. Reproduction instructions

Save the code below as `refgen.py`, install NumPy if needed (`python -m pip install numpy`), and run `python refgen.py`. It writes `cases.csv` in the current working directory, prints the per-case maximum disagreements and dogleg/RF table, and raises an assertion if any case exceeds `1e-9 m`. The contents below are the code actually run to generate the attached CSV and the reported metrics.

```python
#!/usr/bin/env python3
"""Generate T-009 independent minimum-curvature desurvey reference cases.

Requirements: Python 3 and NumPy.
Run: python refgen.py
The script writes cases.csv in the current working directory and prints
per-case maximum disagreement between analytic arc positions and a composite
Simpson integration of the slerp-interpolated tangent.
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
import numpy as np

SIMPSON_N = 20_000  # even number of subintervals for composite Simpson integration
TOLERANCE_M = 1e-9


@dataclass(frozen=True)
class Case:
    case_id: str
    collar: tuple[float, float, float]
    station_depths: tuple[float, ...]
    station_azimuths: tuple[float, ...]
    station_dips: tuple[float, ...]
    sample_depths: tuple[float, ...]


CASES = (
    Case("C01_VERTICAL", (500000.0, 4500000.0, 1000.0),
         (0.0, 100.0, 200.0), (0.0, 0.0, 0.0), (-90.0, -90.0, -90.0),
         (0.0, 25.0, 100.0, 150.0, 200.0, 250.0)),
    Case("C02_CONSTANT_INCLINE", (500120.0, 4500120.0, 985.0),
         (0.0, 80.0, 200.0), (45.0, 45.0, 45.0), (-60.0, -60.0, -60.0),
         (0.0, 40.0, 80.0, 120.0, 200.0, 240.0)),
    Case("C03_PURE_BUILD", (500250.0, 4500250.0, 1020.0),
         (0.0, 100.0), (90.0, 90.0), (-90.0, -60.0),
         (0.0, 25.0, 50.0, 75.0, 100.0, 125.0)),
    Case("C04_PURE_TURN", (499900.0, 4499900.0, 995.0),
         (0.0, 150.0), (0.0, 90.0), (-45.0, -45.0),
         (0.0, 37.5, 75.0, 112.5, 150.0, 180.0)),
    Case("C05_BUILD_TURN_4_STATIONS", (500500.0, 4500500.0, 1035.0),
         (0.0, 37.0, 103.0, 181.0), (20.0, 38.0, 72.0, 110.0),
         (-85.0, -78.0, -67.0, -72.0),
         (0.0, 18.5, 37.0, 70.0, 103.0, 142.0, 181.0, 210.0)),
    Case("C06_AZIMUTH_WRAP", (500750.0, 4500750.0, 1015.0),
         (0.0, 120.0), (350.0, 10.0), (-70.0, -70.0),
         (0.0, 30.0, 60.0, 90.0, 120.0, 145.0)),
    Case("C07_ZERO_DOGLEG_INTERVALS", (499750.0, 4499750.0, 1005.0),
         (0.0, 40.0, 90.0, 140.0), (30.0, 30.0, 30.0, 65.0),
         (-60.0, -60.0, -60.0, -60.0),
         (0.0, 20.0, 40.0, 65.0, 90.0, 115.0, 140.0, 160.0)),
    Case("C08_STATION_BOUNDARIES", (501000.0, 4501000.0, 990.0),
         (20.0, 80.0, 130.0), (0.0, 45.0, 60.0), (-90.0, -70.0, -65.0),
         (0.0, 10.0, 20.0, 45.0, 80.0, 100.0, 130.0, 150.0, 175.0)),
)


def tangent(azimuth_deg: float, dip_deg: float) -> np.ndarray:
    """Unit tangent in X-east, Y-north, Z-up coordinates."""
    az = math.radians(azimuth_deg)
    dip = math.radians(dip_deg)
    horizontal = math.cos(dip)
    if abs(horizontal) < 1e-15:
        return np.array((0.0, 0.0, math.copysign(1.0, math.sin(dip))), dtype=float)
    vec = np.array((horizontal * math.sin(az),
                    horizontal * math.cos(az),
                    math.sin(dip)), dtype=float)
    return vec / np.linalg.norm(vec)


def dogleg(t0: np.ndarray, t1: np.ndarray) -> float:
    """Smaller included angle (radians) between two survey tangents."""
    return math.acos(float(np.clip(np.dot(t0, t1), -1.0, 1.0)))


def ratio_factor(theta: float) -> float:
    """Minimum-curvature ratio factor, with the zero-angle limit handled."""
    if abs(theta) < 1e-12:
        return 1.0
    return math.tan(theta / 2.0) / (theta / 2.0)


def analytic_arc(s: float, interval_length: float,
                 t0: np.ndarray, t1: np.ndarray) -> np.ndarray:
    """Closed-form integral of the circular-arc tangent from MD 0 to s."""
    if s <= 0.0:
        return np.zeros(3, dtype=float)
    theta = dogleg(t0, t1)
    if theta < 1e-12:
        return s * t0
    q = s / interval_length
    # Unit direction in the plane of the great-circle interpolation,
    # orthogonal to t0, pointing from t0 toward t1.
    u = (t1 - math.cos(theta) * t0) / math.sin(theta)
    return (interval_length / theta) * (
        math.sin(q * theta) * t0 + (1.0 - math.cos(q * theta)) * u
    )


def simpson_arc(s: float, interval_length: float,
                t0: np.ndarray, t1: np.ndarray,
                n: int = SIMPSON_N) -> np.ndarray:
    """Numerically integrate slerp(t0,t1,f) over measured depth 0..s.

    Composite Simpson rule is applied to f in [0, s/interval_length].
    This is kept separate from the closed-form arc calculation above.
    """
    if s <= 0.0:
        return np.zeros(3, dtype=float)
    theta = dogleg(t0, t1)
    if theta < 1e-12:
        return s * t0
    if n <= 0 or n % 2:
        raise ValueError("Composite Simpson integration requires positive even n")
    u = (t1 - math.cos(theta) * t0) / math.sin(theta)
    q = s / interval_length
    f = np.linspace(0.0, q, n + 1)
    tangents = (np.cos(theta * f)[:, None] * t0
                + np.sin(theta * f)[:, None] * u)
    weights = np.full(n + 1, 2.0)
    weights[1:-1:2] = 4.0
    weights[0] = weights[-1] = 1.0
    integral_df = (q / n) / 3.0 * np.sum(tangents * weights[:, None], axis=0)
    return interval_length * integral_df


def prepare_geometry(case: Case):
    """Prepare only input geometry; does not calculate any station positions."""
    depths = np.asarray(case.station_depths, dtype=float)
    tangents = [tangent(a, d) for a, d in zip(case.station_azimuths, case.station_dips)]
    collar = np.asarray(case.collar, dtype=float)
    if len(depths) < 1 or len(depths) != len(tangents):
        raise ValueError(f"{case.case_id}: inconsistent or empty station arrays")
    if any(depths[i + 1] <= depths[i] for i in range(len(depths) - 1)):
        raise ValueError(f"{case.case_id}: station depths must be strictly increasing")
    return depths, tangents, collar


def prepare(case: Case):
    depths, tangents, collar = prepare_geometry(case)
    # Position of each survey station from the collar, using analytic segments.
    station_positions = [collar + depths[0] * tangents[0]]
    for i in range(len(depths) - 1):
        length = depths[i + 1] - depths[i]
        station_positions.append(station_positions[-1] + analytic_arc(length, length,
                                                                      tangents[i], tangents[i + 1]))
    return depths, tangents, collar, station_positions


def analytic_position(case: Case, depth: float) -> np.ndarray:
    depths, tangents, collar, station_positions = prepare(case)
    if depth < 0:
        raise ValueError("Sample depth must be non-negative")
    if depth <= depths[0]:
        return collar + depth * tangents[0]
    for i in range(len(depths) - 1):
        if depth <= depths[i + 1]:
            return station_positions[i] + analytic_arc(depth - depths[i],
                                                        depths[i + 1] - depths[i],
                                                        tangents[i], tangents[i + 1])
    return station_positions[-1] + (depth - depths[-1]) * tangents[-1]


def numerical_position(case: Case, depth: float) -> np.ndarray:
    """Independent whole-path point using numerical integration for every arc."""
    depths, tangents, collar = prepare_geometry(case)
    if depth < 0:
        raise ValueError("Sample depth must be non-negative")
    if depth <= depths[0]:
        return collar + depth * tangents[0]
    position = collar + depths[0] * tangents[0]
    for i in range(len(depths) - 1):
        length = depths[i + 1] - depths[i]
        if depth <= depths[i + 1]:
            return position + simpson_arc(depth - depths[i], length,
                                          tangents[i], tangents[i + 1])
        position = position + simpson_arc(length, length, tangents[i], tangents[i + 1])
    return position + (depth - depths[-1]) * tangents[-1]


def fmt(value: float) -> str:
    """Render numeric values to the requested 1e-6 precision."""
    if abs(value) < 0.5e-6:
        value = 0.0
    return f"{value:.6f}"


def all_segment_rows(case: Case) -> list[tuple[str, float, float, float, float]]:
    depths, tangents, _ = prepare_geometry(case)
    rows = []
    for i in range(len(depths) - 1):
        theta = dogleg(tangents[i], tangents[i + 1])
        rows.append((f"{depths[i]:.6f}-{depths[i+1]:.6f}",
                     depths[i + 1] - depths[i], math.degrees(theta), ratio_factor(theta), theta))
    return rows


def generate_csv(path: Path) -> tuple[dict[str, float], list[dict[str, str]]]:
    fieldnames = ["case_id", "collar_x", "collar_y", "collar_z", "station_depths",
                  "station_azimuths", "station_dips", "depth", "x", "y", "z"]
    agreements: dict[str, float] = {}
    segment_summary: list[dict[str, str]] = []
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for case in CASES:
            errors = []
            for depth in case.sample_depths:
                p_analytic = analytic_position(case, depth)
                p_numeric = numerical_position(case, depth)
                error = float(np.linalg.norm(p_analytic - p_numeric))
                errors.append(error)
                writer.writerow({
                    "case_id": case.case_id,
                    "collar_x": fmt(case.collar[0]),
                    "collar_y": fmt(case.collar[1]),
                    "collar_z": fmt(case.collar[2]),
                    "station_depths": "|".join(fmt(v) for v in case.station_depths),
                    "station_azimuths": "|".join(fmt(v) for v in case.station_azimuths),
                    "station_dips": "|".join(fmt(v) for v in case.station_dips),
                    "depth": fmt(depth),
                    "x": fmt(float(p_analytic[0])),
                    "y": fmt(float(p_analytic[1])),
                    "z": fmt(float(p_analytic[2])),
                })
            agreements[case.case_id] = max(errors, default=0.0)
            for interval, length, angle_deg, rf, theta in all_segment_rows(case):
                segment_summary.append({
                    "case_id": case.case_id,
                    "interval_md_m": interval,
                    "interval_length_m": f"{length:.6f}",
                    "dogleg_deg": f"{angle_deg:.12f}",
                    "ratio_factor": f"{rf:.15f}",
                    "dogleg_rad": f"{theta:.15e}",
                })
    if any(error > TOLERANCE_M for error in agreements.values()):
        raise AssertionError(f"Method disagreement exceeded {TOLERANCE_M} m: {agreements}")
    return agreements, segment_summary


def main() -> None:
    output = Path("cases.csv")
    agreements, segments = generate_csv(output)
    print(f"Wrote {output.resolve()} ({sum(len(c.sample_depths) for c in CASES)} point rows)")
    print("Maximum 3D Euclidean disagreement by case (m):")
    for case_id, error in agreements.items():
        print(f"  {case_id}: {error:.12e}")
    print("\nDogleg / ratio-factor table:")
    print("case_id | interval MD (m) | length (m) | dogleg (deg) | RF")
    for row in segments:
        print(f"{row['case_id']} | {row['interval_md_m']} | {row['interval_length_m']} | "
              f"{row['dogleg_deg']} | {row['ratio_factor']}")
    print(f"\nAssertion passed: every case <= {TOLERANCE_M:.1e} m")


if __name__ == "__main__":
    main()
```
