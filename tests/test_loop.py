"""Phase 3 gates: the loop.

The synthetic solver deforms the mesh across the exchange with a known stimulus,
and the instrument responds. Every assertion has a known answer from synthetic
ground truth.

This file carries the Phase 3 gates:
    - Stimulus response: drift increases monotonically with deformation
      magnitude, and at magnitude zero the loop reduces to the Phase 1 fit-only
      case (drift equals the honest-fit drift, nonzero, not zero).
    - Budget caught: a deformation that bows a ruled input off ruled is caught by
      the Phase 2 checker (residual over the declared tolerance), and a smaller
      one stays within budget. The checker is unchanged.

It also carries:
    - Determinism: the same magnitude produces an identical deformed mesh on
      repeat runs.
    - The reporting divergence: across the sweep the curvature residual grows
      faster than the positional drift. Resemblance holds while behavior degrades.
    - UV and descriptor preserved through synthetic mode.

The loop runs over the real filesystem through the two exchange directories, with
the solver as its separate entry point (solver_stub.solve_synthetic), so the
process boundary is genuine.
"""

from __future__ import annotations

import numpy as np
import rhino3dm as r3

from passthrough.constraints import check_constraints, curvature_residual, ruled_residual
from passthrough.encode import Descriptor, mesh_filename, read_payload, write_payload
from passthrough.geometry import build_wing_surface, tessellate
from passthrough.driftgauge import hausdorff
from passthrough.reconstruct import ClassicalReconstructor
from passthrough.solver_stub import deform, solve_synthetic


# --- fixtures -------------------------------------------------------------


def make_ruled_surface() -> r3.NurbsSurface:
    """A genuinely ruled surface via CreateRuledSurface, degree 1 in v so the
    v-isocurves are the straight rulings. Same fixture the Phase 2 ruled gate
    uses, so the budget-caught test stands on a surface already shown to read
    ruled at floating-point zero."""
    pts_a = [r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0.3), r3.Point3d(2, 0, 0.1)]
    pts_b = [r3.Point3d(0, 2, 0.2), r3.Point3d(1, 2, 0.5), r3.Point3d(2, 2, 0.4)]
    ca = r3.Curve.CreateControlPointCurve(pts_a, 2)
    cb = r3.Curve.CreateControlPointCurve(pts_b, 2)
    return r3.NurbsSurface.CreateRuledSurface(ca, cb)


def _descriptor() -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-2, "direction": 1}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": 0},
    )


def _round_trip(
    source_surface, reconstructor, magnitude, n_u, n_v, tmp_path, index=0, curvature=True
):
    """One full loop at a given magnitude, across the real exchange.

    Source surface -> tessellate -> encode -> out/ -> synthetic solver deforms ->
    in/ -> decode -> reconstruct -> re-tessellate -> drift against the deformed
    mesh, and (when asked) curvature residual against the source. Returns the
    deformed mesh, the reconstruction, and the measurements. Drift and the
    curvature residual come from separate calls, kept semantically distinct.

    curvature is skipped for the ruled fixture, which is degree 1 in v and so has
    no well-defined second parametric derivative there. That fixture is checked on
    its ruled residual, not its curvature.
    """
    source_mesh = tessellate(source_surface, n_u, n_v)
    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"
    write_payload(out_dir / mesh_filename(index), source_mesh, _descriptor())
    solve_synthetic(out_dir, in_dir, index, magnitude)
    deformed, returned_desc = read_payload(in_dir / mesh_filename(index))

    recon = reconstructor.reconstruct(deformed.vertices, deformed.uv)
    remesh = tessellate(recon, n_u, n_v)
    return {
        "deformed": deformed,
        "recon": recon,
        "descriptor": returned_desc,
        # The deformed mesh is the new target the reconstruction must match.
        "drift": hausdorff(deformed.vertices, remesh.vertices),
        "curvature": curvature_residual(recon, source_surface) if curvature else None,
    }


# --- synthetic mode basics ------------------------------------------------


