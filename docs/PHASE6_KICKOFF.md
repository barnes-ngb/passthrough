# Phase 6 kickoff for Claude Code: the reverse-problem boundary (speculative)

Paste into Claude Code web after the Phase 5 addendum is merged. This phase is different in character from the others. It is an experiment, not a feature. The goal is to test a claim, not assert it: how well is mesh-to-CAD reconstruction actually solved, and what is carrying identity worth?

Run on its own branch. Write `specs/spec-06-boundary.md` first, framing this as exploratory. Stop at the gate.

Note on phase numbering: this becomes Phase 6. The C# Grasshopper plugin moves to a later optional stretch (Phase 7). Update PLAN.md to reflect that, since this experiment is now the priority over the plugin.

---

Read `AGENT.md`, `PLAN.md`, then `reconstruct.py`, `encode.py`, `constraints.py`, `driftgauge.py`, and `report.py`. Hold the charter and voice rules. This is honesty category 3 work: exploratory, and a result of "this does not converge" or "this is ill-posed" is a valid, informative finding, not a bug to fix. Do not force a rung to look good. Report what actually happens.

## The claim under test

The existing reconstructor carries the UV parameterization across the boundary, which makes reconstruction a well-conditioned least-squares fit with known correspondence. That assumes away the hard part of the real reverse problem. This phase removes carried information rung by rung and measures what happens, to find the boundary where reconstruction stops being well-posed.

## The ladder (most to least carried information)

Build each rung as a distinct reconstruction path or input transformation, sharing the same wing ground truth and the same drift and constraint measurement so the rungs are comparable.

1. **Full carry (baseline).** Carried UV plus identity plus topology. This is the current ClassicalReconstructor. Expected: tight, representation-gap drift only. This rung should reproduce Phase 1's known drift; assert that.

2. **Estimated UV.** Discard the carried UV. Estimate a parameterization from the mesh itself, keeping identity and topology. Start with the simplest defensible method and document its assumptions: for the structured wing tessellation, a normalized grid parameterization; or a closest-point projection of the vertices onto the base surface to recover (u,v). Reconstruct with the estimated UV. Expected: drift increases. This is the parameterization gap made measurable.

3. **No correspondence (point cloud).** Strip the identity ordering: shuffle the vertices and drop the topology, so the input is an unordered point set. Attempt reconstruction. This requires guessing both correspondence and parameterization. Expected: ill-posed or much larger drift. If it does not converge or requires an arbitrary assumption, report that honestly and state the assumption. Do not manufacture a good result.

4. **Changed topology (remesh).** Feed a returned mesh whose connectivity differs from what was sent (reuse the remesh case from Phase 4). The validity gate already flags this as identity-not-preserved. Show that reconstruction cannot use carried UV here, and either blocks cleanly or degrades sharply if forced. Expected: the gate stops it; if bypassed, large drift.

## What to measure and produce

For each rung that produces a reconstruction: positional drift (max, mean), curvature residual, and constraint preservation, using the existing meters. For rungs that do not converge: a clear, structured "did not converge / ill-posed" result with the reason.

Produce a summary across the ladder: a table of rung, information carried, positional drift, curvature residual, and status. If matplotlib is already wired (Phase 5), add an optional simple plot of drift versus rung, so the climb is visible. The table is the deliverable; the plot is a bonus.

The honest headline this is meant to reveal: drift climbs as carried information is removed, the curvature residual climbs faster, and the gap between rung 1 and rungs 2 to 4 quantifies what carrying identity is worth. That difference is the argument that passthrough makes the problem well-posed rather than solving it.

## Gate (with tests)

- The ladder runs end to end and produces a comparable result or an honest non-convergence report for every rung.
- Baseline (rung 1) reproduces the known Phase 1 drift; assert it.
- Monotonic-ish degradation: assert that rung 2 drift exceeds rung 1 drift. Do not over-assert rungs 3 and 4 if they are ill-posed; assert only that they are flagged or reported, not a specific number.
- Determinism: each rung is reproducible given a fixed seed for any shuffling.
- Nothing crashes on the ill-posed rungs; they return a structured result.

Add `scripts/run_phase6.py` that runs the full ladder, prints the summary table, and writes it (and the optional plot) to `artifacts/`.

Run `python -m pytest`. Report the new count (prior plus the Phase 6 tests, prior unchanged) and the summary table. Then stop.

Place this kickoff in `docs/`.

## Note for Nathan (not the agent)

This is the experiment that tests whether we are telling the truth. The likely finding is that mesh into system is not solved without carried data, which is exactly the speculative, honest result you want. The drift-versus-information table is the new centerpiece: it shows where reconstruction is tractable and where it is still open, instead of claiming it works.

---

## Outcome (what the experiment actually found)

The ladder ran end to end. The positional drift climbs as carried information is removed, which is the headline the experiment was built to test:

```
rung carried information                                drift max  drift mean   curv max  status
------------------------------------------------------------------------------------------------
1    carried UV + identity + topology                   1.926e-03   7.215e-04  3.160e+00  reconstructed
2    identity + topology; UV estimated (chord-len...    1.882e-02   5.954e-03  2.985e+00  reconstructed
3    unordered point cloud (no UV, no identity, n...    2.583e+00   4.867e-02  8.615e+00  ill_posed
4    remesh: connectivity differs from what was sent            -           -          -  blocked
```

- Rung 1 reproduces the Phase 1 honest-fit drift exactly (asserted to floating-point tolerance).
- Rung 2 drift is about 10x rung 1: the parameterization gap, made measurable.
- Rung 3 drift is order the part's own size (the span is about 3.74 mm), so the reconstruction does not represent the surface. Reported ill-posed with the PCA-plane assumption stated.
- Rung 4 is stopped by the validity gate before any fit. Forcing it confirms the carried UV cannot be applied across the cardinality change.

One place the experiment refused to flatter the hypothesis: the curvature residual does not climb faster than position here. It is already saturated at rung 1 by the coarse 7x4 basis, which cannot hold the leading-edge curvature regardless of the parameterization. The positional ladder is the clean signal on this fixture; the curvature ladder is dominated by the representation gap the basis already carries. That is reported as the finding, not adjusted away. See `specs/spec-06-boundary.md`.
