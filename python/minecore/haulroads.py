"""Haul road network: shortest route between two points along road strings.

The network is built from 3D strings (road centrelines): every vertex is a
node and every string segment an edge weighted by its 3D length. Vertices of
different strings closer than ``snap`` metres (in 3D) are merged, so roads
join where they meet at a vertex; roads that cross without a shared vertex
are not joined. The start and end points attach to their nearest node.
Shortest paths use Dijkstra (``scipy.sparse.csgraph``).

Each route reports its 3D length, the length on grades steeper than
``ramp_grade`` (ramp) and the rest (flat), the climb and descent, and a
one-way travel time for given loaded and empty speeds on ramps and flats
(loaded going from start to end).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from minecore._core import MineCoreError
from minecore.strings import String

Floats = npt.NDArray[np.float64]


class HaulRoadError(MineCoreError):
    """Invalid road network or no connection."""


@dataclass(frozen=True)
class Route:
    points: Floats  # n x 3, start to end
    length: float  # 3D, m
    ramp_m: float  # on grades steeper than the ramp grade
    flat_m: float
    climb: float  # total rise, m
    descent: float  # total fall, m
    snap_start: float  # distance from the start point to the network, m
    snap_end: float

    def travel_minutes(self, ramp_kmh: float, flat_kmh: float) -> float:
        return 60.0 * (self.ramp_m / ramp_kmh + self.flat_m / flat_kmh) / 1000.0


class RoadNetwork:
    def __init__(self, strings: list[String], snap: float = 1.0) -> None:
        strings = [s for s in strings if len(s.points) >= 2]
        pts = [np.asarray(s.points, np.float64) for s in strings]
        if not pts:
            raise HaulRoadError("no road strings with two or more points")
        allp = np.vstack(pts)
        tree = cKDTree(allp)
        label = np.arange(len(allp))
        for i, j in sorted(tree.query_pairs(max(snap, 0.0))):
            a, b = self._root(label, i), self._root(label, j)
            if a != b:
                label[max(a, b)] = min(a, b)
        roots = np.array([self._root(label, i) for i in range(len(allp))])
        uniq, node = np.unique(roots, return_inverse=True)
        self.nodes = allp[uniq]
        rows, cols, w = [], [], []
        start = 0
        for s, p in zip(strings, pts, strict=True):
            idx = node[start : start + len(p)]
            pairs = list(itertools.pairwise(idx.tolist()))
            if s.closed:
                pairs.append((idx[-1], idx[0]))
            for a, b in pairs:
                if a != b:
                    d = float(np.linalg.norm(self.nodes[a] - self.nodes[b]))
                    rows += [a, b]
                    cols += [b, a]
                    w += [d, d]
            start += len(p)
        n = len(self.nodes)
        self.graph = coo_matrix((w, (rows, cols)), shape=(n, n)).tocsr()
        self._tree = cKDTree(self.nodes)

    @staticmethod
    def _root(label: npt.NDArray[np.int64], i: int) -> int:
        while label[i] != i:
            i = int(label[i])
        return i

    def route(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        ramp_grade: float = 0.03,
    ) -> Route:
        s_d, s = self._tree.query(start)
        e_d, e = self._tree.query(end)
        dist, pred = dijkstra(self.graph, indices=int(s), return_predecessors=True)
        if not math.isfinite(dist[e]):
            raise HaulRoadError("the two points are not connected by the road network")
        path = [int(e)]
        while path[-1] != s:
            path.append(int(pred[path[-1]]))
        p = self.nodes[path[::-1]]
        seg = np.diff(p, axis=0)
        length3 = np.linalg.norm(seg, axis=1)
        plan = np.linalg.norm(seg[:, :2], axis=1)
        steep = np.abs(seg[:, 2]) > ramp_grade * np.maximum(plan, 1e-9)
        return Route(
            p,
            float(length3.sum()),
            float(length3[steep].sum()),
            float(length3[~steep].sum()),
            float(seg[:, 2][seg[:, 2] > 0].sum()),
            float(-seg[:, 2][seg[:, 2] < 0].sum()),
            float(s_d),
            float(e_d),
        )
