"""Write ASCII DXF (R12 entity subset: ``POLYLINE``/``VERTEX`` and ``3DFACE``).

R12 entities are read by every CAD and mining package. Only the ``ENTITIES``
section is written (allowed by the DXF specification); layers are created
implicitly by name. Coordinates use 6 decimals.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

Floats = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Polyline:
    layer: str
    points: Floats  # n x 3
    closed: bool = True


def _num(v: float) -> str:
    return f"{float(v):.6f}"


def _layer(name: str) -> str:
    bad = '<>/\\":;?*|=`'
    clean = "".join("_" if c in bad or ord(c) < 32 else c for c in name.strip())
    return clean or "0"


def write_dxf(
    path: Path,
    polylines: Iterable[Polyline] = (),
    faces: Sequence[tuple[str, Floats]] = (),
) -> int:
    """``faces``: (layer, m x 3 x 3 triangle corners). Returns the entity count."""
    out: list[str] = ["0", "SECTION", "2", "ENTITIES"]
    count = 0
    for line in polylines:
        pts = np.asarray(line.points, np.float64)
        if len(pts) < 2:
            continue
        out += ["0", "POLYLINE", "8", _layer(line.layer), "66", "1", "70"]
        out.append(str(8 | (1 if line.closed else 0)))  # 8 = 3D polyline
        out += ["10", "0.0", "20", "0.0", "30", "0.0"]
        for x, y, z in pts:
            out += ["0", "VERTEX", "8", _layer(line.layer), "70", "32"]
            out += ["10", _num(x), "20", _num(y), "30", _num(z)]
        out += ["0", "SEQEND", "8", _layer(line.layer)]
        count += 1
    for layer, tris in faces:
        name = _layer(layer)
        for tri in np.asarray(tris, np.float64).reshape(-1, 3, 3):
            out += ["0", "3DFACE", "8", name]
            for k, corner in enumerate((tri[0], tri[1], tri[2], tri[2])):
                out += [f"1{k}", _num(corner[0]), f"2{k}", _num(corner[1])]
                out += [f"3{k}", _num(corner[2])]
            count += 1
    out += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(out) + "\n", encoding="ascii", errors="replace")
    return count
