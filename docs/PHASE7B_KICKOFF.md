# Phase 7b kickoff: the Grasshopper bench (C#)

This is the expensive half of the round-trip demo: a C# Grasshopper plugin that cannot
be validated in the sandbox because it needs Rhino on Windows. The gate is Nathan
building and loading it, not a sandbox test. This phase does not claim the plugin works.
It claims the plugin is ready to build.

The contract this builds against was proven by hand in Phase 7a. It is not redesigned
here. The plugin speaks exactly the schemas 7a produces and reads, confirmed from
`encode.py` (the payload and topology tags), `report.py` (the field file shape), and
`run.py` (the status marker and the field-path resolution rule).

PLAN numbering: this is Phase 7b.

## The demo this completes: three buttons, nothing blocks

A bench in Grasshopper where geometry makes the round trip in three manual steps, each
separately triggered, the canvas never frozen:

1. **Export.** Take a NURBS surface from Rhino, mesh it on a structured UV grid, tag it
   with identity and topology, write the incoming payload JSON.
2. **Trigger the run.** Launch the 7a runner on that payload, non-blocking, writing its
   result, field, and `status.json` to a return folder.
3. **Import.** Read `status.json`; once it says `done`, pull the field and color the
   mesh, laying the deltas and flags on top. If `failed`, show the reason and import
   nothing.

## The contracts (validated in 7a, unchanged)

Confirmed from the source before any DTO was written:

- **Incoming payload** (`encode.encode`): `vertices` (N, 3), quad `faces` (M, 4), `uv`
  (N, 2), `units`, `frame`, a `topology` block (`node_ids`, `adjacency` keyed by node id
  as a string, `winding`), and a `descriptor` block (`constraints`, `preserve`,
  `provenance`). The runner's `validate_incoming` requires the node ids to be unique and
  to cover the vertices, the winding to equal `node_ids[faces]`, and the adjacency to
  equal `_adjacency_from_winding(winding)`. The export writes node ids in vertex order,
  so the winding equals the faces and the adjacency is the quad-edge graph, and both
  equality checks hold by construction.
- **Field file** (`passthrough.deviation_field.v1`): `vertices`, quad `faces`,
  `node_ids`, per-vertex `deviation` (or null), `deviation_range` (or null), and
  `flagged` with `collision_pairs` and `folded_faces`. The collision pairs and folded
  faces are in node-id space; the import maps node id to vertex row to place them.
- **Status marker** (`status.json`): on success
  `{ "status": "done", "flagged": ..., "signals": [...], "result": "result.json",
  "field": "field.json", "drift_max": ... }`; on failure
  `{ "status": "failed", "reason": "..." }`. Paths are relative to the return folder. The
  import resolves `field` against the status file's own folder, not the working
  directory.

## The three components

Built in `plugin/PassthroughGh/`. One plugin assembly, a `GH_AssemblyInfo`, and three
components, each with a stable GUID. Target `net7.0-windows`, reference the `Grasshopper`
NuGet package (which brings `RhinoCommon`), produce a `.gha` via a post-build copy.

1. **Export** (`ExportComponent.cs`). Inputs a surface, U and V grid counts, an output
   folder, and a Write button. Meshes the surface on a structured UV grid so the result
   is a quad grid with known connectivity, assigns a node id per vertex, builds the edge
   adjacency from the grid, records the winding, and serializes the `encode.py` payload
   schema to `payload.json`. Outputs the payload path and the meshed `Mesh`. The
   structured grid is the constraint that makes clean tags possible, noted in the README.

2. **Trigger** (`TriggerComponent.cs`). Inputs the payload path, the return folder, the
   runner invocation, an optional working directory, and a Run button. On the rising
   edge of Run it launches the runner as a separate process through `cmd.exe` and
   returns without waiting. The canvas stays live. It clears any stale `status.json`
   first so Import shows `not ready` until the fresh marker lands. The README carries the
   exact `uv run python scripts\run_roundtrip.py run` form to wire in.

3. **Import** (`ImportComponent.cs`). Inputs the return folder and a Pull button. On the
   rising edge of Pull it reads `status.json`. Absent or not done yields a `not ready`
   or `pending` status and pulls nothing; `failed` surfaces the reason; `done` resolves
   the field against the folder, reads it, builds the mesh, colors vertices by deviation
   through a viridis ramp, and surfaces the collision pairs as points and the folded
   faces as indices. A flagged-but-done pass (deviation empty) colors the mesh neutral
   gray and still surfaces the flags, mirroring the static fallback. The import does no
   computation; it reads what 7a wrote.

## Scope notes (stated, not omitted)

- Positional deviation only. The field carries one value per vertex, which colors
  cleanly. Curvature is sampled differently and stays in the static render.
- The solver inside the run is the synthetic morph from earlier phases. The bench
  demonstrates the round-trip architecture and the data continuity, not a real CFD
  solve.
- Measurement on the return is display and exploration in Grasshopper. The authoritative
  numbers come from the field and status, computed by the tested Python. Drift is not
  recomputed in C#.

## Gate (Nathan validates in Rhino, not the sandbox)

A `.gha` cannot be loaded here, so this phase ends ready-to-build, not proven. The
validation Nathan will run:

1. `dotnet build` succeeds.
2. The `.gha` loads in Grasshopper with no version or reference error.
3. Export a surface to a payload; confirm it validates by running
   `run_roundtrip.py run` on it by hand and seeing `done`.
4. Trigger from the canvas; confirm the run produces the return files without freezing
   Grasshopper.
5. Import; confirm the mesh colors by deviation and matches the static render, and that
   a flagged field shows gray with the pairs marked.

Expect the references and the `.gha` load to need a round or two of adjustment. Capture
the exact errors and iterate; this is the half only the Rhino machine can prove.

---

## What was built (notes for the record)

- `plugin/PassthroughGh/PassthroughGh.csproj`: the project. Targets `net7.0-windows`,
  references `Grasshopper` 8.0.23304.9001 with `ExcludeAssets="runtime"` so Rhino's own
  DLLs stay out of the output, and copies the built `.dll` to `.gha` after the build.
- `plugin/PassthroughGh/PassthroughGhInfo.cs`: the `GH_AssemblyInfo` plugin metadata.
- `plugin/PassthroughGh/ExportComponent.cs`: meshes a surface on a structured UV grid,
  tags it, and writes the `encode.py` payload schema. Adjacency mirrors
  `encode._adjacency_from_winding`.
- `plugin/PassthroughGh/TriggerComponent.cs`: launches the runner as a non-blocking
  process on the rising edge of Run, clearing the stale marker first.
- `plugin/PassthroughGh/ImportComponent.cs`: reads the status marker, resolves and reads
  the field against the return folder, colors the mesh by deviation, and surfaces the
  flags. DTOs match the status and field schemas exactly.
- `plugin/PassthroughGh/Colormap.cs`: an analytic viridis approximation, the same color
  family as the static render.
- `plugin/README.md`: the build, install, and use steps, the three-button flow, the
  Python invocation to wire into the trigger, and the scope notes.

The plugin has not been built or loaded. That gate is Nathan's, in Rhino.
