"""Phase 4 gates: carried identity, carried topology, and the validity gate.

The exchange carries a name per vertex and the connectivity that names which
vertices are joined. The validity gate runs after decode and before reconstruction
and separates three failures, each its own signal:

    - identity not preserved (a topology change / remesh)
    - collision (non-neighbor nodes coincide)
    - fold (a face inverted against its carried winding)

Every assertion has a known answer from synthetic ground truth: the solver morph
modes are deterministic stimuli, each built to trip exactly one signal.

This file carries the Phase 4 gates:
    - Clean passes: a valid deformation passes all three checks, reconstruction
      proceeds, and the drift number is produced as in Phase 3.
    - Collision caught: the collision morph is flagged and the report names the
      specific non-neighbor node pair that coincided; a near-miss passes; the
      detector distinguishes legal neighbor-closeness from illegal
      non-neighbor-closeness.
    - Fold caught: the fold morph is flagged via orientation, naming the inverted
      face.
    - Identity integrity: a returned mesh with altered connectivity is flagged as
      identity-not-preserved, a different signal type than collision.

It also carries:
    - Determinism: each morph mode produces an identical result on repeat through
      the file round trip.
    - Topology round-trips through the exchange exactly.
    - Validity results stay semantically distinct from drift and residuals.

The loop runs over the real filesystem through the two exchange directories, with
the solver as its separate entry point (solver_stub.solve_morph), so the process
boundary is genuine.
"""

from __future__ import annotations

import numpy as np
import pytest

from driftgauge.encode import (
    Descriptor,
    decode_topology,
    encode,
    mesh_filename,
    read_payload,
    read_topology,
    topology_from_mesh,
    write_payload,
)
from driftgauge.geometry import build_wing_surface, tessellate
from driftgauge.metrics import hausdorff
from driftgauge.reconstruct import ClassicalReconstructor
from driftgauge.solver_stub import solve_morph
from driftgauge.validity import (
    check_collision,
    check_fold,
    check_identity,
    validity_gate,
)

N_U, N_V = 24, 12


# --- fixtures -------------------------------------------------------------


def _descriptor(index: int = 0) -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-2, "direction": 1}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": index},
    )


def _source_mesh():
    return tessellate(build_wing_surface(), N_U, N_V)


def _clean_distance_regime(mesh, topology):
    """Smallest neighbor (edge) separation and smallest non-neighbor separation on
    a mesh. The collision tolerance must sit between the two: above the smallest
    edge so legal neighbor-closeness is present and ignored, below the smallest
    non-neighbor spacing so a clean return has no non-neighbor pair within it."""
    v = mesh.vertices
    ids = topology.node_ids
    adjacency = topology.adjacency
    from scipy.spatial import cKDTree

    tree = cKDTree(v)
    edge_min = np.inf
    nonneighbor_min = np.inf
    for i, j in tree.query_pairs(r=0.5):
        a, b = int(ids[i]), int(ids[j])
        d = float(np.linalg.norm(v[i] - v[j]))
        if b in adjacency.get(a, ()):
            edge_min = min(edge_min, d)
        else:
            nonneighbor_min = min(nonneighbor_min, d)
    return edge_min, nonneighbor_min


def _tolerance(mesh, topology):
    """A collision tolerance in the valid regime: the geometric mean of the
    smallest edge and the smallest non-neighbor separation, so it is provably
    between them."""
    edge_min, nonneighbor_min = _clean_distance_regime(mesh, topology)
    assert edge_min < nonneighbor_min  # the regime exists for this mesh
    return float(np.sqrt(edge_min * nonneighbor_min))


def _run(out_dir, in_dir, morph, index, **kwargs):
    """One round trip across the real exchange in a given morph mode. Returns the
    sent topology, the returned mesh and topology, and the descriptor."""
    source = _source_mesh()
    write_payload(out_dir / mesh_filename(index), source, _descriptor(index))
    sent_topology = read_topology(out_dir / mesh_filename(index))
    solve_morph(out_dir, in_dir, index, morph, **kwargs)
    returned, desc = read_payload(in_dir / mesh_filename(index))
    returned_topology = read_topology(in_dir / mesh_filename(index))
    return source, sent_topology, returned, returned_topology, desc


