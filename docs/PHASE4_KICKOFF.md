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

Add a validity module (`src/driftgauge/validity.py`, honesty category 1, with the narrow-phase intersection test as category 2 if you use a standard algorithm). Three checks, matching the three signals above, each returning a result with the involved node IDs or faces and a clear signal type.

Wire the loop so the validity gate runs after decode and before reconstruction. On a clean return (all three pass), reconstruction proceeds and driftgauge measures deviation as before. On a flagged return, stop and report the violation.

### Solver modes for testing

Extend the synthetic solver with deterministic modes that produce known violations: a clean morph (passes), a collision morph (folds the surface through itself so non-neighbor nodes coincide), and a fold morph (inverts a face). Deterministic, so each is a known stimulus.

### Gates (all must pass, with tests)

- Clean passes, collision caught (naming the pair), fold caught (naming the face), identity integrity flagged as a distinct signal, determinism per mode. Keep validity results semantically distinct from both drift and constraint residuals.

### Notes

- numpy and scipy. KD-tree for broad-phase. No heavy dependency.
- The reconstructor stays the carried-UV least-squares fit. The control-net-delta variant is a talk point, not built.
- Place this file in `docs/` alongside the earlier kickoffs.

---

## Build notes (filled in as Phase 4 was built)

What landed, and the decisions worth recording:

- The plan numbering and this phase. `PLAN.md` lists Phase 4 as the visual artifact. This kickoff redefines the headline Phase 4 as identity and validity and says to stop before the visual phase, so that is what was built. The visual work in `PLAN.md` is untouched and remains a later phase.

- Carried identity and topology in `encode.py`. A new `Topology` dataclass holds `node_ids` (a stable ID per vertex, the vertex order at origin), `adjacency` (the edge graph keyed by node ID), and `winding` (each face's node-ID sequence in vertex order). `topology_from_mesh` derives all three from a mesh; `encode` writes them into the payload under a `topology` block, deriving them when not supplied. `decode` stays a two-value return for the existing callers, and `decode_topology` / `read_topology` read the topology as a separate call, so no Phase 1 to 3 code or test changed. Adjacency keys are written as strings because JSON object keys are strings, and converted back to integers on decode.

- The validity module, `validity.py`, honesty category 1. `ValidityResult` carries the check name, a pass flag, the signal type, the involved node IDs, the involved faces, and a `detail` map. It holds no Hausdorff number and no constraint residual: a validity result is its own kind of answer, kept on its own code path. Three checks:
  - `check_identity` compares the sent topology against the returned one. A missing node ID or a changed neighbor set is `identity_not_preserved`. The returned adjacency comes from the returned faces and node IDs, so a genuine remesh is caught against geometry, not against a carried claim.
  - `check_collision` builds a scipy KD-tree on the returned vertices, takes every pair within a tolerance, and reads the carried adjacency to decide each pair. Neighbors are legal and ignored; non-neighbors within the tolerance are a `collision`. It reports the offending non-neighbor node pairs and the smallest non-neighbor separation.
  - `check_fold` computes each face's Newell (area-weighted) normal on the returned vertices in the carried winding order, builds face adjacency by shared edge, and flags a face whose normal projects non-positively onto the summed normal of its edge-neighbors. That one test catches a normal flip and a non-positive signed area relative to the neighborhood, which are the same event read two ways.
  - `validity_gate` runs identity first and stops there on failure, because the geometry checks would compare against an identity that is gone. Otherwise it runs collision and fold. The gate is valid only when all three pass, and the loop reconstructs only on a valid return.

- The Newell normal does not detect a pure normal poke-through. Pushing one vertex along the surface normal rotates the incident faces toward edge-on (the orientation dot approaches zero) but never past it, because the in-plane projected area stays fixed in sign. So the fold morph creases a vertex past its spanwise neighbor instead, which genuinely inverts the two faces that share it in v. The push is along the coarse spanwise edge on purpose: the overshooting vertex (reach 2.5) lands between the second and third span nodes, well clear of any non-neighbor, so the crease reads as a fold and not a collision. A chordwise crease would land on the next chord node and entangle the two signals.

- The collision morph wraps the span into a loop in the (y, z) plane so the two free span edges meet. At full closure the leading-edge corner at one span end (node 0) lands exactly on the leading-edge corner at the other end (node `N_V - 1`); those two are not neighbors, so it is a real collision, and the bend is smooth so no face inverts. Thickness rides radially, and the chord stays the loop axis. A `closure` just under 1.0 is the near-miss that keeps the corners just clear of the tolerance. The exact coincidence is at the leading-edge corners because thickness is zero there, so both span ends sit at the same radius regardless of taper.

- The collision tolerance is chosen, not hardcoded blind. It is set to the geometric mean of the smallest edge length and the smallest non-neighbor separation on the clean mesh, which is provably between the two. That makes a clean return have no non-neighbor pair within the tolerance, while legal neighbor pairs are present within it and correctly ignored. A test asserts that regime holds and that neighbor pairs within the tolerance are not reported, which is the distinction the whole check rests on.

- Solver modes in `solver_stub.py`: `clean_morph`, `collision_morph`, `fold_morph`, registered in `MORPHS`, plus a `solve_morph` entry point that reads the payload, applies the morph, and writes the result with the carried topology passed through verbatim. The CLI grew `clean`, `collision`, and `fold` modes alongside `identity` and `synthetic`. Each morph moves vertices only, so all three preserve identity.

- Tests: `tests/test_validity.py`, 16 new. Clean passes and reconstructs; collision caught naming the pair `(0, N_V - 1)`; near-miss passes; the neighbor-versus-non-neighbor distinction; fold caught naming the faces; identity-not-preserved on a rewired corner node and on a missing node, each a distinct signal; the gate stops at identity; determinism per mode through the file round trip; and validity carries no drift or residual. `scripts/run_phase4.py` drives the whole gate over the real exchange and prints the verdict.

- `python -m pytest`: 66 passed (50 from Phase 3, 16 new in `tests/test_validity.py`).
