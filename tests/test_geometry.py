"""Geometry tests. Every assertion has a known-correct answer because the source
surface is constructed, not loaded."""

from __future__ import annotations

import numpy as np

from driftgauge.geometry import (
    build_wing_surface,
    control_net,
    evaluate,
    tessellate,
)


def test_surface_is_valid_and_clamped():
    ns = build_wing_surface()
    assert ns.IsValid
    assert ns.KnotsU.IsClampedStart and ns.KnotsU.IsClampedEnd
    assert ns.KnotsV.IsClampedStart and ns.KnotsV.IsClampedEnd


def test_domain_is_unit_square():
    ns = build_wing_surface()
    du, dv = ns.Domain(0), ns.Domain(1)
    assert (du.T0, du.T1) == (0.0, 1.0)
    assert (dv.T0, dv.T1) == (0.0, 1.0)


def test_corners_interpolate_control_points():
    # A clamped surface interpolates its corner control points at the domain
    # corners. This pins the parameterization to the geometry.
    ns = build_wing_surface()
    net = control_net(ns)
    cu, cv = net.shape[0] - 1, net.shape[1] - 1
    corners = {
        (0.0, 0.0): net[0, 0, :3],
        (1.0, 0.0): net[cu, 0, :3],
        (0.0, 1.0): net[0, cv, :3],
        (1.0, 1.0): net[cu, cv, :3],
    }
    for (u, v), cp in corners.items():
        assert np.allclose(evaluate(ns, u, v), cp, atol=1e-9)


def test_tessellation_counts_and_bounds():
    ns = build_wing_surface()
    n_u, n_v = 40, 16
    mesh = tessellate(ns, n_u, n_v)
    assert mesh.vertices.shape == (n_u * n_v, 3)
    assert mesh.uv.shape == (n_u * n_v, 2)
    assert mesh.faces.shape == ((n_u - 1) * (n_v - 1), 4)
    # Bounds are sane: chord ~1, span 3.6, thin in z. Span sits on a clamped
    # edge so it is exact to float tolerance.
    lo, hi = mesh.vertices.min(0), mesh.vertices.max(0)
    assert np.isclose(hi[1], 3.6, atol=1e-9)  # spanwise extent
    assert hi[2] < 0.2                        # thin section


def test_chord_and_span_bounds_are_resolution_stable():
    # Chord (x) and span (y) extents sit on clamped edges, so they do not move
    # with grid resolution. Thickness (z) peaks at an interior parameter, so a
    # finer grid resolves it at least as high. State both honestly.
    ns = build_wing_surface()
    coarse = tessellate(ns, 10, 6)
    fine = tessellate(ns, 60, 24)
    assert fine.vertices.shape[0] > coarse.vertices.shape[0]

    # x and y bounds stable across resolution.
    assert np.allclose(coarse.vertices.max(0)[:2], fine.vertices.max(0)[:2], atol=1e-9)
    assert np.allclose(coarse.vertices.min(0), fine.vertices.min(0), atol=1e-9)

    # z peak is interior: finer grid finds it at least as high, and the two
    # agree to a loose tolerance (both are near the true peak).
    assert fine.vertices.max(0)[2] >= coarse.vertices.max(0)[2] - 1e-12
    assert np.isclose(coarse.vertices.max(0)[2], fine.vertices.max(0)[2], atol=1e-3)


def test_uv_re_evaluates_to_vertex():
    # Every vertex's stored UV must re-evaluate to that vertex. The
    # parameterization carried on the mesh is correct.
    ns = build_wing_surface()
    mesh = tessellate(ns, 30, 12)
    max_err = 0.0
    for k in range(mesh.vertices.shape[0]):
        p = evaluate(ns, mesh.uv[k, 0], mesh.uv[k, 1])
        max_err = max(max_err, float(np.linalg.norm(mesh.vertices[k] - p)))
    assert max_err < 1e-9


def test_construction_is_deterministic():
    a = control_net(build_wing_surface())
    b = control_net(build_wing_surface())
    assert np.array_equal(a, b)
