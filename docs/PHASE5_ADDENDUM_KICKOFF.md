# Phase 5 addendum kickoff for Claude Code: the two-panel render

Paste into Claude Code web after Phase 5 is merged. A small addition to the visual phase: a second deviation panel that turns the render into the thesis centerpiece. No new measurement, no new phase. Run on its own branch.

---

Read `AGENT.md`, then `specs/spec-05-report-visual.md`, then `src/passthrough/report.py` and `src/passthrough/constraints.py`. Hold the charter and the voice rules. Stay an instrument; math stays in tested Python.

Phase 5 is done (81 tests). This addendum extends the render only. Update `specs/spec-05-report-visual.md` to describe the two-panel render, then build. Stop at the gate.

## Why

The Phase 5 render colors positional deviation. Positional drift is tiny (around 1.9e-3), so that panel looks calm and reassuring. That is the point being made, but only half of it. The other half is curvature deviation, which is large and concentrated at the leading edge. The thesis is that the positional picture looks fine while the curvature picture does not, for the same reconstruction. One panel hides that. Two panels side by side are the argument.

## What to build

A two-panel render: positional deviation on the left, curvature deviation on the right, same reconstruction, same geometry, shared or clearly-labeled separate color scales.

- The curvature deviation field is already computed in `constraints.py` (the curvature-preservation check compares the reconstruction's curvature field to the source's). Reuse that quantity per vertex or per sample point. Do not add a new measurement; surface the one that exists, the same way `deviation_field` surfaced the positional one.
- Color the right panel by curvature deviation. The leading-edge concentration should be visible. Use a separate color scale from the positional panel, since the magnitudes differ by orders of magnitude; label each panel's scale so the contrast is honest and not an artifact of a shared scale.
- Keep the existing single-panel positional render working (other callers and the fallback use it). Add the two-panel render as a new function alongside it, for example `render_comparison`, rather than replacing `render`.
- The flagged-pass behavior carries over: on a collision or fold, there is no reconstruction and therefore no deviation fields, so the comparison render shows the gray-with-flag state, not two empty panels.

## Gate (with tests)

- Two-panel spec assembles: given a clean loop pass, the comparison render receives both a positional deviation field and a curvature deviation field, each with consistent length, and writes a PNG.
- The two fields are distinct quantities: assert the curvature-deviation array is sourced from the curvature check, not a copy of the positional field. Their per-vertex values should differ.
- Separate scales: assert each panel carries its own scale metadata (min/max), so the render does not flatten one panel onto the other's range.
- Single-panel render unchanged: the existing `render` and its tests still pass untouched.
- Flagged pass: a collision pass produces the gray-with-flag comparison output, no panels of data.

Add the two-panel output to `scripts/run_phase5.py` so the runner writes the comparison PNG for the clean pass alongside the existing artifacts.

Run `python -m pytest`. Report the count (81 plus the new tests, prior unchanged). Run the runner. Then stop.

Place this kickoff in `docs/`.

## Note for Nathan (not the agent)

The comparison PNG is very likely your centerpiece slide: two maps of the same reconstruction, the left one calm, the right one lit at the leading edge. Open it on your phone when it lands and check whether the contrast reads at a glance. If it does, the hardest slide in the deck is already made.
