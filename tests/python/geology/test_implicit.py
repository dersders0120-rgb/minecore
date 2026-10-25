"""Implicit domain modelling recovers known shapes from synthetic drilling."""

from __future__ import annotations

import math

import numpy as np
import pytest

from minecore.blockmodel import Grid
from minecore.estimation import Ellipsoid
from minecore.geology.implicit import (
    ImplicitError,
    contact_data,
    implicit_solid,
    implicit_values,
)

R = 40.0


def _drill(spacing: float, length: float = 4.0, inside=None):  # type: ignore[no-untyped-def]
    inside = inside or (lambda p: np.linalg.norm(p, axis=1) < R)
    cols = np.arange(-60.0, 60.1, spacing)
    holes, f, t, ins, collars = [], [], [], [], {}
    for a in cols:
        for b in cols:
            name = f"H{a:+.0f}{b:+.0f}"
            collars[name] = np.array([a, b, 80.0])
            depth = np.arange(0.0, 160.0, length)
            mid = collars[name] - np.outer(depth + length / 2, [0.0, 0.0, 1.0])
            holes += [name] * depth.size
            f.append(depth)
            t.append(depth + length)
            ins.append(inside(mid))

    def at(hole: str, d: np.ndarray) -> np.ndarray:
        return collars[hole] - np.outer(d, [0.0, 0.0, 1.0])

    return (
        np.array(holes, object),
        np.concatenate(f),
        np.concatenate(t),
        np.concatenate(ins),
        at,
    )


def test_contacts_and_signed_distances_by_hand() -> None:
    holes = np.array(["A"] * 4 + ["B"] * 2, object)
    f = np.array([0.0, 2.0, 4.0, 10.0, 0.0, 2.0])
    t = np.array([2.0, 4.0, 6.0, 12.0, 2.0, 4.0])
    ins = np.array([False, True, True, False, True, True])

    def at(hole: str, d: np.ndarray) -> np.ndarray:
        x = 0.0 if hole == "A" else 100.0
        return np.column_stack([np.full(d.size, x), np.zeros(d.size), -d])

    data = contact_data(holes, f, t, ins, at, max_distance=30.0)
    # Hole A: one contact at 2 m (the 6-10 m gap gives none); hole B has no contact.
    assert data.contacts == 1
    values = dict(zip(map(tuple, data.xyz.round(6).tolist()), data.value.tolist(), strict=True))
    assert values[(0.0, 0.0, -1.0)] == -1.0
    assert values[(0.0, 0.0, -3.0)] == 1.0
    assert values[(0.0, 0.0, -5.0)] == 3.0
    assert values[(0.0, 0.0, -11.0)] == -9.0
    assert values[(0.0, 0.0, -2.0)] == 0.0
    assert values[(100.0, 0.0, -1.0)] == 30.0  # 100 m from the contact, capped
    with pytest.raises(ImplicitError):
        contact_data(holes, f, t, np.zeros(6, bool), at)


def test_sphere_is_recovered_from_drilling() -> None:
    data = contact_data(*_drill(15.0))
    assert data.contacts == 2 * 21  # 21 holes cross the sphere, two contacts each
    # Exact interpolation: the zero surface passes through every drilled contact.
    contacts = data.xyz[data.value == 0]
    assert np.abs(implicit_values(data, contacts)).max() < 1e-6
    grid = Grid((-70.0, -70.0, -70.0), (4.0, 4.0, 4.0), (35, 35, 35))
    result = implicit_solid(data, grid)
    assert result.solid.open_edges() == 0
    exact = 4.0 / 3.0 * math.pi * R**3
    assert result.solid.volume() == pytest.approx(exact, rel=0.08)
    assert result.blocks_inside * 64.0 == pytest.approx(exact, rel=0.08)
    probe = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 55.0], [55.0, 0.0, 0.0], [0.0, 30.0, 0.0]])
    assert (implicit_values(data, probe) > 0).tolist() == [True, False, False, True]


def test_anisotropy_follows_a_flat_lens() -> None:
    def lens(p: np.ndarray) -> np.ndarray:
        return (p[:, 0] / 55.0) ** 2 + (p[:, 1] / 55.0) ** 2 + (p[:, 2] / 12.0) ** 2 < 1.0

    data = contact_data(*_drill(30.0, inside=lens))
    flat = Ellipsoid(4.0, 4.0, 1.0)  # horizontal continuity 4x the vertical
    # Between holes (15 m off any hole) the lens edge region is better resolved
    # when the interpolation knows the lens is flat.
    probe = np.array([[15.0, 15.0, 0.0], [15.0, 0.0, 8.0], [15.0, 15.0, 16.0], [0.0, 15.0, -14.0]])
    iso = implicit_values(data, probe) > 0
    aniso = implicit_values(data, probe, anisotropy=flat) > 0
    truth = lens(probe)
    assert (aniso == truth).sum() >= (iso == truth).sum()
    assert aniso.tolist() == truth.tolist()
