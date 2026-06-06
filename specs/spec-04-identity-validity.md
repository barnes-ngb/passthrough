# spec-04-identity-validity.md — Carried identity, carried topology, and the validity gate

Scope: the part the technical screen actually probed. Carry identity and topology
across the exchange boundary, and use them to detect results that are not physical
before any reconstruction runs. This is the headline phase. It sits between decode
and reconstruct in the loop.

Written at the start of the phase, before the code, so the three signals it
separates are defined here and not blurred in the implementation.

## Principle

Reconstruction-by-carried-identity needs the identity to survive the round trip.
So the exchange carries, alongside the mesh, a name per vertex and the connectivity
that names which vertices are joined. The far side moves vertices. It does not
rename or rewire. When the returned mesh still carries the same names and the same
connectivity, the carried topology becomes a tool: it says which geometric
closeness is legal and which is a defect.

The validity gate runs after decode and before reconstruction. On a clean return
it passes and reconstruction proceeds, exactly as Phase 3 measured drift. On a
flagged return it stops and reports the violation. Reconstruction does not run on
a mesh that failed the gate, because the answer would be meaningless.

## Three signals, kept distinct

These are three different failures. They are named separately in the spec, the
code, and the result types, and they are never conflated.

### 1. Topology change (identity not preserved)

The returned connectivity differs from what was sent: a node ID is missing, or a
node's set of adjacent node IDs changed. The far side remeshed or rewired, so the
identity did not survive. This is the open-edge case, not the common case.

Reconstruction-by-carried-identity cannot proceed without the identity, so the
gate flags this as identity-not-preserved and stops. This is not a collision. The
geometry was never even the question; the names and the wiring are gone.

### 2. Collision (self-intersection)

Connectivity is intact. Every node ID and every adjacency is preserved. But the
geometry now passes through itself: the figure-eight, the Mobius band, two wing
surfaces touching. This is the case the team asked about, and it is the one the
carried topology makes detectable.

The carried topology defines which closeness is legal. Two nodes that are
topological neighbors are allowed to sit near each other; that is what an edge is.
Two nodes that are not neighbors becoming geometrically coincident, or coming
within a tolerance of each other, is a collision. A pure deformation preserves
connectivity and can still collide, so this is a separate question from signal 1.

### 3. Fold (local inversion)

Connectivity is intact and there is no global self-intersection, but a face has
flipped: its orientation, read against the carried winding, inverted relative to
its neighbors. A local sub-case of unphysical geometry, detectable from the
carried face winding without any proximity search.

## What the exchange carries

Alongside `vertices`, `faces`, and `uv`, the payload carries the identity and the
topology. The geometry side writes them. The far side returns them unchanged.

- `node_ids`: one stable integer ID per vertex, in vertex order. The ID is the
  vertex's name. It travels out and must come back attached to the same vertex.
  Renumbering or dropping an ID breaks identity.
- `adjacency`: for each node ID, the sorted set of its neighbor node IDs. This is
  the edge graph keyed by name, not by row index. It is what the collision check
  reads to know which closeness is legal, and what the identity check compares to
  decide whether the wiring survived.
- `winding`: the per-face vertex sequence expressed in node IDs, in the face's
  vertex order. The cyclic order is the winding. The fold check reads it to compute
  each face's orientation on the returned mesh against the order that was sent.

The three are derived once from the sent mesh (`node_ids` are the vertex order,
`adjacency` and `winding` come from the faces) and carried verbatim. The synthetic
solver preserves them: it moves vertices and leaves the node IDs, the faces, the
UV, and the winding alone.

## The validity module

`src/driftgauge/validity.py`, honesty category 1. It is instrument logic. The one
place a category 2 method would enter is the optional triangle-triangle
narrow-phase refinement of the collision check, which uses a standard intersection
test. That refinement is not required for the gate and is not built here.

Three checks, one per signal, each returning a result that carries the involved
node IDs or faces and a clear signal type. A result is a `ValidityResult` with the
check name, a pass/fail flag, the signal type on failure, the involved node IDs,
the involved faces, and a `detail` map of secondary numbers.

A validity result is kept semantically distinct from both the positional drift
(`metrics.py`) and the constraint residuals (`constraints.py`). Drift answers "did
the shape move." A residual answers "did a declared property survive." A validity
result answers "is the returned mesh physical at all." Three questions, three code
paths, never folded together.

### Check: identity integrity

Compares the sent topology against the returned topology. Every sent node ID must
return, and each returning node's neighbor set must be unchanged. The returned
adjacency is computed from the returned faces and the returned node IDs, so a
genuine remesh (different faces, different or missing IDs) is caught against the
geometry, not against a claim carried in the payload.