def test_synthetic_at_zero_is_identity():
    # At magnitude 0 the displacement is exactly zero, so the deformed mesh equals
    # the input mesh. This is what makes the loop reduce to fit-only at zero.
    mesh = tessellate(build_wing_surface(), 30, 12)
    out = deform(mesh, 0.0)
    assert (out.vertices == mesh.vertices).all()
    assert (out.uv == mesh.uv).all()
    assert (out.faces == mesh.faces).all()


def test_synthetic_moves_the_mesh_for_nonzero_magnitude():
    # A nonzero magnitude moves vertices off the source. The peak displacement is
    # near the bump center, and the bump is smooth, so the move is bounded.
    mesh = tessellate(build_wing_surface(), 30, 12)
    out = deform(mesh, 0.1)
    moved = np.linalg.norm(out.vertices - mesh.vertices, axis=1)
    assert moved.max() > 0.0
    # Displacement cannot exceed the magnitude: the bump is at most 1 and the
    # normal is unit length.
    assert moved.max() <= 0.1 + 1e-12


def test_uv_and_descriptor_preserved_through_synthetic(tmp_path):
    # Synthetic mode preserves the parameterization and the budget across the
    # boundary, exactly as identity mode does.
    mesh = tessellate(build_wing_surface(), 24, 10)
    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"
    write_payload(out_dir / mesh_filename(0), mesh, _descriptor())
    solve_synthetic(out_dir, in_dir, 0, 0.05)
    returned, desc = read_payload(in_dir / mesh_filename(0))

    assert (returned.uv == mesh.uv).all()
    assert desc.constraints == _descriptor().constraints
    assert desc.provenance == _descriptor().provenance


# --- determinism ----------------------------------------------------------


def test_determinism_same_magnitude_identical_mesh(tmp_path):
    # The same magnitude produces an identical deformed mesh on repeat runs. The
    # field uses no randomness, so the deformed vertices match exactly, through
    # the file round trip and all.
    mesh = tessellate(build_wing_surface(), 30, 12)
    out_dir = tmp_path / "out"
    write_payload(out_dir / mesh_filename(0), mesh, _descriptor())

    first = tmp_path / "in_a"
    second = tmp_path / "in_b"
    solve_synthetic(out_dir, first, 0, 0.07)
    solve_synthetic(out_dir, second, 0, 0.07)
    a, _ = read_payload(first / mesh_filename(0))
    b, _ = read_payload(second / mesh_filename(0))

    assert (a.vertices == b.vertices).all()
    assert (a.uv == b.uv).all()

    # A different magnitude produces a different mesh, so the equality above is
    # not vacuous.
    third = tmp_path / "in_c"
    solve_synthetic(out_dir, third, 0, 0.08)
    c, _ = read_payload(third / mesh_filename(0))
    assert not (a.vertices == c.vertices).all()


# --- gate 1: stimulus response --------------------------------------------


def test_stimulus_response_drift_monotonic_and_zero_reduces_to_fit_only(tmp_path):
    # The Phase 3 stimulus gate. Sweep the magnitude from zero upward. Drift
    # increases monotonically. At magnitude zero the loop reduces to the Phase 1
    # fit-only case: the drift equals the honest-fit drift, nonzero, because the
    # fixed-resolution fit still applies even with no deformation.
    n_u, n_v = 40, 16
    # The Phase 1 reconstructor: the same fixed 7x4 cubic basis, so magnitude zero
    # ties back to the Phase 1 honest-fit number exactly.
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    ns = build_wing_surface()
    source_mesh = tessellate(ns, n_u, n_v)

    # Fit-only drift, no exchange, no deformation: reconstruct straight from the
    # source tessellation. This is the Phase 1 fit-only case.
    fit_surf = rc.reconstruct(source_mesh.vertices, source_mesh.uv)
    fit_remesh = tessellate(fit_surf, n_u, n_v)
    fit_only = hausdorff(source_mesh.vertices, fit_remesh.vertices)

    magnitudes = [0.0, 0.02, 0.04, 0.08, 0.16]
    drifts = [
        _round_trip(ns, rc, m, n_u, n_v, tmp_path, index=i)["drift"]["max"]
        for i, m in enumerate(magnitudes)
    ]

    # At magnitude zero the loop equals the fit-only case to float tolerance: the
    # synthetic solver applies a zero displacement and the boundary adds nothing.
    assert abs(drifts[0] - fit_only["max"]) < 1e-12
    # Nonzero, not zero: the fixed-resolution fit still has a floor.
    assert drifts[0] > 1e-5

    # Monotonic in the stimulus: a larger deformation produces a larger drift.
    assert all(b > a for a, b in zip(drifts, drifts[1:]))