# --- exchange carries identity and topology -------------------------------


def test_topology_derived_from_mesh_matches_grid_connectivity():
    # node_ids name vertices by their order; adjacency follows the quad grid. A
    # known interior vertex has its four grid neighbors and nothing else.
    mesh = _source_mesh()
    topo = topology_from_mesh(mesh)
    assert topo.node_ids.tolist() == list(range(mesh.vertices.shape[0]))
    assert topo.winding.shape == mesh.faces.shape

    # Interior vertex at grid (a, b): neighbors are (a±1, b) and (a, b±1).
    a, b = 5, 5
    nid = a * N_V + b
    expected = {(a - 1) * N_V + b, (a + 1) * N_V + b, nid - 1, nid + 1}
    assert set(topo.adjacency[nid]) == expected


def test_topology_round_trips_through_json_exactly():
    # The integer arrays and the integer-keyed graph survive a real JSON
    # serialize/parse and come back equal. This is what crosses the boundary.
    import json

    mesh = _source_mesh()
    topo = topology_from_mesh(mesh)
    obj = json.loads(json.dumps(encode(mesh, _descriptor(), topo)))
    back = decode_topology(obj)

    assert np.array_equal(back.node_ids, topo.node_ids)
    assert np.array_equal(back.winding, topo.winding)
    assert back.adjacency == topo.adjacency


def test_morph_preserves_identity_and_topology_through_exchange(tmp_path):
    # All three morph modes move vertices only. The carried node_ids, adjacency,
    # and winding return unchanged, which is what the geometry checks rely on.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    for i, morph in enumerate(("clean", "collision", "fold")):
        _, sent, _, returned, _ = _run(out_dir, in_dir, morph, i)
        assert np.array_equal(sent.node_ids, returned.node_ids)
        assert np.array_equal(sent.winding, returned.winding)
        assert sent.adjacency == returned.adjacency


# --- gate 1: clean passes -------------------------------------------------


def test_clean_passes_and_reconstruction_proceeds(tmp_path):
    # A valid deformation passes all three checks. The gate clears reconstruction,
    # and the drift number is produced as in Phase 3, against the returned mesh.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    source, sent, returned, returned_topo, _ = _run(out_dir, in_dir, "clean", 0)
    tol = _tolerance(returned, returned_topo)

    gate = validity_gate(sent, returned, returned_topo, tol)
    assert gate.valid
    assert gate.reconstruct
    assert gate.signals == []
    assert all(r.passed for r in gate.results)

    # Reconstruction proceeds only because the gate cleared it, and produces a
    # finite drift number as in Phase 3.
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    recon = rc.reconstruct(returned.vertices, returned.uv)
    remesh = tessellate(recon, N_U, N_V)
    drift = hausdorff(returned.vertices, remesh.vertices)
    assert drift["max"] > 0.0 and np.isfinite(drift["max"])


# --- gate 2: collision caught ---------------------------------------------


def test_collision_caught_and_names_the_pair(tmp_path):
    # The collision morph wraps the span into a loop so the leading-edge corner at
    # one span end lands on the leading-edge corner at the other span end. Those
    # two nodes are not neighbors, so their coincidence is a collision, and the
    # report names the pair.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    _, sent, returned, returned_topo, _ = _run(out_dir, in_dir, "collision", 0)
    tol = _tolerance(_source_mesh(), sent)

    result = check_collision(returned, returned_topo, tol)
    assert not result.passed
    assert result.signal == "collision"

    # The leading-edge corners are grid (0, 0) and (0, N_V - 1): same chord index,
    # opposite span ends. node_ids equal those row indices.
    le_pair = (0, N_V - 1)
    assert le_pair in result.node_ids
    # The named pair are genuinely not topological neighbors.
    assert le_pair[1] not in sent.adjacency.get(le_pair[0], ())
    # They coincided: the smallest non-neighbor separation is essentially zero.
    assert result.detail["min_nonneighbor_separation"] < 1e-9


