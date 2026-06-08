# PLAN.md — passthrough build

Phased build. Each phase ends at a human approval gate. Do not start a phase until the previous gate passes and the owner approves. Build the hardest principle first, then the loop around it.

## How to read this plan

- Goal: what the phase establishes.
- Deliverables: the files or functions it produces.
- Gate: the concrete pass test. This is a behavior, not an opinion. The phase is done when the gate is green and the owner has approved.
- Category: honesty level per AGENT.md (1 authored, 2 known-method-integrated, 3 seam-only).

The gates are built on synthetic ground truth. The source surface is known exactly, so every gate has a known-correct answer. That is the basis of validation-first here.

---

## Phase 0 — The harness

Goal: prove the measurement reads zero when nothing changed. De-risk the metric before trusting any fit.

Deliverables:
- Repo skeleton per AGENT.md architecture.
- README.md, instrument-not-solver stated in the first line.
- `geometry.py`: one synthetic source surface (see spec-01), UV-grid tessellation.
- `metrics.py`: Hausdorff (max, mean, median) via KD-tree.
- Identity path: tessellate the source, run it straight into the metric against itself.

Gate: identity reconstruction (no fit, no deform) returns drift = 0 within floating-point tolerance. A surface compared to itself has zero drift. If it does not, the metric is wrong and nothing downstream can be trusted.

Category: 1.

---

## Phase 1 — The hardest principle first

Goal: the technical spike. A real classical reconstruction with measured drift. This is the thing that either works or does not, so it goes first.

Deliverables:
- `reconstruct.py`: `Reconstructor` protocol; `ClassicalReconstructor` (least-squares NURBS fit on fixed knot vectors and fixed parameterization).
- `encode.py`: mesh and descriptor to exchange format and back (see spec-02).
- Exchange round trip with an identity solver (deform = none): source -> encode -> out -> identity solver -> in -> decode -> reconstruct -> re-tessellate -> drift.

Gates (two, both must pass):
- Exchange identity: with the identity solver, the round trip drift equals the fit-only drift. The boundary crossing adds nothing. Encode then decode is lossless.
- Honest fit: an honest least-squares reconstruction of the unperturbed surface produces a small, explainable nonzero drift, and the owner can state why it is nonzero (control point count, knot placement) rather than zero.

Category: 2 for the reconstructor, 1 for the exchange.

Note: the fixed-parameterization assumption is the named frontier. State it in `reconstruct.py` as the parameterization-gap boundary.

---

## Phase 2 — The loss budget

Goal: make "what the translation may not lose" executable. This is Move 2 of the talk, in code.

Deliverables:
- `spec-03-metrics-constraints.md`, written at the start of this phase.
- `constraints.py`: the headline constraint checker plus its residual. Headline is spanwise curvature (G2) continuity for the wing case, with ruled-stays-ruled as the clean binary unit underneath it.
- The descriptor in the exchange format carries the loss-budget declaration: which constraints must hold, with tolerances. The constraints are declared in the protocol, not just checked at the end.

Gates (two):
- Ruled binary: a ruled source reads ruled (residual at or near zero). Deform it off-ruled and the residual rises monotonically with the deformation. The checker detects what it claims to detect.
- Budget carried: the descriptor written to `exchange/out` contains the declared constraints, and the checker reads them from the descriptor rather than from a hardcoded value.

Category: 1.

---

## Phase 3 — The loop

Goal: the synthetic solver does real deformation across the exchange. The round trip becomes the workflow shape the team actually runs.

Deliverables:
- `solver_stub.py`: reads `exchange/out`, applies a synthetic, deterministic deformation field (a smooth, pressure-like normal displacement), writes `exchange/in`. No CFD. Known input means known output.
- Drift and constraint residual tracked against deformation magnitude.

Gate: drift scales with the deformation applied. A larger synthetic deformation produces a larger, monotonic drift, and a deformation that violates the declared budget is caught by the constraint checker. The instrument responds correctly to a known stimulus.

Category: 1.

---

## Phase 4 — Identity, topology, and the validity gate

Goal: carry identity and topology across the boundary and use them to detect a returned mesh that is not physical before any reconstruction runs. This is the headline phase, the part the technical screen probed. It sits between decode and reconstruct in the loop. Built.

Deliverables:
- `spec-04-identity-validity.md`, written at the start of this phase.
- The exchange extended (`encode.py`, schema, descriptor) to carry a stable node ID per vertex, the edge adjacency keyed by node ID, and the per-face winding. The synthetic solver returns them unchanged.
- `validity.py`: three checks, one per signal. Identity integrity flags a missing or rewired node ID as `identity_not_preserved`. Collision flags non-neighbor vertices within a tolerance, read against the carried adjacency. Fold flags a face whose Newell normal inverts against its edge-neighbors. A `validity_gate` runs identity first and stops on failure, then runs collision and fold.
- Synthetic solver modes for testing: clean morph, collision morph, fold morph, each deterministic.
- The loop runs the validity gate after decode and before reconstruction. Reconstruction and drift run only on a valid return.

