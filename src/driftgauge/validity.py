"""Physical-validity gating on a returned mesh. See specs/spec-04-identity-validity.md.

Honesty category 1: this is instrument logic, authored here. The collision check
uses scipy's KD-tree for the broad phase, a known method integrated here; the
optional triangle-triangle narrow phase named in the spec is not built.

Three signals, kept distinct in name, code, and reported result:

    identity_not_preserved
        The returned connectivity differs from what was sent: a node ID is
        missing or a node's neighbor set changed. The solver remeshed or rewired,
        so identity did not survive. Reconstruction-by-carried-identity cannot
        proceed, so this check runs first and the gate stops on its failure. This
        is not a collision.

    collision
        Connectivity intact, but the geometry passes through itself. Two nodes
        that are not topological neighbors became coincident or came within a
        tolerance. The carried adjacency is what tells this from legal
        neighbor-closeness.

    fold
        Connectivity intact, no global self-intersection, but a face inverted its
        orientation relative to its neighbors. Detected from the carried winding
        evaluated on the returned geometry.

A ValidityResult is a separate type from the Hausdorff drift (metrics.py) and the
constraint residuals (constraints.py). Validity asks whether the returned mesh is
physically admissible. Drift asks whether the shape moved. A residual asks whether
a declared property survived. They are not folded together.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from driftgauge.geometry import Mesh, Topology


# --- result types ---------------------------------------------------------


@dataclass
class ValidityResult:
    """One validity check, run.

    signal:   the signal type. One of "identity_not_preserved", "collision",
              "fold". Held distinct so the three are never conflated downstream.
    passed:   True when the returned mesh is admissible under this check.
    involved: the offending items. Node-ID pairs for collision, node IDs for
              identity, face indices for fold. Empty when the check passed.
    detail:   a short human-readable summary, for the report.
    """

    signal: str
    passed: bool
    involved: list = field(default_factory=list)
    detail: str = ""


@dataclass
class GateOutcome:
    """The result of running the validity gate.

    valid:   True only when every check that ran passed.
    results: the checks that ran, in order. Identity short-circuits, so on an
             identity failure this holds the identity result alone.
    """

    valid: bool
    results: list[ValidityResult] = field(default_factory=list)

    @property
    def first_violation(self) -> ValidityResult | None:
        for r in self.results:
            if not r.passed:
                return r
        return None


# --- check 1: identity integrity ------------------------------------------


def check_identity_integrity(sent: Topology, returned: Topology) -> ValidityResult:
    """Every sent node ID returns, and each one's neighbor set is unchanged.

    A missing ID or a changed neighbor set means the solver remeshed or rewired:
    identity did not survive. Signal identity_not_preserved, distinct from
    collision. The involved set lists the missing IDs and the IDs whose adjacency
    changed.
    """
    sent_ids = {int(x) for x in sent.node_ids}
    ret_ids = {int(x) for x in returned.node_ids}

    missing = sorted(sent_ids - ret_ids)
    added = sorted(ret_ids - sent_ids)

    changed: list[int] = []
    for nid in sorted(sent_ids & ret_ids):
        if sent.adjacency.get(nid, []) != returned.adjacency.get(nid, []):
            changed.append(nid)

    involved = sorted(set(missing) | set(changed))
    passed = not missing and not added and not changed
    if passed:
        detail = "identity preserved: all node IDs and adjacencies intact"
    else:
        detail = (
            f"identity not preserved: {len(missing)} missing, {len(added)} added, "
            f"{len(changed)} rewired node IDs"
        )
    return ValidityResult(
        signal="identity_not_preserved",
        passed=passed,
        involved=involved,
        detail=detail,
    )


# --- check 2: collision ---------------------------------------------------


def check_collision(
    mesh: Mesh, topology: Topology, tolerance: float
) -> ValidityResult:
    """Non-neighbor nodes coincident or within tolerance are a collision.

    A KD-tree on the returned vertices finds every vertex pair within tolerance.
    Pairs that are topological neighbors are legal and skipped: an edge is the
    statement that two nodes may be near each other. A non-neighbor pair within
    tolerance is the surface passing through itself. The involved set lists the
    offending non-neighbor node-ID pairs.

    Broad-phase by proximity. The triangle-triangle narrow phase named in spec-04
    is not built here.
    """
    verts = mesh.vertices
    node_ids = topology.node_ids
    tree = cKDTree(verts)
    pairs = tree.query_pairs(r=tolerance)

    collisions: list[tuple[int, int]] = []
    for i, j in pairs:
        ni, nj = int(node_ids[i]), int(node_ids[j])
        if nj in topology.adjacency.get(ni, []):
            continue  # neighbors: legal closeness
        collisions.append(tuple(sorted((ni, nj))))

    collisions = sorted(set(collisions))
    passed = not collisions
    if passed:
        detail = f"no non-neighbor nodes within tolerance {tolerance:g}"
    else:
        detail = (
            f"{len(collisions)} non-neighbor node pair(s) within tolerance "
            f"{tolerance:g}, e.g. {collisions[0]}"
        )
    return ValidityResult(
        signal="collision", passed=passed, involved=collisions, detail=detail
    )


# --- check 3: fold --------------------------------------------------------


def _face_normal(points: np.ndarray) -> np.ndarray:
    """Newell's normal of a polygon. Stable for quads, where a single cross
    product of two edges can be ill-defined if the quad is non-planar."""
    n = np.zeros(3)
    k = len(points)
    for a in range(k):
        cur = points[a]
        nxt = points[(a + 1) % k]
        n[0] += (cur[1] - nxt[1]) * (cur[2] + nxt[2])
        n[1] += (cur[2] - nxt[2]) * (cur[0] + nxt[0])
        n[2] += (cur[0] - nxt[0]) * (cur[1] + nxt[1])
    return n


def check_fold(mesh: Mesh, topology: Topology) -> ValidityResult:
    """A face whose orientation inverted relative to its neighbors is a fold.

    Each face normal is computed from the carried winding evaluated on the
    returned vertices. Two edge-adjacent faces are orientation-consistent when
    their normals point to the same side (positive dot). The faces are partitioned
    into connected components over consistent edges only. The largest component is
    the correctly oriented majority; faces outside it are folds.

    The component partition is used rather than a bare neighbor average because an
    inverted patch agrees with itself and disagrees only with the surface around
    it. A partition isolates such a patch, while a smooth bend (every adjacent
    pair consistent) stays one component and reads no fold.
    """
    face_nodes = topology.face_nodes
    n_faces = face_nodes.shape[0]
    if n_faces == 0:
        return ValidityResult(signal="fold", passed=True, detail="no faces")

    id_to_idx = {int(n): i for i, n in enumerate(topology.node_ids)}
    verts = mesh.vertices

    normals = np.empty((n_faces, 3))
    for f in range(n_faces):
        idx = [id_to_idx[int(n)] for n in face_nodes[f]]
        normals[f] = _face_normal(verts[idx])

    # Edge -> faces sharing it, keyed by the unordered node-ID pair.
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for f in range(n_faces):
        ids = [int(n) for n in face_nodes[f]]
        k = len(ids)
        for a in range(k):
            e = tuple(sorted((ids[a], ids[(a + 1) % k])))
            edge_faces.setdefault(e, []).append(f)

    # Adjacency over consistent edges only (normals to the same side).
    consistent: list[set[int]] = [set() for _ in range(n_faces)]
    for shared in edge_faces.values():
        for a in range(len(shared)):
            for b in range(a + 1, len(shared)):
                fa, fb = shared[a], shared[b]
                if float(normals[fa] @ normals[fb]) > 0.0:
                    consistent[fa].add(fb)
                    consistent[fb].add(fa)

    # Connected components over consistent adjacency.
    component = [-1] * n_faces
    comp_id = 0
    for start in range(n_faces):
        if component[start] != -1:
            continue
        stack = [start]
        component[start] = comp_id
        while stack:
            f = stack.pop()
            for g in consistent[f]:
                if component[g] == -1:
                    component[g] = comp_id
                    stack.append(g)
        comp_id += 1

    sizes = np.bincount(component, minlength=comp_id)
    majority = int(np.argmax(sizes))
    folds = sorted(f for f in range(n_faces) if component[f] != majority)

    passed = not folds
    if passed:
        detail = "no inverted faces: orientation consistent across the mesh"
    else:
        detail = f"{len(folds)} inverted face(s) relative to the surface, e.g. face {folds[0]}"
    return ValidityResult(
        signal="fold", passed=passed, involved=folds, detail=detail
    )


# --- the gate -------------------------------------------------------------


def run_validity_gate(
    sent_topology: Topology,
    returned_mesh: Mesh,
    returned_topology: Topology,
    tolerance: float,
) -> GateOutcome:
    """Run the three checks in order and report the outcome.

    Identity runs first and short-circuits. The collision and fold checks read the
    carried topology, and that reading is only meaningful when identity survived,
    so on an identity failure the gate stops with the identity result alone.
    """
    identity = check_identity_integrity(sent_topology, returned_topology)
    if not identity.passed:
        return GateOutcome(valid=False, results=[identity])

    collision = check_collision(returned_mesh, returned_topology, tolerance)
    fold = check_fold(returned_mesh, returned_topology)
    results = [identity, collision, fold]
    return GateOutcome(valid=all(r.passed for r in results), results=results)
