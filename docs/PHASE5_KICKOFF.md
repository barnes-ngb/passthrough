# Phase 5 kickoff for Claude Code: the visual

Paste into Claude Code web after the cleanup pass is merged. This is the visual phase (Phase 5 in the corrected PLAN.md). It makes the numbers visible and produces the demo's fallback.

---

Read `AGENT.md` and `PLAN.md` first, then skim the package modules, especially `driftgauge.py` (the drift meter), `constraints.py`, and `validity.py`. Hold the charter, the voice rules, and the hard do-not list. Stay an instrument. Math stays in tested Python; do not move computation into a display layer.

Phases 0-4 are done. Build Phase 5 only. Write `specs/spec-05-report-visual.md` first, then build. Stop at the Phase 5 gate. Do not start the C# plugin (Phase 6).

There is no `report.py` in the package yet. This phase creates it.

### What this phase produces

1. `src/passthrough/report.py` (honesty category 1). A reporting layer that gathers, for one loop pass: the drift summary (max, mean, median from the drift meter), the constraint residuals (from `constraints.py`), the validity result (from `validity.py`, including any flagged collision pairs or folded faces), and a per-vertex deviation field (the nearest-distance from each reconstructed vertex to the original, the same quantity the drift meter aggregates, kept per-vertex here for display). It assembles these into one report object and can serialize it.

2. A field file for Grasshopper. Emit the deviation field as a file Grasshopper can read and color: the mesh (vertices, faces) plus a per-vertex scalar (the deviation), plus the flagged element lists (collision node pairs, folded faces) so the live display can highlight them. JSON is fine, consistent with the exchange format. This is the file the live Rhino/Grasshopper demo reads; the coloring happens in Grasshopper, which is not built here. Just emit the file in a clean, documented shape.

3. A static render (headless). This is the important deliverable for review and as the demo fallback. Using matplotlib with the Agg backend (headless, no display), render the deviation field as color over the geometry: either a 3D view of the surface colored by deviation, or a 2D UV-space heatmap of the deviation grid, or both. Overlay the flagged regions: mark the collision node pair and the folded faces distinctly. Save as PNG. This render must show the same numbers as the report object and the field file. It doubles as the slides-only fallback if the live demo fails, and it is the artifact a reviewer on a phone can actually look at.

### Why the static render matters here

The live coloring happens in Grasshopper, which needs Rhino and a desktop. The static render is the headless, reviewable, portable version of the same picture. Build it so it stands alone: given a completed loop pass, it produces a publishable image of the deviation field with the flagged regions marked, no Rhino required.

### Dependencies

matplotlib is needed for the render. Add it to `requirements.txt` (and to an optional viz extra in `pyproject.toml` if you want to keep the core install lean). Install with `pip install matplotlib --break-system-packages` if the environment needs it. Use the Agg backend explicitly (`matplotlib.use("Agg")`) so nothing tries to open a window. No other new dependency.

### Gate (with tests)

- Report assembles: a completed loop pass produces a report object carrying drift, constraint residuals, validity result, and the per-vertex deviation field, with array lengths consistent (one deviation per vertex).
- Field file round-trips: the emitted field file can be read back and its mesh and scalar match the report.
- Static render is produced: running the render on a loop pass writes a PNG, and on a flagged pass the flagged regions are present in the rendered output (assert the overlay data is passed to the render, since you cannot assert pixels easily). Keep the render function pure enough that its inputs can be tested even if the image itself is checked only for existence and basic validity.
- The clean-vs-flagged distinction carries through: a clean pass renders the deviation field with no flags; a collision pass renders with the collision pair marked; a fold pass with the folded faces marked.

Add a Phase 5 runner script (`scripts/run_phase5.py`) that runs a clean pass and a collision pass, writes both the field files and the PNGs, and prints where it wrote them, matching the earlier runners' style.

Run `python -m pytest`. Report the new test count (should be the prior count plus the Phase 5 tests, with all prior tests unchanged). Run the scripts. Then stop at the gate.

Place this kickoff in `docs/` with the others.

### Note for Nathan (not for the agent)

The PNG from the static render is the thing you can open on your phone to see the deviation field colored on the wing with the collision flag marked. That is the demo made visible without Rhino, and it is the slides-only fallback at the same time. When this lands, you will have an image to react to and to build the demo slide around.
