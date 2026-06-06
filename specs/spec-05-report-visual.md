# spec-05-report-visual.md — The report, the field file, and the static render

Scope: make the numbers visible. This phase gathers what the earlier phases
already compute into one report object for a single loop pass, emits a field file
the live Grasshopper display reads and colors, and produces a static headless
render that shows the same numbers without Rhino.

Written at the start of the phase, before the code, so the report's shape and the
field file's contract are fixed here and not improvised in the implementation.

## Principle

The instrument already measures everything. driftgauge produces the drift summary
and the per-vertex deviation; constraints produces the residuals; validity produces
the gate result with its flagged node pairs and faces. This phase adds no
measurement. It is a reporting layer: it assembles results that already exist into
one object, serializes that object, and draws it.

The boundary that must not move (AGENT.md): math stays in tested Python. The
report computes nothing the earlier modules do not already compute. The deviation
field reuses `driftgauge.nearest_distances`. The Grasshopper coloring is not built
here; this phase emits the file Grasshopper reads. The static render reads the same
report and draws the same numbers, so the two display paths cannot diverge.

## What a loop pass yields

A single loop pass is one trip through the pipeline: source tessellated, sent out,
the solver returns a mesh, the validity gate runs, and on a valid return the
reconstruction runs and drift is gauged. The report carries the outcome of that
pass. Two shapes of outcome exist and the report holds both honestly:

- A valid pass. The gate cleared, the reconstruction ran, and there is a drift
  summary, a constraint residual per declared constraint, and a per-vertex
  deviation field on the reconstructed mesh. No regions are flagged.
- A flagged pass. The gate stopped (collision or fold), so no reconstruction ran.
  There is no drift summary, no residual, and no deviation field. There is a
  validity result naming the flagged node pair or the flagged faces, carried on the
  returned mesh so the display can mark them.

The report does not invent a drift number for a flagged pass. Absent measurements
are absent, not zero. This keeps the report honest about what a flagged pass means:
the question of how far the shape moved was never reached.

## The deviation field

The deviation field is the per-vertex form of the quantity the drift meter
aggregates. For each vertex of the displayed mesh it is the nearest distance from
that vertex to the comparison point set, computed with `driftgauge.nearest_distances`.

On a valid pass the displayed mesh is the reconstructed, re-tessellated mesh and
the comparison set is the returned (deformed) mesh: the deviation is how far each
reconstructed point sits from the surface it was asked to match. One scalar per
reconstructed vertex. This is the field Grasshopper colors and the render draws.

The drift summary (max, mean, median) and this deviation field read the same
geometry through the same `nearest_distances` call. The summary's symmetric maximum
is taken over the directed distances in both directions; the deviation field is the
reconstruction-to-target direction of exactly that computation. So the field's
maximum is one of the two directed maxima the summary's maximum is taken over, and
is bounded by it. The render and the summary draw from one source and cannot show
unrelated numbers.

## The report object

`src/passthrough/report.py`, honesty category 1. It is instrument logic, authored
here. It computes no new metric.

A `LoopReport` carries:

- `vertices`, `faces`: the displayed mesh. The reconstructed mesh on a valid pass,
  the returned mesh on a flagged pass.
- `node_ids`: one name per displayed vertex, so flagged node-id pairs map to vertex
  rows for display.
- `deviation`: the per-vertex scalar, one per vertex, or `None` on a flagged pass.
- `drift`: the max/mean/median summary, or `None` on a flagged pass.
- `constraints`: the list of constraint results, empty on a flagged pass.
- `validity`: the gate result, always present.
- `collision_pairs`: the flagged non-neighbor node-id pairs, read from the gate.
- `folded_faces`: the flagged face row indices, read from the gate.
- `provenance`: source identity and iteration index, copied from the descriptor.

The lengths are consistent by construction: when `deviation` is present it has one
entry per vertex. The report asserts this on assembly.