def test_collision_near_miss_passes(tmp_path):
    # A bend that stops just short of closure keeps the same corners just clear of
    # the tolerance. No non-neighbor pair is within tolerance, so it passes.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"

    source = _source_mesh()
    write_payload(out_dir / mesh_filename(0), source, _descriptor())
    sent = read_topology(out_dir / mesh_filename(0))
    tol = _tolerance(source, sent)

    # Choose a closure short of 1.0 whose residual corner gap clears the tolerance.
    # The corners separate by 2 * R * sin(gap_angle / 2) with R the loop radius.
    solve_morph(out_dir, in_dir, 0, "collision", closure=0.98)
    returned, _ = read_payload(in_dir / mesh_filename(0))
    returned_topo = read_topology(in_dir / mesh_filename(0))

    result = check_collision(returned, returned_topo, tol)
    assert result.passed
    assert result.signal == "ok"


def test_collision_distinguishes_neighbor_from_nonneighbor_closeness(tmp_path):
    # The heart of the check: legal neighbor-closeness is ignored, illegal
    # non-neighbor-closeness is caught, both decided by the carried topology.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    _, sent, returned, returned_topo, _ = _run(out_dir, in_dir, "collision", 0)
    tol = _tolerance(_source_mesh(), sent)

    # At this tolerance there ARE neighbor pairs within range (the smallest edges
    # are shorter than the tolerance), proving the check is not passing by simply
    # finding nothing nearby.
    from scipy.spatial import cKDTree

    tree = cKDTree(returned.vertices)
    pairs = tree.query_pairs(r=tol)
    ids = returned_topo.node_ids
    neighbor_within = [
        (int(ids[i]), int(ids[j]))
        for i, j in pairs
        if int(ids[j]) in returned_topo.adjacency.get(int(ids[i]), ())
    ]
    assert neighbor_within  # legal closeness is present in the query

    result = check_collision(returned, returned_topo, tol)
    # ...yet none of those neighbor pairs is reported; only non-neighbors are.
    for a, b in result.node_ids:
        assert b not in returned_topo.adjacency.get(a, ())
    assert not result.passed


# --- gate 3: fold caught --------------------------------------------------


def test_fold_caught_and_names_the_face(tmp_path):
    # The fold morph creases one interior vertex past its spanwise neighbor, so the
    # two faces sharing it in v invert against the carried winding. The check flags
    # them by orientation and names them; no collision and no identity loss.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    _, sent, returned, returned_topo, _ = _run(out_dir, in_dir, "fold", 0)

    result = check_fold(returned, returned_topo)
    assert not result.passed
    assert result.signal == "fold"
    assert result.faces  # at least one inverted face is named
    # A genuine flip: the worst face points against its neighborhood consensus.
    assert result.detail["worst_orientation_dot"] < 0.0

    # The fold morph is pure: it is not a collision and not an identity loss.
    tol = _tolerance(_source_mesh(), sent)
    assert check_collision(returned, returned_topo, tol).passed
    assert check_identity(sent, returned_topo).passed


def test_clean_mesh_has_no_fold(tmp_path):
    # The fold check does not fire on a valid deformation: a smooth surface has all
    # face normals in local agreement.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    _, _, returned, returned_topo, _ = _run(out_dir, in_dir, "clean", 0)
    result = check_fold(returned, returned_topo)
    assert result.passed
    assert result.detail["worst_orientation_dot"] > 0.0


# --- gate 4: identity integrity -------------------------------------------