Gate: clean passes and reconstructs. Collision is caught naming the non-neighbor pair, and a near miss that stays clear of the tolerance passes. Fold is caught naming the inverted face. Altered connectivity is flagged as identity-not-preserved, a distinct signal from collision. Each mode is deterministic through the file round trip. A validity result carries no Hausdorff number and no constraint residual.

Category: 1, with the optional triangle-triangle narrow-phase refinement of the collision check as category 2, named and not built.

---

## Phase 5 — The artifact

Goal: the visual the demo shows, and the fallback that survives the demo dying.

Deliverables:
- `report.py`: drift summary, constraint residuals, per-vertex deviation field.
- Deviation field written to a file (mesh plus per-vertex scalar) that Grasshopper reads and colors. A few native GH nodes, no plugin, no compile.
- A static report render that doubles as the slides-only fallback. Same numbers, no live dependency.

Gate: the GH display colors the deviation field on the geometry from the file, and the static fallback render shows the same drift result without running anything live. Both paths produce the same numbers.

Category: 1.

---

## Phase 6 — The reverse-problem boundary (exploratory)

Goal: test the claim that carrying identity is what makes reconstruction well-posed, instead of asserting it. This phase is an experiment, not a feature. It removes carried information rung by rung and measures where reconstruction stops being well-posed. A "this does not converge" or "this is ill-posed" result is a valid finding, not a defect.

This phase is now the priority over the C# plugin, which moves to Phase 7. The experiment tests whether the project is telling the truth about the reverse problem; the plugin is packaging that cannot fail in an interesting way.

Deliverables:
- `spec-06-boundary.md`, written at the start of this phase, framing it as exploratory.
- `boundary.py`: the four-rung ladder. Each rung removes carried information and reconstructs (or reports a structured non-convergence), sharing the same wing ground truth and the same drift, curvature, and constraint meters. Rung 1 full carry (current behavior); rung 2 estimated UV (chord-length on the carried grid); rung 3 no correspondence (shuffled point cloud, PCA-plane parameterization); rung 4 changed topology (remesh, caught by the validity gate). The meters are reused unchanged; only the parameterization and correspondence are stripped.
- `scripts/run_phase6.py`: runs the full ladder, prints the summary table, writes the table and the optional drift-versus-rung plot to `artifacts/`.

Gate: the ladder runs end to end and produces a comparable result or an honest structured non-convergence for every rung, with nothing crashing on the ill-posed or blocked rungs. Rung 1 reproduces the Phase 1 honest-fit drift on the same grid and reconstructor, asserted to floating-point tolerance. Rung 2 drift exceeds rung 1 drift. Rungs 3 and 4 are asserted only to be flagged or reported (ill-posed, blocked), not held to a specific number. Each rung is reproducible given a fixed seed for the shuffle.

Category: 1 for the measurement (it reuses the category 1 meters and the category 2 classical reconstructor unchanged). Exploratory in intent: a negative result is the intended kind of result. The learned reconstructor stays category 3 and unbuilt.

---

## Phase 7 — C# Grasshopper plugin (earned stretch)

Goal: a native GH component that renders the deviation field, demonstrating C# on their stack. An optional stretch, lower priority than the Phase 6 experiment. Only after the core phases are green and time remains.

Deliverables:
- A GHA component that reads the same report file and renders the colored deviation mesh natively.

Gate: the component loads in Grasshopper and renders the field matching the file-fed baseline. No new math lives in the component; it reads the same tested output. The Python engine remains the source of correctness.

Category: 1.

Note: this is packaging, not the spike. It cannot fail in an interesting way, so it earns its place only after the core is proven and the Phase 6 experiment is done.

---

## Phase 8 — Live service (narration only, probably never)

If a running service is ever wanted, it is a deployment wrapper around the existing file contract, not a rebuild. Out of scope for the build. Hold it as a sentence in the talk about deployment options, not as code.

---

## Not built, documented: LearnedReconstructor

A `LearnedReconstructor` stub lives in `reconstruct.py` with the same `Reconstructor` interface and a docstring stating what it would do: predict parametric surface coefficients from mesh input, trained to minimize round-trip drift under the declared constraints, differentiable so it yields per-parameter sensitivities. Not implemented. Category 3. This is the seam that makes the project evolution-ready and gives the Move 3 hinge a basis in real architecture.

---

## Scope guard

- The build targets round-trip drift across the exchange. That is the whole target.
- Three boundaries hold throughout: synthetic solver, file exchange, classical bounded reconstructor.
- The timeline is provisional until the round is confirmed by the recruiter. Thesis and charter work is safe regardless. Heavy demo build (Phases 3 to 5) is best held until confirmation and a date.
