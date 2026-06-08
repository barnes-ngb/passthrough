# spec-06-boundary.md — The reverse-problem boundary (exploratory)

Scope: test a claim rather than assert one. The instrument carries the UV
parameterization, the per-vertex identity, and the topology across the boundary.
That makes reconstruction a well-conditioned least-squares fit with known
correspondence. This phase removes the carried information rung by rung and
measures what happens, to locate the boundary where reconstruction stops being
well-posed.

Written at the start of the phase, before the code, so the ladder's shape and the
honest framing are fixed here and not improvised in the implementation.

This phase is an experiment, not a feature. A result of "this does not converge"
or "this is ill-posed" is a valid, informative finding, not a defect to fix. The
code does not force a rung to look good. It reports what actually happens.

## Where this sits against the charter

AGENT.md names three frontiers and insists the instrument stay an instrument, not
drift into a solver. This phase walks straight up to the parameterization-gap
frontier and the reverse-problem-proper frontier and measures them. It does not
cross them. The methods that estimate a parameterization or guess a correspondence
are deliberately simple and deliberately named as assumptions. The point is to
show how far reconstruction degrades once carried information is gone, which is the
measurement of what carrying it is worth. Measuring the gap is instrument work.
Closing it is the solver work this project does not claim.

Honesty framing: the kickoff calls this honesty-category-3 work in the sense that
a negative result is the intended kind of result. The code itself is authored
instrument logic. It reuses the category 1 meters (driftgauge, constraints) and
the category 2 classical reconstructor unchanged. It does not implement the
learned reconstructor, which remains category 3 and unbuilt (AGENT.md hard
do-not list).

## The claim under test

Carrying the UV across the boundary assumes away the hard part of the real reverse
problem. With carried UV the fit has known correspondence and a fixed
parameterization, so it is a linear least-squares solve. Strip the carried
information and the problem becomes the one a real mesh-to-CAD pipeline faces:
recover the parameterization, recover the correspondence, or fail. The claim is
that the gap between the full-carry rung and the stripped rungs quantifies what
passthrough's carried identity is worth, and that the stripped rungs show the
reverse problem is not solved here, only made well-posed by the carried data.

## The ladder

Four rungs, most to least carried information. Each shares the same wing ground
truth (the spec-01 surface, tessellated on the same grid) and the same drift,
curvature, and constraint meters, so the rungs are comparable. No solver
deformation is applied. The experiment isolates the effect of removing carried
information, not the effect of a deformation, so every rung reconstructs the
undeformed wing and is measured against the same source tessellation.

### Rung 1 — Full carry (baseline)

Carried UV, carried identity, carried topology. This is the current
ClassicalReconstructor used exactly as the loop uses it: reconstruct from the
source points and the carried per-vertex UV. Expected: tight, representation-gap
drift only. This rung reproduces the Phase 1 honest-fit drift on the same grid and
reconstructor (max near 1.93e-3 mm on the 40x16 grid with the 7x4 cubic net), and
the gate asserts that equality.

### Rung 2 — Estimated UV

Discard the carried UV. Keep identity and topology, so the structured grid ordering
is still known. Estimate a parameterization from the mesh geometry by chord-length
along the grid lines: accumulate edge lengths down each grid column to get the u
parameters, across each grid row to get the v parameters, normalize each to [0, 1],
and average over the other index. This is the simplest defensible estimate that
respects the carried grid structure.

Assumption stated: the grid topology is known (rung 2 keeps it) and the rulings of
the parameterization run along the grid lines. The estimate differs from the
carried uniform UV because the wing is not uniformly spaced in arc length. Its
section clusters samples near the leading edge, so chord-length parameters bunch
where the uniform carried parameters did not. The fixed clamped-uniform knot
vectors were placed for the uniform parameterization, so the estimated parameters
no longer line up with the knots and the fit degrades. Expected: drift increases.
This is the parameterization gap made measurable.

### Rung 3 — No correspondence (point cloud)

Strip the identity ordering. Shuffle the vertices under a fixed seed and drop the
topology, so the input is an unordered point set. Reconstruction now has to guess
both the correspondence and the parameterization. The defensible simplest attempt:
project the points onto their best-fit plane (PCA), assign the two in-plane
principal directions to v and u by spatial extent (the wing is longer in span than
in chord), fix the principal-axis signs by a deterministic convention (the largest-
magnitude loading is made positive, the standard SVD sign flip), and normalize each
in-plane coordinate to [0, 1] as the parameterization.

