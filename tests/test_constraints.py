"""Constraint checker tests. Every assertion has a known answer from synthetic
ground truth.

This file carries the Phase 2 gates:
    - Ruled binary: a ruled fixture reads ruled, and a known off-ruled bow raises
      the residual monotonically. The checker detects what it claims to detect.
    - Budget carried: the descriptor survives the exchange unchanged, and the
      checker reads tolerances from the descriptor. Changing a tolerance flips the
      pass/fail result.

It also carries the verify-before-trust gate for the authored derivatives: the
first and second parametric derivatives, and the curvature built on them, are
cross-checked against finite differences of rhino3dm's PointAt.
"""

from __future__ import annotations

import numpy as np
import rhino3dm as r3

from passthrough.constraints import (
    ConstraintResult,
    check_constraints,
    curvature_field,
    curvature_residual,
    ruled_residual,
    surface_derivatives,
)
from passthrough.encode import Descriptor
from passthrough.geometry import build_wing_surface, evaluate, tessellate
from passthrough.reconstruct import (
    ClassicalReconstructor,
    basis_derivatives_1d,
    basis_matrix_1d,
    clamped_uniform_knots,
)
from passthrough.solver_stub import solve_identity


# --- fixtures -------------------------------------------------------------


def make_ruled_surface() -> r3.NurbsSurface:
    """A genuinely ruled surface via CreateRuledSurface. Degree 1 in v, so the
    rulings (v-isocurves) are straight by construction. The tests confirm the
    isocurves are linear before relying on the residual."""
    pts_a = [r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0.3), r3.Point3d(2, 0, 0.1)]
    pts_b = [r3.Point3d(0, 2, 0.2), r3.Point3d(1, 2, 0.5), r3.Point3d(2, 2, 0.4)]
    ca = r3.Curve.CreateControlPointCurve(pts_a, 2)
    cb = r3.Curve.CreateControlPointCurve(pts_b, 2)
    return r3.NurbsSurface.CreateRuledSurface(ca, cb)


def make_bowed_surface(bow: float) -> r3.NurbsSurface:
    """A degree-2-in-v surface whose rulings bow off straight by a known amount.

    The v control points are the two ends and a middle row. At bow = 0 the middle
    row sits at the midpoint of the ends, so each quadratic v-isocurve degenerates
    to the straight chord and the surface is ruled. Offsetting the middle row by
    bow in z bends every ruling; the perpendicular deviation of a quadratic
    Bezier's midpoint from its chord is bow / 2, so the residual is a known
    function of bow and rises monotonically with it.
    """
    u_profile = [(0.0, 0.0), (0.6, 0.4), (1.4, 0.35), (2.0, 0.0)]
    n_u, n_v = len(u_profile), 3
    s = r3.NurbsSurface.Create(3, False, 4, 3, n_u, n_v)
    for i, (x, z) in enumerate(u_profile):
        end0 = np.array([x, 0.0, z])
        end1 = np.array([x, 2.0, z])
        mid = (end0 + end1) / 2 + np.array([0.0, 0.0, bow])
        for j, p in enumerate([end0, mid, end1]):
            s.Points[i, j] = r3.Point4d(float(p[0]), float(p[1]), float(p[2]), 1.0)
    s.KnotsU.CreateUniformKnots(1.0)
    s.KnotsV.CreateUniformKnots(1.0)
    s.SetDomain(0, r3.Interval(0.0, 1.0))
    s.SetDomain(1, r3.Interval(0.0, 1.0))
    return s


def _fd_point(surface: r3.NurbsSurface, u: float, v: float) -> np.ndarray:
    p = surface.PointAt(float(np.clip(u, 0, 1)), float(np.clip(v, 0, 1)))
    return np.array([p.X, p.Y, p.Z])


def _fd_curvature(surface: r3.NurbsSurface, u: float, v: float, h: float = 1e-4):
    """Mean and Gaussian curvature from finite differences of PointAt. The
    independent computation the analytic curvature is checked against."""
    P = _fd_point
    Su = (P(surface, u + h, v) - P(surface, u - h, v)) / (2 * h)
    Sv = (P(surface, u, v + h) - P(surface, u, v - h)) / (2 * h)
    Suu = (P(surface, u + h, v) - 2 * P(surface, u, v) + P(surface, u - h, v)) / h**2
    Svv = (P(surface, u, v + h) - 2 * P(surface, u, v) + P(surface, u, v - h)) / h**2
    Suv = (
        P(surface, u + h, v + h)
        - P(surface, u + h, v - h)
        - P(surface, u - h, v + h)
        + P(surface, u - h, v - h)
    ) / (4 * h * h)
    n = np.cross(Su, Sv)
    n_hat = n / np.linalg.norm(n)
    E, F, G = Su @ Su, Su @ Sv, Sv @ Sv
    L, M, N = Suu @ n_hat, Suv @ n_hat, Svv @ n_hat
    det = E * G - F * F
    H = (E * N - 2 * F * M + G * L) / (2 * det)
    K = (L * N - M * M) / det
    return H, K


