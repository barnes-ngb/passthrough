"""The validity gate: is the returned mesh physical at all.

See specs/spec-04-identity-validity.md. This module runs after decode and before
reconstruction. It uses the identity and topology carried across the boundary
(encode.Topology) to separate three distinct failures, named here and never
conflated:

    1. Topology change  -> identity not preserved (a remesh). Flag and stop.
    2. Collision        -> non-neighbor nodes coincide. The carried topology says
                           which closeness is legal.
    3. Fold             -> a face inverted against its carried winding.

Honesty category 1: this is instrument logic, authored here. The one place a
category 2 method would enter is the optional triangle-triangle narrow-phase
refinement of the collision check, which is named in the spec and not built.

A validity result is kept semantically distinct from positional drift
(metrics.py) and from constraint residuals (constraints.py). Drift answers "did
the shape move." A residual answers "did a declared property survive." A validity
result answers "is the returned mesh physical at all." This module returns the
third only. It computes no Hausdorff number and no residual.

Named boundary surfaced in this module:
    The collision check is broad-phase by proximity (a scipy KD-tree). A
    triangle-triangle narrow-phase that would confirm an actual surface crossing
    on candidate non-adjacent face pairs is a category 2 refinement, not built.
    Broad-phase by proximity is sufficient for the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from driftgauge.encode import Topology
from driftgauge.geometry import Mesh


# --- result types ---------------------------------------------------------


@dataclass
class ValidityResult:
    """One validity check, run.

    check:    the check name: "identity_integrity", "collision", or "fold".
    passed:   True when the check found no violation.
    signal:   "ok" when passed; otherwise the violation signal type, one of
              "identity_not_preserved", "collision", "fold". The signal type is
              what keeps the three failures distinct in any report.
    node_ids: the node IDs involved in a violation. For collision, the offending
              non-neighbor pairs as (id_a, id_b) tuples. For identity, the missing
              or rewired node IDs. Empty when passed.
    faces:    the face row indices involved in a violation. For fold, the inverted
              faces. Empty for the other checks.
    detail:   secondary numbers (tolerance, smallest separation, counts). Reported,
              never the basis of a different signal.
    """

    check: str
    passed: bool
    signal: str
    node_ids: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)


@dataclass
class GateResult:
    """The validity gate, run on one returned mesh.

    valid:      True only when all three checks passed.
    reconstruct: True when the loop may proceed to reconstruction. Identical to
                 valid; named separately because it is the decision the loop acts
                 on (reconstruct and gauge drift only on a valid return).
    results:    the ValidityResult of each check that ran, in order. When identity
                fails the gate stops, so only the identity result is present.
    """

    valid: bool
    reconstruct: bool
    results: list[ValidityResult]

    @property
    def signals(self) -> list[str]:
        """The violation signal types raised, in order. Empty on a clean return."""
        return [r.signal for r in self.results if not r.passed]


# --- check 1: identity integrity ------------------------------------------


def check_identity(sent: Topology, returned: Topology) -> ValidityResult:
    """Every sent node ID returns, and each one's neighbor set is unchanged.

    The returned adjacency is taken from the returned topology, which is built from
    the returned faces and node IDs, so a genuine remesh is caught against the
    returned geometry's own wiring rather than against a claim. A missing node ID
    or a changed neighbor set means the far side remeshed or rewired: identity not
    preserved. Reconstruction-by-carried-identity cannot proceed, so the gate stops
    on this signal.
    """
    returned_ids = set(int(i) for i in returned.node_ids)
    missing = [int(i) for i in sent.node_ids if int(i) not in returned_ids]

    rewired: list[int] = []
    for nid, sent_nbrs in sent.adjacency.items():
        if int(nid) not in returned_ids:
            continue
        returned_nbrs = returned.adjacency.get(int(nid), ())
        if tuple(sent_nbrs) != tuple(returned_nbrs):
            rewired.append(int(nid))

    involved = sorted(set(missing) | set(rewired))
    passed = not involved
    return ValidityResult(
        check="identity_integrity",
        passed=passed,
        signal="ok" if passed else "identity_not_preserved",
        node_ids=involved,
        detail={
            "n_missing": len(missing),
            "n_rewired": len(rewired),
            "n_sent": int(sent.node_ids.shape[0]),
            "n_returned": int(returned.node_ids.shape[0]),
        },
    )


# --- check 2: collision ---------------------------------------------------


def check_collision(
    mesh: Mesh, topology: Topology, tolerance: float
) -> ValidityResult:
    """Non-neighbor nodes within the tolerance are a collision.

    A KD-tree on the returned vertices yields every vertex pair within the
    tolerance. For each pair the two node IDs are read and the carried adjacency is
    asked whether they are neighbors. Neighbors are legal closeness (that is what an
    edge is) and are ignored. Non-neighbors within the tolerance are the collision:
    the geometry passed through itself where the topology says it should not have.

    The tolerance is the caller's. It is chosen below the mesh's smallest
    non-neighbor spacing, so a clean return has no non-neighbor pair within it.
    Legal neighbor pairs may well fall within the tolerance; they are present in the
    query and dropped here, which is how the check distinguishes legal
    neighbor-closeness from illegal non-neighbor-closeness.
    """
    verts = mesh.vertices
    node_ids = topology.node_ids
    adjacency = topology.adjacency

    tree = cKDTree(verts)
    pairs = tree.query_pairs(r=float(tolerance))

    offending: list[tuple[int, int]] = []
    min_sep = float("inf")
    for i, j in pairs:
        a, b = int(node_ids[i]), int(node_ids[j])
        if b in adjacency.get(a, ()):  # neighbors: legal closeness
            continue
        sep = float(np.linalg.norm(verts[i] - verts[j]))
        min_sep = min(min_sep, sep)
        offending.append((min(a, b), max(a, b)))

    offending = sorted(set(offending))
    passed = not offending
    return ValidityResult(
        check="collision",
        passed=passed,
        signal="ok" if passed else "collision",
        node_ids=offending,
        detail={
            "tolerance": float(tolerance),
            "n_pairs_within_tolerance": len(pairs),
            "n_offending_pairs": len(offending),
            "min_nonneighbor_separation": (
                min_sep if min_sep != float("inf") else float("nan")
            ),
        },
    )


# --- check 3: fold --------------------------------------------------------


def _face_normals(verts: np.ndarray, face_rows: np.ndarray) -> np.ndarray:
    """Area-weighted (Newell) normal of each face polygon, in its winding order.

    N_f = sum_k ( P_k x P_{k+1} ) over the face's vertices, cyclically. The sign of
    this vector is the face's orientation; its magnitude is twice the projected
    area. Newell's form is used so a non-planar quad still gets a well-defined
    orientation, which is the case the fold check has to read.
    """
    P = verts[face_rows]  # (M, K, 3)
    P_next = np.roll(P, -1, axis=1)
    return np.cross(P, P_next).sum(axis=1)


def check_fold(mesh: Mesh, topology: Topology) -> ValidityResult:
    """A face whose orientation inverts relative to its edge-neighbors is a fold.

    Each face's orientation is its Newell normal computed on the returned vertices
    in the carried winding order. Two faces are neighbors when they share an edge
    (a consecutive node-ID pair in the winding). For each face, the consensus of its
    neighbors is the sum of their normals; the face has inverted when its own normal
    projects non-positively onto that consensus. That single test catches both a
    normal flip and a non-positive signed area relative to the local neighborhood:
    they are the same event read two ways.

    Reading orientation from the carried winding is the point. The winding is the
    order that was sent; the fold is detected against it, not against whatever order
    the returned face array happens to hold.
    """
    verts = mesh.vertices
    winding = topology.winding
    id_to_row = {int(nid): r for r, nid in enumerate(topology.node_ids)}
    face_rows = np.array(
        [[id_to_row[int(nid)] for nid in face] for face in winding], dtype=int
    )

    normals = _face_normals(verts, face_rows)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    safe = np.where(lengths < 1e-15, 1.0, lengths)
    unit = normals / safe

    # Face adjacency by shared edge, in node-ID space.
    edge_to_faces: dict[frozenset, list[int]] = {}
    for fi, face in enumerate(winding):
        k = len(face)
        for a in range(k):
            e = frozenset((int(face[a]), int(face[(a + 1) % k])))
            edge_to_faces.setdefault(e, []).append(fi)

    n_faces = winding.shape[0]
    face_neighbors: list[set[int]] = [set() for _ in range(n_faces)]
    for fs in edge_to_faces.values():
        for a in fs:
            for b in fs:
                if a != b:
                    face_neighbors[a].add(b)

    offending: list[int] = []
    worst_dot = 1.0
    for fi in range(n_faces):
        nbrs = face_neighbors[fi]
        if not nbrs:
            continue
        consensus = normals[list(nbrs)].sum(axis=0)
        clen = float(np.linalg.norm(consensus))
        if clen < 1e-15:
            continue
        dot = float(unit[fi] @ (consensus / clen))
        worst_dot = min(worst_dot, dot)
        if dot <= 0.0:
            offending.append(fi)

    passed = not offending
    return ValidityResult(
        check="fold",
        passed=passed,
        signal="ok" if passed else "fold",
        faces=sorted(offending),
        node_ids=[
            tuple(int(n) for n in winding[fi]) for fi in sorted(offending)
        ],
        detail={
            "n_faces": int(n_faces),
            "n_inverted": len(offending),
            "worst_orientation_dot": worst_dot,
        },
    )


# --- the gate -------------------------------------------------------------


def validity_gate(
    sent: Topology,
    returned_mesh: Mesh,
    returned_topology: Topology,
    tolerance: float,
) -> GateResult:
    """Run the three checks in order on a returned mesh, against the sent topology.

    Identity integrity runs first. If it fails the gate stops and reports
    identity-not-preserved without running the geometry checks: they would compare
    against an identity that is gone. If identity holds, collision and fold run on
    the returned geometry with the carried topology. The gate is valid only when all
    three pass. The loop reconstructs and gauges drift only on a valid return.
    """
    identity = check_identity(sent, returned_topology)
    if not identity.passed:
        return GateResult(valid=False, reconstruct=False, results=[identity])

    collision = check_collision(returned_mesh, returned_topology, tolerance)
    fold = check_fold(returned_mesh, returned_topology)
    results = [identity, collision, fold]
    valid = all(r.passed for r in results)
    return GateResult(valid=valid, reconstruct=valid, results=results)
