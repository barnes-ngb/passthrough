"""Phase 7b follow-up gate: the reconstructed surface carried back, and the headline
evaluation surfaced beside it.

See specs/spec-07-surface.md and docs/PHASE7B_SURFACE_KICKOFF.md. The run already
reconstructs an analytic surface on a clean pass (the ClassicalReconstructor fit).
This phase carries that surface across the file contract in the portable
`passthrough.surface.v1` form so the Grasshopper bench rebuilds it as a real Rhino
surface beside the original, and surfaces the drift and curvature numbers the loop
already measured at the top of the result.

Every gate has a known answer: the synthetic wing is the input and its drift and
curvature are the Phase 5 numbers.

The gates carried here:
    - A clean round trip writes a `surface` block whose control-net dimensions,
      degrees, and knot lengths are internally consistent (knot length equals control
      count plus degree plus one per direction).
    - A flagged pass writes no surface block (surface is null, in the result and in
      the status marker).
    - The headline drift and curvature numbers in the result match the existing
      Phase 5 values, and agree with the full fields they summarize.
    - The carried surface rebuilds verbatim: nurbs_surface_from_dict reconstructs the
      same surface the run fit, the path the C# bench follows in RhinoCommon.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from passthrough.constraints import curvature_deviation_field
from passthrough.driftgauge import hausdorff
from passthrough.geometry import build_wing_surface, tessellate
from passthrough.reconstruct import (
    SURFACE_FORMAT,
    ClassicalReconstructor,
    nurbs_surface_from_dict,
)
from passthrough.report import read_report
from passthrough.run import (
    N_CTRL_U,
    N_CTRL_V,
    N_U,
    N_V,
    emit_synthetic_payload,
    run_roundtrip,
)
from passthrough.solver_stub import clean_morph


# --- helpers --------------------------------------------------------------


def _internal_clean_pass():
    """Run the loop on the internally generated wing and return the reconstructed
    surface plus the Phase 5 drift and curvature references. This mirrors run_loop on
    a clean pass exactly, so the round-trip result is checked against a known answer."""
    source = tessellate(build_wing_surface(), N_U, N_V)
    returned = clean_morph(source)
    rc = ClassicalReconstructor(n_ctrl_u=N_CTRL_U, n_ctrl_v=N_CTRL_V)
    recon = rc.reconstruct(returned.vertices, returned.uv)
    remesh = tessellate(recon, N_U, N_V)
    drift = hausdorff(returned.vertices, remesh.vertices)
    source_surface = rc.reconstruct(source.vertices, source.uv)
    curv = curvature_deviation_field(recon, source_surface)
    return recon, drift, float(curv["deviation"].max())


def _run_clean(tmp_path) -> tuple[dict, dict]:
    """Run a clean round trip and return (result dict, status dict) read from disk."""
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"
    run_roundtrip(payload, return_dir)
    result = json.loads((return_dir / "result.json").read_text())
    status = json.loads((return_dir / "status.json").read_text())
    return result, status


# --- gate: a clean pass writes an internally consistent surface block ------


def test_clean_pass_writes_surface_block(tmp_path):
    result, status = _run_clean(tmp_path)

    surface = result["surface"]
    assert surface is not None
    assert surface["format"] == SURFACE_FORMAT
    # The status marker points the import side at the file holding the surface.
    assert status["surface"] == "result.json"


def test_surface_block_is_internally_consistent(tmp_path):
    result, _ = _run_clean(tmp_path)
    s = result["surface"]

    cu, cv = s["count_u"], s["count_v"]
    du, dv = s["degree_u"], s["degree_v"]

    # Control net dimensions match the declared counts.
    ctrl = np.asarray(s["control_points"], dtype=float)
    assert ctrl.shape == (cu, cv, 3)

    # Weights are one grid of scalars over the same net; v1 is non-rational.
    weights = np.asarray(s["weights"], dtype=float)
    assert weights.shape == (cu, cv)
    assert np.allclose(weights, 1.0)

    # Knot length equals control count plus degree plus one, per direction.
    assert len(s["knots_u"]) == cu + du + 1
    assert len(s["knots_v"]) == cv + dv + 1

    # The reconstructor fits at the Phase 5 control resolution, cubic by cubic.
    assert (cu, cv) == (N_CTRL_U, N_CTRL_V)
    assert (du, dv) == (3, 3)


def test_surface_knots_are_clamped_and_nondecreasing(tmp_path):
    result, _ = _run_clean(tmp_path)
    s = result["surface"]
    for axis, degree in (("knots_u", s["degree_u"]), ("knots_v", s["degree_v"])):
        knots = np.asarray(s[axis], dtype=float)
        # Nondecreasing, and clamped at both ends (degree + 1 repeats of 0 then 1).
        assert np.all(np.diff(knots) >= -1e-12)
        assert np.allclose(knots[: degree + 1], 0.0)
        assert np.allclose(knots[-(degree + 1):], 1.0)


# --- gate: a flagged pass writes no surface block -------------------------


def test_flagged_pass_writes_no_surface_block(tmp_path):
    payload = tmp_path / "incoming.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"
    run_roundtrip(payload, return_dir, morph="collision")

    result = json.loads((return_dir / "result.json").read_text())
    status = json.loads((return_dir / "status.json").read_text())

    assert result["surface"] is None
    assert status["surface"] is None
    # The headline evaluation is absent too: no reconstruction ran.
    assert result["evaluation"]["drift"] is None
    assert result["evaluation"]["curvature_max"] is None
    assert status["drift_max"] is None
    assert status["curvature_max"] is None


# --- gate: the headline evaluation matches the Phase 5 values --------------


def test_headline_evaluation_matches_phase5(tmp_path):
    result, status = _run_clean(tmp_path)
    _, drift_ref, curvature_ref = _internal_clean_pass()

    ev = result["evaluation"]
    assert ev["drift"]["max"] == pytest.approx(drift_ref["max"])
    assert ev["drift"]["mean"] == pytest.approx(drift_ref["mean"])
    assert ev["drift"]["median"] == pytest.approx(drift_ref["median"])
    assert ev["curvature_max"] == pytest.approx(curvature_ref)

    # The status marker echoes the same two headline numbers.
    assert status["drift_max"] == pytest.approx(drift_ref["max"])
    assert status["curvature_max"] == pytest.approx(curvature_ref)


def test_headline_agrees_with_the_full_fields(tmp_path):
    """One source of numbers: the headline equals the fields it summarizes, not a
    separate recomputation."""
    result, _ = _run_clean(tmp_path)
    ev = result["evaluation"]
    assert ev["drift"] == result["drift"]
    assert ev["curvature_max"] == pytest.approx(max(result["curvature_deviation"]))


# --- gate: the carried surface rebuilds verbatim --------------------------


def test_carried_surface_rebuilds_to_the_same_surface(tmp_path):
    """The block is enough to rebuild the surface with no fitting. Rebuild it in
    rhino3dm (the path the C# bench follows in RhinoCommon) and confirm it evaluates
    to the same points as the surface the run fit."""
    result, _ = _run_clean(tmp_path)
    recon, _, _ = _internal_clean_pass()

    rebuilt = nurbs_surface_from_dict(result["surface"])

    # Sample both on a grid in the shared [0, 1] domain and compare.
    us = np.linspace(0.0, 1.0, 11)
    vs = np.linspace(0.0, 1.0, 7)
    for u in us:
        for v in vs:
            a = recon.PointAt(float(u), float(v))
            b = rebuilt.PointAt(float(u), float(v))
            assert a.X == pytest.approx(b.X, abs=1e-9)
            assert a.Y == pytest.approx(b.Y, abs=1e-9)
            assert a.Z == pytest.approx(b.Z, abs=1e-9)


def test_report_loadable_with_surface(tmp_path):
    """read_report carries the surface dict through unchanged, so a consumer that
    reads the result object (not the raw JSON) still gets the analytic surface."""
    _, _ = _run_clean(tmp_path)
    report = read_report(tmp_path / "return" / "result.json")
    assert report.surface is not None
    assert report.surface["format"] == SURFACE_FORMAT
