"""Metric tests. Known inputs, known answers."""

from __future__ import annotations

import numpy as np

from driftgauge.metrics import hausdorff, nearest_distances


def _grid_points():
    xs, ys = np.meshgrid(np.linspace(0, 1, 12), np.linspace(0, 1, 12))
    zs = np.zeros_like(xs)
    return np.column_stack([xs.ravel(), ys.ravel(), zs.ravel()])


def test_identity_is_zero():
    p = _grid_points()
    d = hausdorff(p, p)
    assert d["max"] == 0.0
    assert d["mean"] == 0.0
    assert d["median"] == 0.0


def test_known_uniform_offset():
    # Shift every point by a known vector. Every nearest-neighbor distance for
    # an interior point equals the shift length; edge effects only shorten some,
    # so the max equals the shift length exactly.
    p = _grid_points()
    shift = np.array([0.0, 0.0, 0.05])
    q = p + shift
    d = hausdorff(p, q)
    assert np.isclose(d["max"], 0.05, atol=1e-9)
    assert d["mean"] <= 0.05 + 1e-9
    assert d["mean"] > 0.0


def test_directed_distance_zero_when_subset():
    # Every source point is also in target, so directed source->target is zero.
    p = _grid_points()
    extra = np.vstack([p, p + np.array([0.0, 0.0, 1.0])])
    d = nearest_distances(p, extra)
    assert np.allclose(d, 0.0)


def test_symmetry():
    a = _grid_points()
    b = a + np.array([0.01, 0.0, 0.0])
    assert hausdorff(a, b) == hausdorff(b, a)
