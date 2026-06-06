"""The Phase 4 validity gates. See spec-04.

Every assertion has a known answer from synthetic ground truth. Each scenario
runs through the real file exchange: the source is written to out/ with its
carried identity, the solver morphs it in a named mode, and the gate reads the
return from in/. The boundary is genuine, not an inline call.

Gates carried here:
    - Clean passes: a valid deformation passes all three checks.
    - Collision caught: the collision morph is flagged and the offending
      non-neighbor node pair is named; a near-miss just clear of tolerance passes.
    - Fold caught: the fold morph is flagged and the inverted face is named.
    - Identity integrity: altered connectivity is flagged as a different signal
      type than collision.
    - Determinism: each mode is identical on repeat through the round trip.
    - Separation: validity results carry no drift and no constraint residual.
"""

from __future__ import annotations

import numpy as np
import pytest

from driftgauge.encode import (
    Descriptor,
    mesh_filename,
    read_payload_full,
    write_payload,
)
from driftgauge.geometry import build_topology, build_wing_surface, tessellate
from driftgauge.solver_stub import solve
from driftgauge.validity import (
    ValidityResult,
    check_collision,
    run_validity_gate,
)

N_U, N_V = 24, 10
TOL = 1e-3


def _descriptor() -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-4}],
        provenance={"source": "wing_v0", "iteration": 0},
    )


def _round_trip(tmp_path, mode: str, index: int = 0, **params):
    """Write the source out, run the solver in `mode`, read the return back.

    Returns (sent_topology, returned_mesh, returned_topology). The sent topology is
    the one written to out/; the returned topology comes from in/.
    """
    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"
    source = tessellate(build_wing_surface(), N_U, N_V)
    sent_topology = build_topology(source)
    write_payload(out_dir / mesh_filename(index), source, _descriptor(), sent_topology)
    solve(out_dir, in_dir, index, mode, **params)
    mesh, _, returned_topology = read_payload_full(in_dir / mesh_filename(index))
    return sent_topology, mesh, returned_topology


def _signals(gate):
    return {r.signal: r for r in gate.results}


# --- clean passes ---------------------------------------------------------


def test_clean_passes_all_three(tmp_path):
    sent, mesh, ret = _round_trip(tmp_path, "clean")
    gate = run_validity_gate(sent, mesh, ret, TOL)
    assert gate.valid
    sig = _signals(gate)
    assert sig["identity_not_preserved"].passed
    assert sig["collision"].passed
    assert sig["fold"].passed
    # A clean morph does move the mesh: this is a deformation, not the identity.
    assert not np.allclose(mesh.vertices, tessellate(build_wing_surface(), N_U, N_V).vertices)


# --- collision caught -----------------------------------------------------


def test_collision_caught_names_non_neighbor_pair(tmp_path):
    sent, mesh, ret = _round_trip(tmp_path, "collision")
    gate = run_validity_gate(sent, mesh, ret, TOL)
    assert not gate.valid
    assert gate.first_violation.signal == "collision"

    # The full curl closes the chord into a loop, so the leading-edge row and the
    # trailing-edge row coincide. At span station b = 0 those are node 0 and node
    # (N_U - 1) * N_V, which are not neighbors. That exact pair is named.
    le_te_pair = (0, (N_U - 1) * N_V)
    collision = _signals(gate)["collision"]
    assert le_te_pair in collision.involved

    # The named pair really is non-adjacent in the carried topology.
    a, b = le_te_pair
    assert b not in sent.adjacency[a]

    # Identity and fold pass: the curl is smooth and rewires nothing.
    assert _signals(gate)["identity_not_preserved"].passed
    assert _signals(gate)["fold"].passed


def test_near_miss_just_clear_of_tolerance_passes(tmp_path):
    # The curl stopped short so the end rows sit at about 1.5 * TOL apart, just
    # clear of the collision tolerance. The detector is not trigger-happy.
    sent, mesh, ret = _round_trip(tmp_path, "near_miss", clearance=1.5 * TOL)
    gate = run_validity_gate(sent, mesh, ret, TOL)
    assert gate.valid
    assert _signals(gate)["collision"].passed