Failure signal: `identity_not_preserved`. The result names the missing node IDs
and the node IDs whose neighbor set changed. On failure the gate stops; the later
checks assume the identity it would compare against.

### Check: collision

Builds a spatial index (scipy KD-tree) on the returned vertices and queries all
vertex pairs within the tolerance. For each such pair it reads the two node IDs and
asks the carried adjacency whether they are neighbors. Neighbors are legal and
ignored. Non-neighbors within the tolerance are a collision.

Failure signal: `collision`. The result names the offending non-neighbor node
pairs and reports the smallest non-neighbor separation found. The tolerance is a
parameter of the gate, chosen below the mesh's smallest non-neighbor spacing so a
clean return has no non-neighbor pair within it, and a near miss that stays just
clear of the tolerance passes.

Broad-phase by proximity is sufficient for the gate. A triangle-triangle
narrow-phase on the candidate non-adjacent face pairs is an optional refinement
(category 2), not built here.

### Check: fold

Uses the carried winding to compute each face's orientation on the returned mesh,
as the area-weighted (Newell) normal of the face polygon in its carried vertex
order. Faces that share an edge are neighbors. A face whose orientation points
against the consensus of its edge-neighbors, a non-positive projection of its
normal onto the summed neighbor normal, has inverted relative to its neighborhood.

Failure signal: `fold`. The result names the inverted faces. A normal flip and a
non-positive signed area relative to the local neighborhood are the same event read
two ways; the projection onto the neighbor consensus captures both.

### The gate

The gate runs the three checks in order on a returned mesh and its topology,
against the sent topology. Identity integrity runs first; if it fails the gate
stops and reports identity-not-preserved without running the geometry checks,
because they would compare against an identity that is gone. If identity holds, the
collision and fold checks run on the returned geometry with the carried topology.
The gate is valid only when all three pass. The loop reconstructs and gauges drift
only on a valid return.

## Solver modes for testing

The synthetic solver gains deterministic modes, each a known stimulus that
produces a known violation.

- Clean morph: a smooth normal-direction deformation, small enough to pass all
  three checks. Reconstruction proceeds and drift is produced as in Phase 3.
- Collision morph: a smooth bend that wraps the span into a loop so the two free
  span edges meet. The leading-edge corner nodes at the two span ends, which are
  not topological neighbors, coincide exactly. No face is inverted and no node is
  renumbered, so this trips the collision check alone. A near-miss variant stops
  the bend just short of closure so the same corners stay just clear of the
  tolerance.
- Fold morph: a single interior vertex is creased past its spanwise neighbor,
  pushed along that edge far enough to overshoot it, so the two faces that share the
  vertex in the span direction fold over and invert against the carried winding
  while the rest of the mesh stays put. The push is along the coarse spanwise edge
  so the overshooting vertex lands clear of any non-neighbor node and the crease
  does not read as a collision. This trips the fold check alone.

Each mode is deterministic. It uses no randomness, so it produces an identical
result on repeat through the file round trip.

## Validation requirements

These are the Phase 4 gates, restated as testable definitions.

- Clean passes. A valid deformation passes all three checks, reconstruction
  proceeds, and the drift number is produced as in Phase 3.
- Collision caught. The collision morph is flagged, and the result names the
  specific non-neighbor node pair that coincided. A near-miss that stays just clear
  of the tolerance passes. The detector distinguishes legal neighbor-closeness from
  illegal non-neighbor-closeness using the carried topology: neighbor pairs within
  the tolerance are present and ignored, while the non-neighbor pair is caught.
- Fold caught. The fold morph is flagged via orientation, naming the inverted face.
- Identity integrity. A returned mesh with altered connectivity for a node ID is
  flagged as identity-not-preserved, reported as a different signal type than
  collision.
- Determinism. Each solver mode produces an identical result on repeat through the
  file round trip.
- Validity is not drift and not a residual. A validity result carries no Hausdorff
  number and no constraint residual; they are reported by separate code paths.

## Named boundaries surfaced here

- The collision check is broad-phase by proximity. The triangle-triangle
  narrow-phase that would confirm an actual surface crossing on candidate
  non-adjacent face pairs is a category 2 refinement, named and not built.
- The synthetic solver preserves identity and topology by construction. A real
  solver that remeshes is exactly the identity-not-preserved case the gate is built
  to catch, and the open frontier where carried-identity reconstruction stops.
- The "modify the original control net by a delta" reconstructor (start from the
  source NURBS and apply the morph rather than regress fresh) is a conceptual
  extension, the atomic-edit-versus-whole-cloth fork. It is a talk point, not built
  in this phase.