def test_identity_not_preserved_is_flagged_as_its_own_signal():
    # A returned mesh whose connectivity changed for a node ID is flagged as
    # identity-not-preserved, a different signal type than collision. Built directly
    # here: a remesh is exactly what the synthetic solver does NOT do, so the
    # stimulus is constructed rather than produced by a morph mode.
    mesh = _source_mesh()
    sent = topology_from_mesh(mesh)

    # A remesh that rewires a node: the corner vertex (0, 0) sits in exactly one
    # face, so dropping that face strips its adjacency while the vertex itself
    # stays. Same vertices, altered faces, changed connectivity for node 0.
    nid = 0  # grid corner (0, 0)
    faces = mesh.faces
    drop = np.where((faces == nid).any(axis=1))[0]
    assert drop.shape[0] == 1  # a corner belongs to a single face
    mask = np.ones(faces.shape[0], dtype=bool)
    mask[drop[0]] = False
    remeshed = type(mesh)(vertices=mesh.vertices, faces=faces[mask], uv=mesh.uv)
    returned = topology_from_mesh(remeshed)

    result = check_identity(sent, returned)
    assert not result.passed
    assert result.signal == "identity_not_preserved"
    assert nid in result.node_ids
    # The neighbor set changed: node 0 had three neighbors and now has none.
    assert result.detail["n_rewired"] >= 1
    # Distinct from a collision: it is reported under a different signal type.
    assert result.signal != "collision"


def test_missing_node_id_is_flagged():
    # A node ID that does not return at all is identity-not-preserved.
    mesh = _source_mesh()
    sent = topology_from_mesh(mesh)

    # Drop the last vertex and every face that used it: that node ID is gone.
    last = mesh.vertices.shape[0] - 1
    keep_faces = ~(mesh.faces == last).any(axis=1)
    smaller = type(mesh)(
        vertices=mesh.vertices[:last],
        faces=mesh.faces[keep_faces],
        uv=mesh.uv[:last],
    )
    returned = topology_from_mesh(smaller)  # node_ids 0..last-1; `last` is missing

    result = check_identity(sent, returned)
    assert not result.passed
    assert result.detail["n_missing"] >= 1


def test_gate_stops_at_identity_before_geometry_checks():
    # When identity fails the gate stops: it reports only the identity result and
    # does not run the collision or fold checks, which would compare against an
    # identity that is gone.
    mesh = _source_mesh()
    sent = topology_from_mesh(mesh)
    last = mesh.vertices.shape[0] - 1
    keep_faces = ~(mesh.faces == last).any(axis=1)
    smaller = type(mesh)(
        vertices=mesh.vertices[:last],
        faces=mesh.faces[keep_faces],
        uv=mesh.uv[:last],
    )
    returned_topo = topology_from_mesh(smaller)

    gate = validity_gate(sent, smaller, returned_topo, tolerance=0.01)
    assert not gate.valid
    assert not gate.reconstruct
    assert len(gate.results) == 1
    assert gate.results[0].signal == "identity_not_preserved"


# --- determinism ----------------------------------------------------------


@pytest.mark.parametrize("morph", ["clean", "collision", "fold"])
def test_morph_is_deterministic_through_round_trip(tmp_path, morph):
    # Each morph mode produces an identical result on repeat through the file round
    # trip. The morphs use no randomness.
    out_dir = tmp_path / "out"
    write_payload(out_dir / mesh_filename(0), _source_mesh(), _descriptor())

    first, second = tmp_path / "in_a", tmp_path / "in_b"
    solve_morph(out_dir, first, 0, morph)
    solve_morph(out_dir, second, 0, morph)
    a, _ = read_payload(first / mesh_filename(0))
    b, _ = read_payload(second / mesh_filename(0))

    assert np.array_equal(a.vertices, b.vertices)
    assert np.array_equal(a.uv, b.uv)
    assert np.array_equal(a.faces, b.faces)


# --- validity is not drift and not a residual -----------------------------


def test_validity_result_carries_no_drift_or_residual(tmp_path):
    # A validity result is its own kind of answer. It carries node IDs, faces, and a
    # signal type, and no Hausdorff number and no constraint residual. The three
    # questions stay on separate code paths.
    out_dir, in_dir = tmp_path / "out", tmp_path / "in"
    _, sent, returned, returned_topo, _ = _run(out_dir, in_dir, "collision", 0)
    tol = _tolerance(_source_mesh(), sent)

    result = check_collision(returned, returned_topo, tol)
    keys = set(result.detail.keys())
    assert not (keys & {"max", "mean", "median", "residual"})
    assert hasattr(result, "signal")
    assert not hasattr(result, "residual")