def test_collision_distinguishes_neighbor_from_non_neighbor(tmp_path):
    # Direct statement of the detector's discriminator: neighbor-closeness is
    # legal, non-neighbor-closeness is not. Take the collision mesh and raise the
    # tolerance high enough that many neighbor pairs fall within it; the detector
    # must still report only non-neighbor pairs.
    sent, mesh, ret = _round_trip(tmp_path, "collision")
    result = check_collision(mesh, ret, tolerance=0.05)
    for a, b in result.involved:
        assert b not in ret.adjacency[a]
        assert a not in ret.adjacency[b]


# --- fold caught ----------------------------------------------------------


def test_fold_caught_names_inverted_face(tmp_path):
    sent, mesh, ret = _round_trip(tmp_path, "fold")
    gate = run_validity_gate(sent, mesh, ret, TOL)
    assert not gate.valid
    assert gate.first_violation.signal == "fold"

    fold = _signals(gate)["fold"]
    assert len(fold.involved) >= 1  # at least the inverted face is named

    # Identity and collision pass: a crossing rewires nothing and collapses no
    # vertices onto a non-neighbor.
    assert _signals(gate)["identity_not_preserved"].passed
    assert _signals(gate)["collision"].passed


# --- identity integrity ---------------------------------------------------


def test_identity_not_preserved_is_a_distinct_signal(tmp_path):
    sent, mesh, ret = _round_trip(tmp_path, "remesh")
    gate = run_validity_gate(sent, mesh, ret, TOL)
    assert not gate.valid

    violation = gate.first_violation
    assert violation.signal == "identity_not_preserved"
    assert violation.signal != "collision"
    assert violation.involved  # the missing or rewired node IDs are named

    # The gate short-circuits on identity: it does not run collision or fold,
    # because reading the carried topology is only meaningful when identity held.
    assert len(gate.results) == 1


# --- determinism ----------------------------------------------------------


@pytest.mark.parametrize(
    "mode,params",
    [
        ("identity", {}),
        ("clean", {}),
        ("collision", {}),
        ("near_miss", {"clearance": 1.5 * TOL}),
        ("fold", {}),
        ("remesh", {}),
    ],
)
def test_each_mode_is_deterministic_through_round_trip(tmp_path, mode, params):
    out_dir = tmp_path / "out"
    in_a = tmp_path / "in_a"
    in_b = tmp_path / "in_b"
    source = tessellate(build_wing_surface(), N_U, N_V)
    write_payload(out_dir / mesh_filename(0), source, _descriptor(), build_topology(source))

    solve(out_dir, in_a, 0, mode, **params)
    solve(out_dir, in_b, 0, mode, **params)

    mesh_a, _, topo_a = read_payload_full(in_a / mesh_filename(0))
    mesh_b, _, topo_b = read_payload_full(in_b / mesh_filename(0))

    assert np.array_equal(mesh_a.vertices, mesh_b.vertices)
    assert np.array_equal(topo_a.node_ids, topo_b.node_ids)
    assert topo_a.adjacency == topo_b.adjacency
    assert np.array_equal(topo_a.face_nodes, topo_b.face_nodes)


# --- separation -----------------------------------------------------------


def test_validity_result_carries_no_drift_or_residual(tmp_path):
    # A ValidityResult answers admissibility only. It holds no Hausdorff number and
    # no constraint residual; those are produced by separate code paths.
    sent, mesh, ret = _round_trip(tmp_path, "collision")
    gate = run_validity_gate(sent, mesh, ret, TOL)
    for r in gate.results:
        assert isinstance(r, ValidityResult)
        assert set(vars(r)) == {"signal", "passed", "involved", "detail"}
        assert not hasattr(r, "drift")
        assert not hasattr(r, "residual")
