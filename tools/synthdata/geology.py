from __future__ import annotations

import numpy as np

from synthdata.config import Plane, Topography


def make_topography(rng: np.random.Generator) -> Topography:
    return Topography(
        xc=500000.0,
        yc=4400000.0,
        z0=1200.0,
        gx=float(rng.normal(0.0, 0.002)),
        gy=float(rng.normal(0.0, 0.002)),
        amplitude=float(rng.uniform(5.0, 20.0)),
        lx=float(rng.uniform(1000.0, 3000.0)),
        ly=float(rng.uniform(1000.0, 3000.0)),
    )


def make_plane(
    rng: np.random.Generator,
    collar_hint: tuple[float, float, float],
) -> Plane:
    dip_dir = float(rng.uniform(0.0, 360.0))
    dip = float(rng.uniform(30.0, 70.0))
    thickness = float(rng.uniform(5.0, 25.0))
    dummy = Plane.create(dip_dir, dip, (0.0, 0.0, 0.0), thickness)
    n = dummy.normal
    ref = (
        collar_hint[0] - n[0] * thickness / 2.0,
        collar_hint[1] - n[1] * thickness / 2.0,
        collar_hint[2] - n[2] * thickness / 2.0,
    )
    return Plane.create(dip_dir, dip, ref, thickness)