Every step of that is an arbitrary assumption, and the spec says so plainly. There
is no correspondence to recover the true parameterization from, so a plane and an
axis assignment are imposed. Expected: ill-posed. The reconstruction departs from
the surface by a distance on the order of the part's own size, which is the honest
reading that the assumption does not hold. The numbers are reported as evidence of
the ill-posedness, not as a result to defend. The recovery uses no vertex order, so
it is invariant to the shuffle. That invariance is itself the honest statement that
no correspondence was used.

### Rung 4 — Changed topology (remesh)

Feed a returned mesh whose connectivity differs from what was sent. The wing is
re-tessellated on a different grid, the realistic form of the remesh that Phase 4
flagged as identity-not-preserved (Phase 4 simulated it by dropping a face; a
resolution change is the same signal and also breaks the vertex cardinality). The
validity gate runs first and stops on identity-not-preserved, so reconstruction
never runs. That is the primary result: the gate blocks it cleanly.

The deeper reason is shown by forcing it: the carried UV has one entry per sent
vertex, the remesh has a different vertex count, so the carried correspondence does
not exist for the remeshed vertices and the reconstructor refuses the call. The
carried UV is structurally unusable across a topology change, not merely inaccurate.
Expected: the gate stops it; if bypassed, it cannot proceed at all.

## What is measured

For each rung that produces a reconstruction:

- Positional drift (max, mean), via driftgauge.hausdorff against the source
  tessellation. This is the same comparison Phase 1 used for fit-only drift, so
  rung 1 ties back to the Phase 1 number exactly.
- Curvature residual (max, mean, leading-edge max), via constraints.curvature_residual
  against the source surface.
- Constraint preservation, via constraints.check_constraints against a declared
  curvature budget, reported as the residual beside the declared tolerance and a
  pass/fail flag.

For each rung that does not produce a usable reconstruction: a structured result
carrying the status (ill-posed or blocked) and the reason, with no invented drift
number. Absent measurements are absent, not zero, the same discipline the report
layer holds for a flagged pass.

## The result type

One RungResult per rung:

- rung: the rung number, 1 to 4.
- name: the short rung name.
- carried: what information this rung carries, for the table.
- status: "reconstructed" (a usable fit), "ill_posed" (a fit was produced but rests
  on an arbitrary assumption and does not represent the surface), or "blocked" (no
  fit ran; the gate stopped it).
- drift: the max/mean summary, or None when no fit ran.
- curvature: the curvature residual summary, or None.
- constraints: the constraint results, empty when no fit ran.
- reason: a plain-language statement of what happened, carried on every rung and
  required on the ill-posed and blocked rungs.
- detail: secondary numbers (the assumption parameters, the gate signal, counts).

## The summary

A table across the ladder: rung, information carried, positional drift, curvature
residual, status. The table is the deliverable. If matplotlib is available (it is,
from Phase 5), an optional plot of drift versus rung is a bonus so the climb is
visible. The table and the plot read the same RungResult list and cannot diverge.

## The honest headline this is meant to reveal

Positional drift climbs as carried information is removed: tight at rung 1, an order
of magnitude worse at rung 2, and order the part's own size at rung 3. The gap
between rung 1 and the rest is what carrying identity is worth, and it is the
argument that passthrough makes the problem well-posed rather than solving it.

The curvature residual is the place the experiment refuses to flatter the
hypothesis. The kickoff predicted curvature would climb faster than position. On
this fixture it does not climb from rung 1 to rung 2, because the curvature residual
is already saturated at rung 1 by the coarse 7x4 basis, which cannot represent the
leading-edge curvature regardless of the parameterization. That is reported as the
finding, not adjusted away. The positional ladder is the clean signal here; the
curvature ladder is dominated by the representation gap the basis already carries.

## Gate

- The ladder runs end to end and produces a comparable result or an honest
  structured non-convergence for every rung. Nothing crashes on the ill-posed or
  blocked rungs.
- Baseline: rung 1 reproduces the Phase 1 honest-fit drift on the same grid and
  reconstructor, asserted to floating-point tolerance.
- Degradation: rung 2 drift exceeds rung 1 drift. Rungs 3 and 4 are asserted only
  to be flagged or reported (ill-posed, blocked), not held to a specific number.
- Determinism: each rung is reproducible given a fixed seed for the shuffle.

scripts/run_phase6.py runs the full ladder, prints the summary table, and writes
the table (and the optional plot) to artifacts/.
