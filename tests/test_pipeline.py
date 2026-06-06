"""The loop with the validity gate wired in. See spec-04.

The gate sits after decode and before reconstruction. On a clean return
reconstruction proceeds and a drift number is produced as in Phase 3. On a flagged
return the pipeline stops and reports the violation, and no reconstruction runs.
"""

from __future__ import annotations

from driftgauge.encode import (
    Descriptor,
    mesh_filename,
    write_payload,
)
from driftgauge.geometry import build_topology, build_wing_surface, tessellate
from driftgauge.pipeline import process_return
from driftgauge.reconstruct import ClassicalReconstructor
from driftgauge.solver_stub import solve

N_U, N_V = 24, 10
TOL = 1e-3


def _descriptor() -> Descriptor:
    return Descriptor(
        constraints=[
            {"type": "ruled", "tolerance": 1e-4},
            {"type": "curvature", "tolerance": 5.0},
        ],
        provenance={"source": "wing_v0", "iteration": 0},
    )


def _setup(tmp_path, mode, **params):
    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"
    source_surface = build_wing_surface()
    source = tessellate(source_surface, N_U, N_V)
    sent_topology = build_topology(source)
    write_payload(out_dir / mesh_filename(0), source, _descriptor(), sent_topology)
    solve(out_dir, in_dir, 0, mode, **params)
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    return process_return(
        in_dir / mesh_filename(0),
        sent_topology,
        rc,
        source_surface,
        N_U,
        N_V,
        TOL,
    )


def test_clean_return_is_reconstructed_and_drift_is_produced(tmp_path):
    result = _setup(tmp_path, "clean")
    assert result.valid
    assert result.gate.valid
    # Reconstruction ran: a drift number is produced as in Phase 3.
    assert result.drift is not None
    assert result.drift["max"] >= 0.0
    # Constraint residuals are produced alongside, as distinct results.
    assert result.constraints is not None
    assert {c.type for c in result.constraints} == {"ruled", "curvature"}


def test_collision_return_stops_before_reconstruction(tmp_path):
    result = _setup(tmp_path, "collision")
    assert not result.valid
    assert result.gate.first_violation.signal == "collision"
    # No reconstruction was attempted on an inadmissible mesh.
    assert result.drift is None
    assert result.constraints is None


def test_fold_return_stops_before_reconstruction(tmp_path):
    result = _setup(tmp_path, "fold")
    assert not result.valid
    assert result.gate.first_violation.signal == "fold"
    assert result.drift is None
    assert result.constraints is None


def test_remesh_return_stops_with_identity_signal(tmp_path):
    result = _setup(tmp_path, "remesh")
    assert not result.valid
    assert result.gate.first_violation.signal == "identity_not_preserved"
    assert result.drift is None
    assert result.constraints is None
