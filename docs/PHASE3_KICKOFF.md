# Phase 3 kickoff for Claude Code

Paste into Claude Code web after Phase 2 is committed and merged. Hands the build from Phase 2 (constraint checker, tests green) to Phase 3: the loop. The synthetic solver does real deformation across the exchange, so the round trip becomes the workflow shape the team actually runs.

---

Read `AGENT.md` and `PLAN.md` first, then `specs/spec-01-geometry.md`, `specs/spec-02-exchange.md`, and `specs/spec-03-metrics-constraints.md`. Skim the Phase 1 and Phase 2 modules. Hold the charter throughout, including the voice rules and the hard do-not list. No real CFD. No server. Stay an instrument.

Phase 2 is done and its 43 tests pass. Build Phase 3 only. Stop at the Phase 3 gate and report against its pass test. Do not start Phase 4 (no colormap or Grasshopper display yet).

The phase has one new behavior and one reporting addition.

New behavior: the synthetic solver (`src/driftgauge/solver_stub.py`, honesty category 1).
- Add a synthetic mode alongside the identity mode. Keep both. The identity solver stays for the Phase 1 exchange-identity gate.
- Synthetic mode reads the mesh from `exchange/out`, applies a smooth, deterministic, normal-direction displacement field, and writes the deformed mesh to `exchange/in`, preserving the uv array and the descriptor.
- The field is a stand-in for what a flow solve would do to a wing surface. State that plainly in the module. It is the seam where a real solver would connect.
- The field is a fixed analytic function of surface position scaled by a single magnitude parameter. No randomness; if any is used, seed it. Determinism is required so the drift it produces is a known stimulus. A pressure-like bump (for example, a smooth function peaked over part of the chord, applied along the surface normal) is a reasonable shape. Do not over-engineer the physics; smoothness and determinism are what matter.

The loop, end to end: source surface, tessellate, encode, `exchange/out`, synthetic solver deforms the mesh, `exchange/in`, decode, reconstruct a NURBS surface from the deformed mesh, re-tessellate, measure drift of the reconstruction against the deformed mesh, and check the declared constraints against the reconstruction. The deformed mesh is the new target; the reconstruction should match it, and the constraint check asks whether the declared properties survived.

Reporting addition: track both drift and the curvature residual across a sweep of deformation magnitudes. This is not just for the gate. The point of interest is that as deformation grows, the curvature residual grows faster than the positional drift. That divergence is the demonstration the presentation is built on (resemblance holds while behavior degrades), so make it easy to read: a small table or arrays of magnitude, drift, curvature residual.

Gates (both must pass, with tests):
- Stimulus response: sweep the deformation magnitude from zero upward. Drift increases monotonically with magnitude. At magnitude zero the loop reduces to the Phase 1 fit-only case (drift equals the honest-fit drift, not zero, because the fixed-resolution fit still applies). The instrument responds correctly to a known stimulus.
- Budget caught: construct a deformation that violates a declared constraint (for example, one that bows a ruled input off-ruled) and confirm the checker's residual exceeds the declared tolerance. Construct or scale one that stays within budget and confirm it passes. The Phase 2 checker, unchanged, does the catching.

Add a determinism test: the same magnitude produces an identical deformed mesh on repeat runs.

Keep the two entry points genuine (geometry side, solver side as separate processes). Keep constraint residuals semantically distinct from drift in all reporting.

Write tests alongside the code, each backed by a known answer from synthetic ground truth. Run `python -m pytest` and report the result. Then stop at the gate.

Place this file in `docs/` alongside the earlier kickoffs.

---

## Build notes (filled in as Phase 3 was built)

What landed, and the decisions worth recording:

- Synthetic mode in `solver_stub.py`. `pressure_bump(uv)` is the fixed analytic shape: a 2D Gaussian peaked at `(u, v) = (0.25, 0.50)`, near the leading edge in chord and mid-span. `displacement(mesh, magnitude) = magnitude * bump * normal`. No randomness. Identity mode stays.
- Normals come from the mesh, not the surface. The far side receives only the mesh (vertices, faces, uv) and the descriptor, never the source `NurbsSurface`, so `surface_normals` builds per-vertex normals from face geometry. A real solver behind this same contract would be in the same position. The mesh-derived normals track `NormalAt` to within a hundredth of a radian, which is plenty for a synthetic stimulus.
- The bump varies in both u and v on purpose. Varying in u puts the peak over part of the chord (the wing shape). Varying in v bows a ruling that runs along v, which is what the budget-caught gate needs.
- Two reconstructor resolutions appear in the tests, each chosen to make its point cleanly. The stimulus gate uses the Phase 1 fixed 7x4 cubic basis, so magnitude zero ties back to the Phase 1 honest-fit number exactly. The divergence demonstration uses a richer 12x8 basis, which fits the undeformed source closely so both baselines reflect the deformation rather than a coarse-fit floor, and the curvature-grows-faster divergence reads against a clean zero.
- The loop orchestration lives in `scripts/run_phase3.py` (the human-readable sweep table and gate check) and in `tests/test_loop.py` (the validated gates), the same split Phase 1 used. No new package module was added; only the solver gained a mode.
- `python -m pytest`: 50 passed (43 from Phase 2, 7 new in `tests/test_loop.py`).