The report serializes to a plain dict and back. The validity gate result, the
constraint results, and the arrays all survive the round trip.

## The field file

The field file is what the live Rhino/Grasshopper display reads and colors. It is
JSON, consistent with the exchange format. It is a display artifact, not part of the
exchange contract: it flows one way, from the instrument to the display, and nothing
reads it back into the loop.

Shape, documented so Grasshopper can consume it without guessing:

```
{
  "format": "passthrough.deviation_field.v1",
  "units": "mm",
  "vertices": [[x, y, z], ...],          # the displayed mesh vertices
  "faces": [[i, j, k, l], ...],          # quad vertex indices into vertices
  "node_ids": [...],                      # one name per vertex, vertex order
  "deviation": [d0, d1, ...] | null,      # one scalar per vertex, or null
  "deviation_range": [min, max] | null,   # for a stable color scale, or null
  "flagged": {
    "collision_pairs": [[a, b], ...],     # node-id pairs that coincided
    "folded_faces": [f, ...]              # face row indices that inverted
  }
}
```

The coloring happens in Grasshopper from `deviation` against `deviation_range`. The
flagged lists let the display highlight the collision pair and the folded faces.
The file round-trips: reading it back yields the same mesh and the same scalar the
report holds.

## The static render

`render` in `report.py`, matplotlib with the Agg backend set explicitly so nothing
opens a window. It is the headless, portable form of the same picture, and the
slides-only fallback if the live demo fails.

The render reads a `LoopReport` and draws the deviation field as color over the
geometry: a 3D view of the mesh vertices colored by deviation with a colorbar. It
overlays the flagged regions: a collision node pair is marked at both coinciding
vertices with a connecting segment, and a folded face is drawn as a highlighted
polygon. The drift summary is written into the title so the image carries its own
numbers. The result is saved as a PNG.

The render is split so its inputs are testable without inspecting pixels. A pure
`build_render_spec(report)` turns the report into a `RenderSpec`: the vertices, the
faces, the deviation array, the collision segments as vertex-coordinate pairs, and
the folded faces as vertex-coordinate polygons. The render function draws that spec.
Tests assert the spec carries the right overlay geometry for each pass; the image
itself is checked only for existence and basic validity (a non-empty PNG).

On a flagged pass `deviation` is `None`. The render draws the returned geometry in a
neutral fill and still marks the flagged regions, so a collision pass shows the
marked pair and a fold pass shows the marked faces.

## Validation requirements

These are the Phase 5 gates, restated as testable definitions.

- Report assembles. A completed loop pass produces a report carrying the drift
  summary, the constraint residuals, the validity result, and the per-vertex
  deviation field, with one deviation per vertex.
- Field file round-trips. The emitted field file reads back with its mesh and scalar
  matching the report.
- Static render is produced. Running the render on a loop pass writes a non-empty
  PNG. On a flagged pass the flagged regions are present in the render spec passed to
  the drawing, asserted directly since pixels cannot be.
- The clean-vs-flagged distinction carries through. A clean pass renders the
  deviation field with no flags. A collision pass renders with the collision pair
  marked. A fold pass renders with the folded faces marked.
- One source of numbers. The deviation field is the reconstruction-to-target
  direction of the same `nearest_distances` computation the drift summary aggregates,
  so the field's maximum is bounded by the summary's symmetric maximum and the render
  and the report cannot show unrelated numbers.

## Named boundaries surfaced here

- The coloring lives in Grasshopper, not here. This phase emits the file; the live
  display reads it. A native C# Grasshopper component that renders the same file is
  the Phase 6 earned stretch, not built here.
- The static render is matplotlib, not Rhino. It is the reviewable, portable form of
  the picture and the demo fallback. It is not a second rendering engine for the
  product; it stands in for the live display when there is no desktop.
- The field file flows one way. It is a display artifact, not a second exchange
  contract. Nothing reads it back into the loop.
</content>
</invoke>
