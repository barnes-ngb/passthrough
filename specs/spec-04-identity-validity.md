# spec-04-identity-validity.md — Carried identity and validity gating

Scope: carry identity and topology across the exchange boundary, and gate the
returned mesh on three physical-validity signals before reconstruction runs. This
is the headline phase. It makes the round trip detect the failure modes a real
optimization loop cares about: a solver that remeshed, a deformation that drove
the surface through itself, and a deformation that inverted a face.

Written at the start of the phase, before the code, so the three signals are
defined here and not blurred in the implementation. The discipline of this spec
is that the three signals stay distinct. They have different causes, different
detectors, and different reported signal types.

## Principle

A reconstruction-by-carried-identity needs identity to carry. Vertices alone do
not say which point is which or which points were connected. So the exchange
carries, alongside the geometry, a stable node ID per vertex, the edge adjacency
keyed by node ID, and the face winding order keyed by node ID. These ride across
the boundary unchanged. The synthetic solver moves vertices. It does not
renumber and it does not rewire.

Carried topology is what makes validity checkable. It defines which closeness is
legal (a node near its own neighbor) and which is not (a node coincident with a
stranger). It defines the original face winding, so an inverted face is a
measurable disagreement rather than an opinion. Without the carried identity the
returned mesh is just a point cloud and none of the three checks below can be
stated.

## The three signals, kept distinct

These are three different things. They are named separately in the spec, the
code, and the reported result. Conflating any two of them is the error this spec
exists to prevent.

### 1. Topology change (identity not preserved)

The returned mesh's connectivity differs from what was sent. A node ID is
missing, or a node ID's set of adjacent node IDs changed. This means the solver
remeshed or rewired, so identity did not survive the boundary. It is the
open-edge case, not the common case.

When identity did not survive, reconstruction-by-carried-identity cannot
proceed: the thing it carries is gone. So this check runs first, and on failure
the gate stops. This is not a collision. A collision is a geometric event on an
intact topology; this is the topology itself changing. The reported signal type
is `identity_not_preserved`.

### 2. Collision (self-intersection)

Connectivity is intact. Every node ID and every adjacency is preserved. The
geometry now passes through itself: the figure-eight, the surface folded back
onto itself, two regions touching. This is the case the technical screen probed.

The carried topology is what makes it detectable. Topology defines which
closeness is legal. Two nodes that ARE topological neighbors are allowed to be
geometrically near each other, because that is what an edge is. Two nodes that
are NOT neighbors becoming coincident, or coming within a tolerance, is a
collision. A pure deformation preserves connectivity and can still collide, so
collision is not topology change. The reported signal type is `collision`.

### 3. Fold (local inversion)

Connectivity is intact and there is no global self-intersection, but a face has
flipped. Its orientation inverted relative to its neighbors. This is a local
sub-case, detectable from the carried face winding evaluated on the returned
geometry. The reported signal type is `fold`.

## What the exchange carries

Added to the payload alongside `vertices`, `faces`, and `uv`:

- `node_ids`: one stable integer ID per vertex, by vertex position. The identity
  that travels with the point. For a freshly tessellated mesh the IDs are the
  vertex indices, but they are carried explicitly so a returned mesh can be
  checked against what was sent rather than assumed.
- `adjacency`: a map from node ID to the sorted list of its neighbor node IDs,
  derived from the mesh edges. JSON object keys are strings; the loader parses
  them back to integers.
- `face_nodes`: for each face, the ordered node IDs in winding order. This is the
  faces array expressed in node IDs rather than vertex positions, so the winding
  is identity-anchored and survives independent of vertex ordering.

These three together are the `Topology`. It is built from a mesh by
`build_topology`, serialized in the payload, and preserved verbatim by the
synthetic solver. The `Mesh` (vertices, faces, uv) and the `Topology` are
separate objects so the geometry and its identity are not entangled.

Backward compatibility: `decode` keeps returning `(mesh, descriptor)` so the
Phase 1 to 3 call sites are unchanged. `decode_topology` and `read_payload_full`
return the topology for the Phase 4 path. A payload written without an explicit
topology block has one rebuilt from its mesh on read, so old files still load.

## The validity module

`src/driftgauge/validity.py`, honesty category 1. Three checks, one per signal,
each returning a `ValidityResult` carrying the signal type, a pass flag, the
involved node IDs or faces, and a short human-readable detail. The results are a
distinct type from both the Hausdorff drift (`metrics.py`) and the constraint
residuals (`constraints.py`). Validity answers "is the returned mesh physically
admissible." Drift answers "did the shape move." A residual answers "did the
declared property survive." Three questions, three result types, not folded
together.

### Identity integrity

Input: the sent topology and the returned topology. Every sent node ID must
return, and each returned node's neighbor set must equal what was sent. A missing
ID or a changed neighbor set fails the check with signal `identity_not_preserved`.
The involved set lists the missing IDs and the IDs whose adjacency changed.

### Collision