# --- gate 2: budget caught ------------------------------------------------


def test_budget_caught_off_ruled(tmp_path):
    # The Phase 3 budget gate. A ruled input deformed off ruled is caught by the
    # Phase 2 checker, and a smaller deformation stays within budget. The checker
    # is unchanged; the descriptor declares the tolerance.
    n_u, n_v = 24, 12
    # A reconstructor rich enough to carry the bow into the reconstruction, so the
    # off-ruled violation is visible to the checker rather than smoothed away.
    rc = ClassicalReconstructor(n_ctrl_u=5, n_ctrl_v=5)
    ruled = make_ruled_surface()

    # No deformation: the reconstruction stays ruled, residual at floating-point
    # zero, comfortably inside the declared tolerance.
    at_zero = _round_trip(ruled, rc, 0.0, n_u, n_v, tmp_path, index=0, curvature=False)
    res_zero = check_constraints(at_zero["descriptor"], at_zero["recon"])[0]
    assert res_zero.type == "ruled"
    assert res_zero.passed
    assert res_zero.residual < 1e-6

    # A small deformation bows the rulings a little, still within the 1e-2 budget.
    within = _round_trip(ruled, rc, 0.01, n_u, n_v, tmp_path, index=1, curvature=False)
    res_within = check_constraints(within["descriptor"], within["recon"])[0]
    assert res_within.passed
    assert res_within.residual <= res_within.tolerance

    # A larger deformation bows them past the budget: caught.
    over = _round_trip(ruled, rc, 0.05, n_u, n_v, tmp_path, index=2, curvature=False)
    res_over = check_constraints(over["descriptor"], over["recon"])[0]
    assert not res_over.passed
    assert res_over.residual > res_over.tolerance

    # The residual rose with the deformation: the checker tracks the stimulus.
    assert res_over.residual > res_within.residual > res_zero.residual


# --- the reporting divergence ---------------------------------------------


def test_curvature_residual_grows_faster_than_drift(tmp_path):
    # The demonstration the presentation is built on: as deformation grows, the
    # curvature residual grows faster than the positional drift. Resemblance holds
    # (drift stays a small fraction of the chord) while behavior degrades (the
    # curvature field departs).
    #
    # A richer fixed basis is used here than in the stimulus gate. It fits the
    # undeformed source closely, so both baselines reflect the deformation rather
    # than a coarse-fit floor, and the divergence is read against a clean zero.
    n_u, n_v = 40, 16
    rc = ClassicalReconstructor(n_ctrl_u=12, n_ctrl_v=8)
    ns = build_wing_surface()

    magnitudes = [0.0, 0.02, 0.04, 0.08, 0.16]
    drift = []
    curv = []
    for i, m in enumerate(magnitudes):
        rt = _round_trip(ns, rc, m, n_u, n_v, tmp_path, index=i)
        drift.append(rt["drift"]["max"])
        curv.append(rt["curvature"]["max"])

    # Both rise monotonically with the stimulus.
    assert all(b > a for a, b in zip(drift, drift[1:]))
    assert all(b > a for a, b in zip(curv, curv[1:]))

    # The divergence: relative to the magnitude-zero baseline, the curvature
    # residual grows faster than the drift at every step.
    for d, c in zip(drift[1:], curv[1:]):
        assert c / curv[0] > d / drift[0]

    # Resemblance holds: even at the largest deformation the drift stays well
    # under one percent of the unit chord, while the curvature residual is order 1.
    assert drift[-1] < 1e-2
    assert curv[-1] > 1.0
