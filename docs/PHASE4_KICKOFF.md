# Phase 4 kickoff for Claude Code

Paste into Claude Code web after Phase 3 is committed and merged. This is the headline phase. It builds the thing the hiring team actually probed in the technical screen: carry identity and topology across the boundary, reconstruct using them, and detect unphysical results.

---

Read `AGENT.md` and `PLAN.md` first, then the three specs, then skim the Phase 1 through 3 modules (`reconstruct.py`, `encode.py`, `solver_stub.py`, `constraints.py`). Hold the charter, the voice rules, and the hard do-not list. No real CFD, no server. Stay an instrument.

Phase 3 is done and its 50 tests pass. Build Phase 4 only. Write `specs/spec-04-identity-validity.md` first, then build. Stop at the Phase 4 gate. Do not start the visual phase.

### The concept, stated so it is not muddied

There are three different signals here. Keep them distinct in names, code, and the spec.

1. Topology change. The returned mesh's connectivity differs from what was sent: a node ID is missing, or its set of adjacent node IDs changed. This means the solver remeshed or rewired, so identity did not survive. It is the open-edge case, not the common case. Detect it, flag it as "identity not preserved," and stop, because reconstruction-by-carried-identity cannot proceed without identity. Do not call this a collision.

2. Collision, or self-intersection. Connectivity is intact, every ID and adjacency preserved, but the geometry now passes through itself: the figure-eight, the Mobius, two wing surfaces touching. This is the case the team asked about. The carried topology is what makes it detectable: it defines which closeness is legal. Two nodes that ARE topological neighbors are allowed to be near each other. Two nodes that are NOT neighbors becoming geometrically coincident is a collision. Do not conflate this with topology change. A pure deformation preserves connectivity and can still collide.

3. Fold, or local inversion. Connectivity intact, no global self-intersection, but a face has flipped: its orientation or signed area inverted relative to its neighbors. A local sub-case, detectable from the carried face winding.

### Deliverables

Extend the exchange to carry identity and topology (`encode.py`, schema, descriptor). Alongside vertices, faces, and uv, carry: a stable node ID per vertex, and the edge adjacency (for each node ID, its neighbor node IDs), and the face winding order. These ride across the boundary unchanged. The synthetic solver preserves them (it moves vertices; it does not renumber or rewire).

Add a validity module (`src/driftgauge/validity.py`, honesty category 1, with the narrow-phase intersection test as category 2 if you use a standard algorithm). Three checks, matching the three signals above, each returning a result with the involved node IDs or faces and a clear signal type:

- Identity integrity: every sent node ID returns, and each one's neighbor set is unchanged. Failure means identity not preserved (remesh). Flag and stop.
- Collision: build a spatial index (scipy KD-tree) on the returned vertices. For each pair of nodes that come within a tolerance of each other, check whether they are neighbors in the carried topology. Neighbors are legal. Non-neighbors that are coincident or within tolerance are a collision. Report the offending non-neighbor node pairs. Broad-phase by proximity is sufficient for the gate; a triangle-triangle narrow-phase on candidate non-adjacent face pairs is an optional refinement, not required.
- Fold: using the carried winding, compute each face's orientation on the returned mesh and compare against its neighbors. A normal flip or a non-positive signed area relative to the local neighborhood is a fold. Report the offending faces.

Wire the loop so the validity gate runs after decode and before reconstruction. On a clean return (all three pass), reconstruction proceeds and driftgauge measures deviation as before. On a flagged return, stop and report the violation. The flow is: carry identity and topology out, solver morphs, return, validity gate, then reconstruct and gauge drift only if valid.

### Solver modes for testing

Extend the synthetic solver with deterministic modes that produce known violations: a clean morph (passes), a collision morph (folds the surface through itself so non-neighbor nodes coincide), and a fold morph (inverts a face). Deterministic, so each is a known stimulus.

### Gates (all must pass, with tests)

- Clean passes: a valid deformation passes all three checks, reconstruction proceeds, and the drift number is produced as in Phase 3.
- Collision caught: the collision morph is flagged, and the report names the specific non-neighbor node pair that coincided. A near-miss that stays just clear of the tolerance passes. The detector distinguishes legal neighbor-closeness from illegal non-neighbor-closeness using the carried topology.
- Fold caught: the fold morph is flagged via orientation, naming the inverted face.
- Identity integrity: a returned mesh with altered connectivity for a node ID is flagged as identity-not-preserved, and this is reported as a different signal type than collision.

Add a determinism test: each solver mode produces an identical result on repeat through the file round trip. Keep validity results semantically distinct from both drift and constraint residuals.

### Notes

- Do the spatial work in numpy and scipy. KD-tree for broad-phase. No heavy dependency.
- The reconstructor stays as the carried-UV least-squares fit. The "modify the original control net by a delta" variant (start from the original NURBS and apply the morph rather than regress fresh) is an optional conceptual extension and a talk point about the atomic-edit-versus-whole-cloth fork. Do not build it unless the clean gates are green and time remains.
- Place this file in `docs/` alongside the earlier kickoffs.