# --- verify-before-trust: authored derivatives ----------------------------


def test_basis_zeroth_derivative_matches_values():
    # Order 0 of the derivative routine must equal the validated basis values.
    knots = clamped_uniform_knots(3, 8)
    ts = np.linspace(0.0, 1.0, 41)
    d0 = basis_derivatives_1d(ts, 3, knots, 8, 2)[0]
    assert np.allclose(d0, basis_matrix_1d(ts, 3, knots, 8), atol=1e-14)


def test_basis_derivatives_match_finite_differences():
    # First and second derivatives of the basis against central differences of
    # the validated basis values. The first derivative is well inside FD reach;
    # the second is checked at a coarser step where its FD truncation is smaller.
    knots = clamped_uniform_knots(3, 8)
    ts = np.linspace(0.05, 0.95, 40)
    _, d1, d2 = basis_derivatives_1d(ts, 3, knots, 8, 2)

    h = 1e-6
    fd1 = (basis_matrix_1d(ts + h, 3, knots, 8) - basis_matrix_1d(ts - h, 3, knots, 8)) / (2 * h)
    assert np.abs(d1 - fd1).max() < 1e-7

    h2 = 1e-3
    fd2 = (
        basis_matrix_1d(ts + h2, 3, knots, 8)
        - 2 * basis_matrix_1d(ts, 3, knots, 8)
        + basis_matrix_1d(ts - h2, 3, knots, 8)
    ) / (h2 * h2)
    assert np.abs(d2 - fd2).max() < 1e-3


def test_surface_point_matches_rhino():
    # The authored derivative evaluation must reproduce rhino3dm's own PointAt.
    # If S is wrong, every curvature built on it is wrong.
    ns = build_wing_surface()
    rng = np.random.default_rng(2)
    us, vs = rng.random(40), rng.random(40)
    S = surface_derivatives(ns, us, vs)["S"]
    err = max(np.linalg.norm(S[k] - evaluate(ns, us[k], vs[k])) for k in range(40))
    assert err < 1e-12


def test_analytic_curvature_matches_finite_differences():
    # The verify-before-trust gate for curvature. Analytic mean and Gaussian
    # curvature agree with a finite-difference curvature at interior samples,
    # including the high-curvature leading-edge band near u = 0.
    ns = build_wing_surface()
    us = np.linspace(0.05, 0.95, 8)
    vs = np.linspace(0.1, 0.9, 6)
    grid_u = np.repeat(us, len(vs))
    grid_v = np.tile(vs, len(us))
    cf = curvature_field(ns, grid_u, grid_v)
    max_h_err = max_k_err = 0.0
    for k in range(grid_u.shape[0]):
        Hf, Kf = _fd_curvature(ns, grid_u[k], grid_v[k])
        max_h_err = max(max_h_err, abs(cf["H"][k] - Hf))
        max_k_err = max(max_k_err, abs(cf["K"][k] - Kf))
    # Bounded by finite-difference truncation, not by an error in the analytic form.
    assert max_h_err < 1e-5
    assert max_k_err < 1e-5


# --- ruled binary gate ----------------------------------------------------


def test_ruled_fixture_isocurves_are_linear():
    # Confirm IsoCurve behaves before relying on it: the v-isocurves of the ruled
    # fixture are linear, so they are the straight rulings.
    s = make_ruled_surface()
    assert all(s.IsoCurve(1, float(p)).IsLinear() for p in np.linspace(0, 1, 5))


def test_ruled_fixture_reads_ruled():
    # A genuinely ruled surface reads a residual at floating-point zero, well
    # below any sensible declared tolerance.
    s = make_ruled_surface()
    assert ruled_residual(s) < 1e-9


def test_ruled_residual_zero_at_zero_bow():
    # The bowed fixture at bow = 0 is ruled by construction (mid row at the
    # midpoint), so its residual is zero.
    assert ruled_residual(make_bowed_surface(0.0), direction=1) < 1e-9


def test_ruled_residual_rises_monotonically_with_bow():
    # Off-ruled by a known, increasing amount: the residual rises monotonically.
    # The checker detects what it claims to detect. The deviation tracks bow / 2.
    bows = [0.0, 0.01, 0.05, 0.1, 0.2]
    residuals = [ruled_residual(make_bowed_surface(b), direction=1) for b in bows]
    assert all(b < a for b, a in zip(residuals, residuals[1:]))
    for b, res in zip(bows, residuals):
        assert abs(res - b / 2) < 1e-6


def test_ruled_auto_direction_finds_the_ruled_direction():
    # With no declared direction the checker tries both and takes the smaller. The
    # bowed fixture is curved in u (the airfoil-like profile) and straight in v at
    # bow = 0, so auto-detection still reads it ruled.
    assert ruled_residual(make_bowed_surface(0.0)) < 1e-9


# --- curvature gate -------------------------------------------------------


def test_curvature_residual_near_zero_for_matching_fit():
    # A reconstruction whose spline space contains the source recovers it, so the
    # curvature field is preserved and the residual is at floating-point zero.
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)
    surf = ClassicalReconstructor(n_ctrl_u=8, n_ctrl_v=4).reconstruct(mesh.vertices, mesh.uv)
    res = curvature_residual(surf, ns)
    assert res["max"] < 1e-6