Input: the returned mesh and the carried topology, plus a distance tolerance.
Build a scipy KD-tree on the returned vertices and query all vertex pairs within
the tolerance. For each such pair, look up whether the two node IDs are neighbors
in the carried adjacency. Neighbor pairs are legal and skipped. A non-neighbor
pair within the tolerance is a collision. The involved set lists the offending
non-neighbor node-ID pairs. Signal `collision`.

Broad-phase by proximity is sufficient for the gate. A triangle-triangle
narrow-phase on candidate non-adjacent face pairs is an optional category-2
refinement, named here and not required for the gate.

### Fold

Input: the returned mesh and the carried topology. For each face, take its node
IDs in carried winding order, look up the returned vertex positions, and compute
the face normal (Newell's method, which is stable for quads). Build face
adjacency from shared edges. Two adjacent faces are orientation-consistent when
their normals point to the same side (positive dot). Partition the faces into
connected components over consistent edges only. The largest component is the
correctly oriented majority. Faces outside it are folds: locally inverted
relative to the surrounding surface. The involved set lists the inverted face
indices. Signal `fold`.

This component method is chosen over a bare neighbor-average because an inverted
patch is internally self-consistent. Its own faces agree with each other and
disagree only with the surface around them, so a component partition isolates it
cleanly while a smooth bend (every adjacent pair consistent) stays one component
and reads no fold.

## The gate, wired into the loop

`run_validity_gate(sent_topology, returned_mesh, returned_topology, tolerance)`
runs the three checks in order: identity, then collision, then fold. Identity
runs first and short-circuits, because the other two read the carried topology
and that reading is only meaningful when identity survived. The gate returns the
results it ran and an overall valid flag.

The loop wiring lives in `pipeline.py`. After a return is decoded and before any
reconstruction, the gate runs. On a clean return (all checks pass) reconstruction
proceeds, the surface is re-tessellated, and drift and constraint residuals are
produced as in the earlier phases. On a flagged return the pipeline stops and
reports the violation. No reconstruction is attempted on an inadmissible mesh.

Flow: carry identity and topology out, the solver morphs, the mesh returns, the
validity gate runs, and only a valid return is reconstructed and gauged for
drift.

## The synthetic solver modes

The solver gains deterministic morph modes, each a known stimulus that produces a
known violation or none. Determinism means a mode produces an identical result on
repeat through the file round trip.

- `identity`: returns the mesh unchanged. The Phase 1 mode, retained.
- `clean`: a smooth normal-direction displacement field, small in amplitude. A
  pressure-like bump, zero on the boundary. It moves vertices and preserves
  topology, and it passes all three checks. This is the Phase 3 deformation,
  carried forward as the valid baseline.
- `collision`: curls the surface chordwise into a closed loop. The leading-edge
  row and the trailing-edge row, which are not neighbors in the open grid, come
  to the same place. The bend is smooth, so no face inverts. This is a true
  self-intersection on intact topology.
- `near_miss`: the same curl stopped just short of closing, so the two end rows
  come within just over the tolerance and the collision check passes. The control
  case that proves the detector is not trigger-happy.
- `fold`: crosses one interior node with a neighbor by exchanging their
  positions. The map from parameter space to 3D now sends two adjacent parameters
  to crossed locations, so the face on their shared edge winds backwards. This is a
  genuine local inversion. Poking a single vertex out of plane only tilts the
  incident quads toward perpendicular, which is not an inversion; crossing two
  parameters reverses a face's winding outright. The fold check flags the inverted
  face and the other two checks pass.
- `remesh`: a clean displacement plus a rewrite of one node's identity in the
  returned topology, standing in for a solver that remeshed. The identity check
  flags it as `identity_not_preserved`, a different signal than collision.

The curl modes use the section's zero-thickness leading and trailing edges: both
end rows sit on the chord line, so curling the chord into a circle of fixed
radius brings them to the same radius and the same angle modulo a full turn,
which is an exact coincidence rather than an approximate one. That is what makes
the collision a known answer.

## Validation requirements (the Phase 4 gates)

- Clean passes. The clean morph passes identity, collision, and fold,
  reconstruction proceeds, and a drift number is produced as in Phase 3.
- Collision caught. The collision morph is flagged, and the report names the
  specific non-neighbor node pair that coincided. The near-miss, just clear of
  the tolerance, passes. The detector tells legal neighbor-closeness from illegal
  non-neighbor-closeness using the carried topology.
- Fold caught. The fold morph is flagged via orientation, naming the inverted
  face.
- Identity integrity. A returned mesh with altered connectivity for a node ID is
  flagged as `identity_not_preserved`, reported as a different signal type than
  collision.
- Determinism. Each solver mode produces an identical result on repeat through
  the file round trip.
- Separation. Validity results carry no drift number and no constraint residual.
  The three are produced by separate code paths and combined only later, in a
  report, as distinct fields.

## Named boundaries surfaced here

- The collision check is broad-phase by proximity. The narrow-phase
  triangle-triangle test is the named refinement, not built for the gate.
- The solver preserves identity and topology by construction. A real solver that
  remeshes is exactly the `identity_not_preserved` case this gate exists to
  catch, which is why the synthetic `remesh` mode is included.
- The carried winding assumes the faces are simple polygons whose orientation is
  well defined. True for the grid tessellation by construction.
