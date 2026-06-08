"""Phase 6 gate: the reverse-problem boundary ladder.

The experiment removes carried information rung by rung and measures what happens.
This file carries the Phase 6 gate (spec-06):

    - The ladder runs end to end and produces a comparable result or an honest
      structured non-convergence for every rung. Nothing crashes on the ill-posed
      or blocked rungs.
    - Baseline: rung 1 reproduces the Phase 1 honest-fit drift on the same grid and
      reconstructor, asserted to floating-point tolerance.
    - Degradation: rung 2 drift exceeds rung 1 drift. Rungs 3 and 4 are asserted
      only to be flagged or reported (ill-posed, blocked), not held to a number.
    - Determinism: each rung is reproducible given a fixed seed for the shuffle.

The exploratory rungs are honest about being exploratory. The test asserts they are
reported as ill-posed or blocked, not that they hit a specific drift, because a
"this is ill-posed" result is the valid finding, not a number to defend.
"""

from __future__ import annotations

import numpy as np

from passthrough.boundary import (
    N_CTRL_U,
    N_CTRL_V,
    N_U,
    N_V,
    estimate_grid_uv,
    pca_plane_uv,
    run_ladder,
    summary_table,
)
from passthrough.driftgauge import hausdorff
from passthrough.geometry import build_wing_surface, tessellate
from passthrough.reconstruct import ClassicalReconstructor


# --- the ladder runs end to end -------------------------------------------


def test_ladder_runs_end_to_end_with_a_status_per_rung():
    # Every rung returns a structured result. Nothing crashes on the ill-posed or
    # blocked rungs; they come back as results, not exceptions.
    results = run_ladder()
    assert [r.rung for r in results] == [1, 2, 3, 4]
    valid_status = {"reconstructed", "ill_posed", "blocked"}
    for r in results:
        assert r.status in valid_status
        assert r.reason  # every rung states what happened
        assert r.carried


def test_reconstructed_rungs_carry_all_three_meters():
    # A reconstructed or ill-posed rung carries drift, curvature, and the constraint
    # result, so the rungs are comparable on one set of meters.
    results = run_ladder()
    for r in results:
        if r.reconstructed:
            assert set(r.drift) >= {"max", "mean"}
            assert set(r.curvature) >= {"max", "mean", "le_max"}
            assert len(r.constraints) == 1
            assert r.constraints[0].type == "curvature"
        else:
            # A blocked rung invents no measurement: absent, not zero.
            assert r.drift is None
            assert r.curvature is None
            assert r.constraints == []


# --- gate: rung 1 reproduces the Phase 1 honest-fit drift -----------------


def test_rung1_reproduces_phase1_fit_drift():
    # Rung 1 is the current ClassicalReconstructor with carried UV. It must equal the
    # Phase 1 fit-only drift on the same grid and reconstructor, to float tolerance.
    # The baseline is recomputed here independently rather than hardcoded.
    ns = build_wing_surface()
    source = tessellate(ns, N_U, N_V)
    rc = ClassicalReconstructor(N_CTRL_U, N_CTRL_V)
    fit_surf = rc.reconstruct(source.vertices, source.uv)
    fit_only = hausdorff(source.vertices, tessellate(fit_surf, N_U, N_V).vertices)

    rung1 = run_ladder()[0]
    assert abs(rung1.drift["max"] - fit_only["max"]) < 1e-12
    assert abs(rung1.drift["mean"] - fit_only["mean"]) < 1e-12
    # The Phase 1 honest-fit floor: small, nonzero, bounded.
    assert 1e-5 < rung1.drift["max"] < 3e-3


# --- gate: degradation ----------------------------------------------------


def test_rung2_drift_exceeds_rung1_drift():
    # Removing the carried UV and estimating it makes the fit worse. The
    # parameterization gap made measurable.
    results = run_ladder()
    rung1, rung2 = results[0], results[1]
    assert rung2.drift["max"] > rung1.drift["max"]
    assert rung2.drift["mean"] > rung1.drift["mean"]


def test_rung3_is_reported_ill_posed():
    # Rung 3 strips correspondence. It is asserted only to be flagged ill-posed with
    # a stated reason, not held to a specific drift. The drift it does report is
    # order the part's own size, which is the evidence, not a result to defend.
    rung3 = run_ladder()[2]
    assert rung3.status == "ill_posed"
    assert rung3.reason
    assert rung3.reconstructed  # a fit was produced; it just does not represent the surface
    # Sanity, not a tight assertion: the drift is far above the well-posed rungs.
    assert rung3.drift["max"] > 1.0


