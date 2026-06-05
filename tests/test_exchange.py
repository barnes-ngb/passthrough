"""Phase 1 exchange-identity gate.

With the identity solver, the round-trip drift equals the fit-only drift. The
boundary crossing (encode, write to out/, identity solver, write to in/, read,
decode) adds nothing. Any drift that remains is the fit alone.

The test runs the boundary over the real filesystem, through two directories,
the way the protocol defines it. The solver step is the separate entry point in
solver_stub, not an inline function call dressed up as a boundary.
"""

from __future__ import annotations

from driftgauge.encode import Descriptor, mesh_filename, read_payload, write_payload
from driftgauge.geometry import build_wing_surface, tessellate
from driftgauge.metrics import hausdorff
from driftgauge.reconstruct import ClassicalReconstructor
from driftgauge.solver_stub import solve_identity


def _descriptor() -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-4}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": 0},
    )


def _drift_of_reconstruction(source_mesh, reconstructed_from_mesh, rc, n_u, n_v):
    surf = rc.reconstruct(reconstructed_from_mesh.vertices, reconstructed_from_mesh.uv)
    remesh = tessellate(surf, n_u, n_v)
    return hausdorff(source_mesh.vertices, remesh.vertices)


def test_exchange_identity_gate(tmp_path):
    n_u, n_v = 40, 16
    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"

    ns = build_wing_surface()
    source = tessellate(ns, n_u, n_v)
    # Reduced fixed basis: the realistic bounded reconstructor, so the fit-only
    # drift is the honest nonzero value, not float noise. The gate is that the
    # boundary preserves whatever that value is.
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)

    # Fit-only path: reconstruct straight from the source tessellation.
    fit_only = _drift_of_reconstruction(source, source, rc, n_u, n_v)

    # Round-trip path: source -> encode -> out/ -> identity solver -> in/ ->
    # decode -> reconstruct -> re-tessellate -> drift.
    index = 0
    write_payload(out_dir / mesh_filename(index), source, _descriptor())
    solve_identity(out_dir, in_dir, index)
    returned, returned_desc = read_payload(in_dir / mesh_filename(index))
    round_trip = _drift_of_reconstruction(source, returned, rc, n_u, n_v)

    # The boundary adds nothing: the two drifts are equal. Lossless encode/decode
    # and an identity solver mean the reconstructor sees identical inputs, so the
    # drifts match to float tolerance.
    for key in ("max", "mean", "median"):
        assert abs(round_trip[key] - fit_only[key]) < 1e-12

    # The descriptor survived the crossing unchanged: the budget is still tied to
    # the returned mesh.
    assert returned_desc.constraints == _descriptor().constraints
    assert returned_desc.provenance == _descriptor().provenance


def test_identity_solver_returns_mesh_unchanged(tmp_path):
    # Isolate the solver: in identity mode the returned vertices and uv equal the
    # sent ones exactly.
    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"
    source = tessellate(build_wing_surface(), 20, 10)
    write_payload(out_dir / mesh_filename(3), source, _descriptor())
    solve_identity(out_dir, in_dir, 3)
    returned, _ = read_payload(in_dir / mesh_filename(3))

    drift = hausdorff(source.vertices, returned.vertices)
    assert drift["max"] == 0.0
    assert (source.uv == returned.uv).all()
