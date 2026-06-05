"""Reconstructor tests. Every assertion has a known answer from the synthetic
ground truth.

This file carries the Phase 1 honest-fit gate. The classical fit is a known
method (honesty category 2): the tests validate it, they do not prove a novel
algorithm.
"""

from __future__ import annotations

import numpy as np
import pytest

from driftgauge.geometry import build_wing_surface, control_net, evaluate, tessellate
from driftgauge.metrics import hausdorff
from driftgauge.reconstruct import (
    ClassicalReconstructor,
    LearnedReconstructor,
    Reconstructor,
    basis_matrix_1d,
    clamped_uniform_knots,
    tensor_basis_matrix,
)

# Honest-fit gate bounds, stated and justified.
#
# A reconstructor with fewer chordwise control points than the source (7 vs the
# source's 8) commits to a spline space that cannot contain the source surface.
# The least-squares fit returns the best L2 projection at the sampled points, so
# the unperturbed reconstruction carries a small, nonzero drift. The drift is set
# by the resolution gap and the uniform knot placement, not by floating-point
# error.
#
# Upper bound: 3e-3. The measured drift is ~1.93e-3, below 0.2% of the unit
# chord. The bound is set tight enough to catch a real regression and loose
# enough to absorb the conditioning of the solve.
# Lower bound (nonzero floor): 1e-5. The drift must sit well above float noise
# (~1e-14 for a matching basis), proving it is the resolution gap and not a bug.
HONEST_FIT_MAX_BOUND = 3e-3
HONEST_FIT_NONZERO_FLOOR = 1e-5


def test_basis_partition_of_unity():
    # B-spline basis functions sum to 1 at every parameter. A direct sanity
    # check on the Cox-de Boor implementation.
    knots = clamped_uniform_knots(3, 8)
    ts = np.linspace(0.0, 1.0, 51)
    B = basis_matrix_1d(ts, 3, knots, 8)
    assert np.allclose(B.sum(axis=1), 1.0, atol=1e-12)
    assert (B >= -1e-12).all()  # basis functions are non-negative


def test_numpy_basis_matches_rhino_evaluation():
    # The authored numpy basis must agree with rhino3dm's own NURBS evaluation,
    # otherwise control points fit against it would evaluate wrong when handed
    # back to rhino3dm. Evaluate the source's known control net both ways.
    ns = build_wing_surface()
    net = control_net(ns)  # (8, 4, 4): x, y, z, w with w = 1
    n_u, n_v = net.shape[0], net.shape[1]
    ku = clamped_uniform_knots(3, n_u)
    kv = clamped_uniform_knots(3, n_v)

    rng = np.random.default_rng(0)
    uv = rng.random((200, 2))
    B = tensor_basis_matrix(uv, 3, 3, ku, kv, n_u, n_v)
    pts_numpy = B @ net[:, :, :3].reshape(n_u * n_v, 3)

    max_err = 0.0
    for k in range(uv.shape[0]):
        rh = evaluate(ns, uv[k, 0], uv[k, 1])
        max_err = max(max_err, float(np.linalg.norm(pts_numpy[k] - rh)))
    assert max_err < 1e-12


def test_classical_reconstructor_satisfies_protocol():
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    assert isinstance(rc, Reconstructor)


def test_matching_resolution_recovers_source():
    # When the fixed spline space contains the source (same degree, same control
    # count, same uniform knots), the fit recovers it to float tolerance. This
    # proves the method is correct, so the nonzero drift in the reduced case is
    # the resolution gap and not a defect in the solve.
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)
    rc = ClassicalReconstructor(n_ctrl_u=8, n_ctrl_v=4)
    surf = rc.reconstruct(mesh.vertices, mesh.uv)
    remesh = tessellate(surf, 40, 16)
    drift = hausdorff(mesh.vertices, remesh.vertices)
    assert drift["max"] < 1e-9


def test_honest_fit_small_nonzero_drift():
    # Phase 1 honest-fit gate. Reconstruct the unperturbed source with a fixed
    # basis coarser than the source (7 chordwise control points vs 8). The drift
    # is small, nonzero, and explained by control-point count and knot placement.
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    surf = rc.reconstruct(mesh.vertices, mesh.uv)
    remesh = tessellate(surf, 40, 16)
    drift = hausdorff(mesh.vertices, remesh.vertices)

    # Nonzero: well above float noise. The gate asserts a bound, not zero.
    assert drift["max"] > HONEST_FIT_NONZERO_FLOOR
    # Small and bounded: below the stated, justified bound.
    assert drift["max"] < HONEST_FIT_MAX_BOUND


def test_reconstructed_surface_is_valid_nurbs():
    # The fitted surface must be a usable rhino3dm NurbsSurface: valid, clamped,
    # unit-square domain. Downstream tessellation depends on it.
    ns = build_wing_surface()
    mesh = tessellate(ns, 30, 12)
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    surf = rc.reconstruct(mesh.vertices, mesh.uv)
    assert surf.IsValid
    assert surf.KnotsU.IsClampedStart and surf.KnotsU.IsClampedEnd
    assert surf.KnotsV.IsClampedStart and surf.KnotsV.IsClampedEnd
    du, dv = surf.Domain(0), surf.Domain(1)
    assert (du.T0, du.T1) == (0.0, 1.0)
    assert (dv.T0, dv.T1) == (0.0, 1.0)


def test_learned_reconstructor_is_an_unbuilt_seam():
    # Category 3: architected, not built. Calling it must fail loudly, not
    # silently return something.
    rc = LearnedReconstructor()
    assert isinstance(rc, Reconstructor)
    with pytest.raises(NotImplementedError):
        rc.reconstruct(np.zeros((4, 3)), np.zeros((4, 2)))