def test_curvature_residual_large_for_coarse_fit():
    # A coarser fit cannot hold the source curvature, most visibly at the leading
    # edge. The residual is large and the LE band carries a real share of it.
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)
    surf = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4).reconstruct(mesh.vertices, mesh.uv)
    res = curvature_residual(surf, ns)
    assert res["max"] > 0.5
    assert res["le_max"] > 0.1
    assert res["mean"] < res["max"]  # typical case below worst case


def test_curvature_requires_source():
    # The curvature constraint compares against the source. Declaring it without a
    # source is an error, not a silent skip.
    desc = Descriptor(constraints=[{"type": "curvature", "tolerance": 0.1}])
    ns = build_wing_surface()
    mesh = tessellate(ns, 20, 8)
    surf = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4).reconstruct(mesh.vertices, mesh.uv)
    try:
        check_constraints(desc, surf, source=None)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when source is missing")


# --- the checker reads the descriptor -------------------------------------


def test_checker_reads_constraints_from_descriptor():
    # The checker iterates the declared constraints and reports one result each,
    # with the residual beside the declared tolerance.
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)
    surf = ClassicalReconstructor(n_ctrl_u=8, n_ctrl_v=4).reconstruct(mesh.vertices, mesh.uv)
    ruled = make_ruled_surface()
    desc = Descriptor(constraints=[{"type": "curvature", "tolerance": 1e-4}])
    results = check_constraints(desc, surf, source=ns)
    assert len(results) == 1
    assert results[0].type == "curvature"
    assert results[0].tolerance == 1e-4
    assert results[0].passed

    # The ruled result against the ruled fixture, declared and checked.
    desc2 = Descriptor(constraints=[{"type": "ruled", "tolerance": 1e-4, "direction": 1}])
    res2 = check_constraints(desc2, ruled)
    assert res2[0].type == "ruled"
    assert res2[0].passed


def test_unknown_constraint_type_is_refused():
    # A declared property the checker cannot verify is refused, not skipped.
    desc = Descriptor(constraints=[{"type": "nonsense", "tolerance": 1.0}])
    ns = build_wing_surface()
    mesh = tessellate(ns, 20, 8)
    surf = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4).reconstruct(mesh.vertices, mesh.uv)
    try:
        check_constraints(desc, surf, source=ns)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an unknown constraint type")


def test_tolerance_in_descriptor_flips_pass_fail():
    # The budget-carried gate, sharpened: the same residual, two declared
    # tolerances, opposite verdicts. The checker owns no tolerance; the descriptor
    # does. The raw residual is unchanged across the flip.
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)
    surf = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4).reconstruct(mesh.vertices, mesh.uv)

    loose = Descriptor(constraints=[{"type": "curvature", "tolerance": 100.0}])
    tight = Descriptor(constraints=[{"type": "curvature", "tolerance": 1e-6}])
    r_loose = check_constraints(loose, surf, source=ns)[0]
    r_tight = check_constraints(tight, surf, source=ns)[0]

    assert r_loose.passed
    assert not r_tight.passed
    assert abs(r_loose.residual - r_tight.residual) < 1e-12  # same residual


# --- budget carried across the exchange -----------------------------------


def test_descriptor_survives_the_exchange_unchanged(tmp_path):
    # The descriptor written to exchange/out is present and unchanged when read
    # back from exchange/in, and the checker reads its constraints from there.
    ns = build_wing_surface()
    mesh = tessellate(ns, 30, 12)
    constraints = [
        {"type": "ruled", "tolerance": 1e-4, "direction": 1},
        {"type": "curvature", "tolerance": 0.05},
    ]
    desc = Descriptor(
        constraints=constraints,
        preserve={"region": "leading_edge", "property": "curvature_class"},
        provenance={"source": "wing", "iteration": 0},
    )

    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"
    from passthrough.encode import mesh_filename, write_payload, read_payload

    write_payload(out_dir / mesh_filename(0), mesh, desc)
    solve_identity(out_dir, in_dir, 0)
    _, returned = read_payload(in_dir / mesh_filename(0))

    assert returned.constraints == constraints
    assert returned.preserve == desc.preserve
    assert returned.provenance == desc.provenance

    # The checker runs against the budget read back from the exchange.
    surf = ClassicalReconstructor(n_ctrl_u=8, n_ctrl_v=4).reconstruct(mesh.vertices, mesh.uv)
    results = check_constraints(returned, surf, source=ns)
    assert [r.type for r in results] == ["ruled", "curvature"]


# --- residual is not drift ------------------------------------------------


def test_constraint_result_carries_no_drift():
    # A residual is the degree of constraint violation, reported separately from
    # positional drift. The result type carries no Hausdorff field.
    fields = ConstraintResult.__dataclass_fields__
    assert "residual" in fields
    assert not any("hausdorff" in f or "drift" in f for f in fields)
