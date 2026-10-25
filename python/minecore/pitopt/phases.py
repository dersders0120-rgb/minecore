"""Pushbacks chosen by NPV from the nested shells (best phase selection).

A pushback design is a grouping of shells ``1..final`` into consecutive
phases, written as its *boundaries*: the last shell of every phase but the
final one (``(2, 5)`` with 8 shells = shells 1-2, 3-5, 6-8). Each candidate
is valued by the NPV of the constrained (greedy) schedule under the same
limits, and must give every phase at least ``min_ore_t`` of plant feed.

When the number of admissible groupings is within ``max_evaluations`` they
are all evaluated (exact over this schedule); otherwise a local search
starts from the ore-target grouping (``optimise.pushbacks``) and moves,
removes or adds one boundary at a time while the NPV improves. The local
search is a heuristic: it never returns a design worse than its start.
The minimum mining width is applied after the choice, not during it.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from minecore.pitopt.economics import PitOptError
from minecore.pitopt.optimise import PitResult, pushbacks
from minecore.pitopt.scheduling import Limits, schedule_constrained

Ints = npt.NDArray[np.int64]
Bounds = tuple[int, ...]


@dataclass(frozen=True)
class PushbackChoice:
    phase: Ints  # pushback of each block (0 = outside the final pit)
    boundaries: Bounds  # last shell of each pushback but the final one
    npv: float  # NPV of the constrained schedule of this design
    start_npv: float  # NPV of the ore-target grouping it started from
    start_boundaries: Bounds
    evaluations: int
    exhaustive: bool  # every admissible grouping was evaluated


def phase_from_boundaries(result: PitResult, final_shell: int, boundaries: Bounds) -> Ints:
    """Pushback number of each block for shells grouped at ``boundaries``."""
    shell = result.shell
    inside = (shell > 0) & (shell <= final_shell)
    out = np.zeros(len(shell), np.int64)
    edges = np.asarray(boundaries, np.int64)
    out[inside] = np.searchsorted(edges, shell[inside], side="left") + 1
    return out


def _boundaries_of(phase: Ints, result: PitResult) -> Bounds:
    tops = []
    for p in range(1, int(phase.max()) if phase.size else 1):
        tops.append(int(result.shell[phase == p].max()))
    return tuple(tops)


def _count(n: int, k_max: int) -> int:
    return sum(math.comb(n, k) for k in range(k_max + 1))


def best_pushbacks(  # noqa: PLR0913 - options are keyword-only
    result: PitResult,
    final_shell: int,
    limits: Limits,
    *,
    min_ore_t: float,
    max_pushbacks: int | None = None,
    max_evaluations: int = 300,
    progress: Callable[[float, str], None] | None = None,
) -> PushbackChoice:
    """The grouping of shells into pushbacks with the highest schedule NPV."""
    if not 1 <= final_shell <= len(result.revenue_factors):
        raise PitOptError("final shell out of range")
    if max_pushbacks is not None and max_pushbacks < 1:
        raise PitOptError("at least one pushback is needed")
    if max_evaluations < 1:
        raise PitOptError("max evaluations must be >= 1")
    ore = np.zeros(final_shell + 1)
    for n in range(1, final_shell + 1):
        ore[n] = float(result.feed[(result.shell == n) & result.ore].sum())
    cum = np.cumsum(ore)
    limit = final_shell if max_pushbacks is None else min(max_pushbacks, final_shell)

    def admissible(b: Bounds) -> bool:
        if len(b) + 1 > limit:
            return False
        edges = (0, *b, final_shell)
        return all(cum[hi] - cum[lo] >= min_ore_t for lo, hi in itertools.pairwise(edges))

    cache: dict[Bounds, float] = {}

    def npv(b: Bounds) -> float:
        if b not in cache:
            phase = phase_from_boundaries(result, final_shell, b)
            cache[b] = schedule_constrained(result, phase, limits).npv
            if progress is not None:
                progress(min(len(cache) / max_evaluations, 1.0), f"{len(cache)} tasarım")
        return cache[b]

    start = _boundaries_of(pushbacks(result, final_shell, min_ore_t), result)
    if not admissible(start):  # the ore target allows fewer phases than it made
        start = ()
    start_npv = npv(start)
    total = _count(final_shell - 1, limit - 1)
    exhaustive = total <= max_evaluations
    if exhaustive:
        best = max(
            (b for b in _all(final_shell, limit) if admissible(b)),
            key=lambda b: (npv(b), -len(b)),
            default=start,
        )
    else:
        best = _climb(start, final_shell, admissible, npv, max_evaluations, cache)
    return PushbackChoice(
        phase_from_boundaries(result, final_shell, best),
        best,
        npv(best),
        start_npv,
        start,
        len(cache),
        exhaustive,
    )


def _all(final_shell: int, limit: int) -> Iterator[Bounds]:
    shells = range(1, final_shell)
    for k in range(limit):
        yield from itertools.combinations(shells, k)


def _neighbours(b: Bounds, final_shell: int) -> Iterator[Bounds]:
    taken = set(b)
    for i, edge in enumerate(b):  # move one boundary by one shell
        for step in (-1, 1):
            moved = edge + step
            if 1 <= moved < final_shell and moved not in taken:
                yield tuple(sorted((*b[:i], moved, *b[i + 1 :])))
        yield b[:i] + b[i + 1 :]  # merge two pushbacks
    for edge in range(1, final_shell):  # split a pushback
        if edge not in taken:
            yield tuple(sorted((*b, edge)))


def _climb(  # noqa: PLR0913, PLR0917 - search state
    start: Bounds,
    final_shell: int,
    admissible: Callable[[Bounds], bool],
    npv: Callable[[Bounds], float],
    max_evaluations: int,
    cache: dict[Bounds, float],
) -> Bounds:
    current, value = start, npv(start)
    while len(cache) < max_evaluations:
        better = None
        for cand in _neighbours(current, final_shell):
            if not admissible(cand):
                continue
            if cand not in cache and len(cache) >= max_evaluations:
                break
            v = npv(cand)
            if v > value + 1e-9 * max(abs(value), 1.0):
                better, value = cand, v
        if better is None:
            break
        current = better
    return current
