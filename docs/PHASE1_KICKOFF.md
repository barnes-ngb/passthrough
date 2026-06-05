# Phase 1 kickoff for Claude Code

Paste this into Claude Code web after the repo is committed. It hands the build from Phase 0 (validated, tests green) to Phase 1.

---

Read `AGENT.md` and `PLAN.md` first, then `specs/spec-01-geometry.md` and `specs/spec-02-exchange.md`. Hold the charter throughout, including the voice rules and the hard do-not list. Stay an instrument, not a solver.

Phase 0 is done and its tests pass. Build Phase 1 only. Stop at the Phase 1 gate and report against its pass test. Do not start Phase 2.

Phase 1 has two pieces and two gates.

1. The reconstructor (`src/driftgauge/reconstruct.py`).
   - Define a `Reconstructor` protocol with one method: `reconstruct(points, uv) -> NurbsSurface`.
   - Implement `ClassicalReconstructor`: a least-squares fit of NURBS control points on fixed knot vectors, using the supplied UV as the parameterization. This is a linear system; solve it with `scipy.linalg.lstsq` or the normal equations. Build the basis-function matrix from the surface degree and knot vectors.
   - Add `LearnedReconstructor` as a documented stub with the same interface and a docstring stating what it would do. Do not implement it. Honesty category 3.
   - The fixed UV is the parameterization-gap boundary. Comment it as such.
   - Honesty category 2 for the classical fit. You did not invent least-squares NURBS fitting; you are integrating a known method and validating it.

2. The exchange round trip (`src/driftgauge/encode.py`, plus an identity `solver_stub.py`).
   - Implement `encode`/`decode` per `spec-02-exchange.md`: mesh vertices, faces, the per-vertex uv, and the descriptor, to and from JSON in `exchange/out` and `exchange/in`.
   - Implement `solver_stub` in identity mode only for this phase: read from `exchange/out`, write the mesh back unchanged to `exchange/in`, preserving uv and descriptor.

Gates (both must pass, with tests):
   - Exchange identity: with the identity solver, round-trip drift equals fit-only drift. encode then decode is lossless within tolerance.
   - Honest fit: reconstructing the unperturbed source produces a small, explainable nonzero drift. State why it is nonzero (control point count, knot placement) rather than expecting zero. Add a test asserting the drift is below a stated, justified bound, not that it is zero.

Verify rhino3dm capability before relying on it. The control-point setter is `Points[i, j] = Point4d(x, y, z, w)`, knots are set with `KnotsU.CreateUniformKnots(1.0)`, and evaluation is `PointAt(u, v)`. If you need basis-function values and rhino3dm does not expose them, compute them in numpy from the knot vectors (Cox-de Boor). Author the math in numpy; do not add a heavy dependency.

Write tests alongside the code. Keep every test backed by a known answer from the synthetic ground truth. Run `python -m pytest` and report the result. Then stop at the gate.
