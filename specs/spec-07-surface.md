# spec-07-surface.md — The reconstructed surface, carried back

Scope: the analytic surface the run reconstructs, written in a portable form the
Grasshopper bench rebuilds as a Rhino `NurbsSurface`. This is what completes the
round trip on the canvas. The original surface leaves as a tagged mesh, the loop
morphs it and reconstructs it, and the reconstruction returns as a real surface that
sits beside the original so the drift between them can be probed.

Written when Phase 7b's follow-up starts, so the schema is fixed here and not
improvised in the writer or the C# reader.

## Where this sits against the charter

The reconstruction already happens in Python, in `ClassicalReconstructor`
(reconstruct.py, honesty category 2: a known least-squares NURBS fit, integrated and
validated). This spec does not add a reconstructor or any geometry math. It defines
how the reconstructor's existing output is carried across the file contract so the
bench can display it. The fit stays in Python; Grasshopper is the evaluation bench,
not the reconstructor (AGENT.md: math stays in tested Python, the bench does not
recompute). The C# side does one thing with this block: rebuild the surface verbatim
from the carried data. No fitting, no knot solving, no geometry beyond construction.

## Where the block lives

The `surface` block is part of the result file (`result.json`), written by the
existing report writer (report.py `report_to_dict` / `write_report`). The status
marker (`status.json`) names the file that holds it under its `surface` key, the same
way it names the field file under `field`, so the import side resolves the surface
against the return folder without a separate convention.

The block is present only on a reconstructed (clean) pass. A flagged pass ran no
reconstruction, so `surface` is `null` in the result and the status marker's `surface`
key is `null`. Absent reconstruction is absent, not an empty surface.

## Schema: `passthrough.surface.v1`

A single JSON object. The `format` string carries the version so a reader can refuse
an unknown one.

```json
{
  "format": "passthrough.surface.v1",
  "degree_u": 3,
  "degree_v": 3,
  "count_u": 7,
  "count_v": 4,
  "control_points": [ [ [x, y, z], ... ], ... ],
  "weights": [ [w, ...], ... ],
  "knots_u": [ ... ],
  "knots_v": [ ... ]
}
```

Fields:

- `degree_u`, `degree_v`: the spline degrees. The classical reconstructor fits cubic
  by cubic (3, 3).
- `count_u`, `count_v`: the control-net dimensions. `count_u` is the number of rows,
  `count_v` the number of columns.
- `control_points`: the control net as a `(count_u, count_v, 3)` nested array.
  Euclidean `[x, y, z]`, not weighted homogeneous coordinates. Row-major: the outer
  index is U, the inner is V, matching the reconstructor's `Points[i, j]` layout.
- `weights`: the control-net weights as a `(count_u, count_v)` nested array. The v1
  surface is non-rational, so every weight is `1.0`. The grid is carried explicitly so
  a later rational fit rides the same contract without a version bump.
- `knots_u`, `knots_v`: the full clamped knot vectors. Each is a flat array of length
  `count + degree + 1` per direction (the standard "full" convention, the one
  `clamped_uniform_knots` produces). A reader can validate the lengths without knowing
  any geometry library.

## The knot convention, stated once

This block writes the full knot vector. rhino3dm and RhinoCommon both store their knot
list with the outermost knot at each end dropped (length `count + degree - 1`). The
writer restores those two end knots when extracting; a rebuilder drops them again when
constructing. Both sides of the contract carry the full vector, so the length check is
the same on both sides:

    len(knots_u) == count_u + degree_u + 1
    len(knots_v) == count_v + degree_v + 1

## Internal consistency (the gate)

A reader checks the block stands on its own before trusting it:

- `control_points` has shape `(count_u, count_v, 3)`.
- `weights` has shape `(count_u, count_v)`.
- `len(knots_u) == count_u + degree_u + 1` and
  `len(knots_v) == count_v + degree_v + 1`.

These hold by construction for any surface the reconstructor produces, and are
asserted in the Python tests on a clean round trip.

## Rebuild

The block is enough to rebuild the surface verbatim, with no fitting:

- Python: `reconstruct.nurbs_surface_from_dict` (the inverse of
  `nurbs_surface_to_dict`), used by the tests to prove the carried data reconstructs
  the same surface the run fit.
- C#: the Import component builds a `Rhino.Geometry.NurbsSurface` from the same fields
  (control points, weights, degrees, knots), dropping the outer end knots to match
  RhinoCommon's knot list. This is the bench's only geometry step, and it is
  construction, not computation.

## The evaluation alongside it

The result also carries an `evaluation` block at its top level, so the bench shows the
numbers without recomputing or parsing the full fields:

```json
"evaluation": {
  "drift": { "max": ..., "mean": ..., "median": ... },
  "curvature_max": ...
}
```

These are not new measurements. `drift` is the positional summary driftgauge already
produced; `curvature_max` is the maximum of the curvature deviation field the
constraint check already produced. The status marker echoes the two headline numbers
(`drift_max`, `curvature_max`) so the import side can display them straight from the
marker. Both are `null` on a flagged pass.