def test_rung4_is_blocked_by_the_gate():
    # Rung 4 is a remesh. The validity gate stops it before any fit, on
    # identity-not-preserved. Asserted to be blocked and flagged, not to a number.
    rung4 = run_ladder()[3]
    assert rung4.status == "blocked"
    assert not rung4.reconstructed
    assert rung4.detail["gate_signals"] == ["identity_not_preserved"]
    assert not rung4.detail["gate_valid"]
    # The deeper reason: carried UV cannot be applied across the cardinality change.
    assert rung4.detail["n_sent"] != rung4.detail["n_returned"]
    assert rung4.detail["forced_carried_uv_error"] is not None


# --- gate: determinism ----------------------------------------------------


def test_ladder_is_deterministic_given_the_seed():
    # The only randomness is the rung 3 shuffle. The same seed gives identical drift
    # on every rung, through the whole ladder.
    a = run_ladder(seed=7)
    b = run_ladder(seed=7)
    for ra, rb in zip(a, b):
        assert ra.status == rb.status
        if ra.reconstructed:
            assert ra.drift["max"] == rb.drift["max"]
            assert ra.drift["mean"] == rb.drift["mean"]
            assert ra.curvature["max"] == rb.curvature["max"]


def test_rung3_recovery_is_invariant_to_the_shuffle():
    # The PCA-plane recovery uses no vertex order, so a different shuffle seed gives
    # the same parameterization up to row order, and the same drift. That invariance
    # is the honest statement that no correspondence was used.
    one = run_ladder(seed=1)[2]
    two = run_ladder(seed=999)[2]
    assert abs(one.drift["max"] - two.drift["max"]) < 1e-9
    assert abs(one.drift["mean"] - two.drift["mean"]) < 1e-9


# --- the estimation helpers -----------------------------------------------


def test_estimate_grid_uv_is_normalized_and_differs_from_uniform():
    # The chord-length estimate spans [0, 1] in both parameters and is not the
    # uniform carried UV, because the wing section is not uniformly spaced.
    ns = build_wing_surface()
    source = tessellate(ns, N_U, N_V)
    uv = estimate_grid_uv(source.vertices, N_U, N_V)

    assert uv.shape == source.uv.shape
    assert np.isclose(uv[:, 0].min(), 0.0) and np.isclose(uv[:, 0].max(), 1.0)
    assert np.isclose(uv[:, 1].min(), 0.0) and np.isclose(uv[:, 1].max(), 1.0)
    # It departs from the uniform carried UV in the chordwise direction (the LE
    # clustering), which is what drives the rung 2 degradation.
    assert not np.allclose(uv[:, 0], source.uv[:, 0])


def test_pca_plane_uv_is_deterministic_and_order_invariant():
    # The PCA recovery is deterministic (fixed sign convention) and invariant to the
    # point order, so it carries no correspondence.
    ns = build_wing_surface()
    source = tessellate(ns, N_U, N_V)
    pts = source.vertices

    uv_a, detail_a = pca_plane_uv(pts)
    uv_b, _ = pca_plane_uv(pts)
    assert np.array_equal(uv_a, uv_b)  # deterministic

    rng = np.random.default_rng(3)
    perm = rng.permutation(pts.shape[0])
    uv_shuffled, _ = pca_plane_uv(pts[perm])
    # The same parameter lands on the same point regardless of row order.
    inverse = np.empty_like(perm)
    inverse[perm] = np.arange(perm.shape[0])
    assert np.allclose(uv_shuffled[inverse], uv_a)
    assert "singular_values" in detail_a


# --- the summary ----------------------------------------------------------


def test_summary_table_reports_every_rung_and_dashes_the_blocked_one():
    # The deliverable. One row per rung; the blocked rung shows a dash, not a zero.
    results = run_ladder()
    table = summary_table(results)
    for r in results:
        assert str(r.rung) in table
    assert "reconstructed" in table
    assert "ill_posed" in table
    assert "blocked" in table
    # The blocked rung's row carries no fabricated number.
    blocked_row = [ln for ln in table.splitlines() if "blocked" in ln][0]
    assert "-" in blocked_row
