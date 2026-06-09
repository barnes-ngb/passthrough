# PassthroughGh: the Grasshopper bench

A native Grasshopper plugin that drives the passthrough round trip from the Rhino
canvas. Three components, three buttons, each separately triggered, the canvas never
frozen:

1. **Export** a NURBS surface as a tagged mesh (the incoming payload).
2. **Trigger** the Phase 7a runner on that payload, non-blocking.
3. **Import** the result once the status marker says `done`, and color the mesh by
   deviation.

This is Phase 7b. It is packaging around the Phase 7a contract, not new math. No
measurement lives in C#. The components write the payload schema `encode.py` defines
and read the field file and status marker `run.py` writes. The Python engine stays the
single source of correctness.

## What it cannot do, and who validates it

This plugin needs Rhino 8 on Windows to build and load. It was authored where that is
not available, so it has not been loaded or run. The gate is yours, in Rhino. The
report that ships with it states this plainly: it is ready to build, not proven to work.

## Build

Requires Rhino 8 on Windows and the .NET 7 SDK (the C# Dev Kit toolchain).

```powershell
cd plugin\PassthroughGh
dotnet build -c Release
```

The build emits `PassthroughGh.dll` and a post-build step copies it to
`PassthroughGh.gha` alongside it, under `bin\Release\net7.0-windows\`.

The project references the `Grasshopper` NuGet package, which brings `RhinoCommon` as a
dependency. The package version is pinned to `8.0.23304.9001` (Rhino 8.0 RTM). If
`dotnet restore` cannot find that exact build, bump the version in
`PassthroughGh.csproj` to match the service release of Rhino 8 you have installed.

## Install

No admin rights needed. Copy the `.gha` into the Grasshopper Libraries folder:

```powershell
copy bin\Release\net7.0-windows\PassthroughGh.gha "$env:APPDATA\Grasshopper\Libraries\"
```

Then unblock it (Windows marks files from another machine as blocked, and Grasshopper
will refuse a blocked assembly):

```powershell
Unblock-File "$env:APPDATA\Grasshopper\Libraries\PassthroughGh.gha"
```

Restart Rhino and Grasshopper. The three components appear on the **Passthrough** tab,
**Bench** group.

### Troubleshooting: missing component icons

The components load their toolbar icons from PNGs embedded in the assembly, under the
manifest name `PassthroughGh.Resources.<filename>` (the `EmbeddedResource` item in the
csproj, matching the `RootNamespace`). `IconLoader.Load` returns null when the embedded
resource is not found, so a missing icon falls back to Grasshopper's default rather than
taking the component down. If the icons come up as the default checkerboard, the PNGs
were not embedded. Confirm what actually shipped in the assembly by listing its manifest
resource names. From a Grasshopper C# script component (which already has the assembly
loaded once the plugin is installed):

```csharp
foreach (var name in typeof(PassthroughGh.ExportComponent).Assembly.GetManifestResourceNames())
    Rhino.RhinoApp.WriteLine(name);
```

If the list shows no `PassthroughGh.Resources.passthrough_export_24.png` (and the import
and trigger names), the PNGs were absent from the build's `Resources\` folder at compile
time, so nothing was embedded. The csproj globs `Resources\*.png` relative to the project
directory (`plugin\PassthroughGh\Resources\`); confirm the PNGs are present there before
the build. The guard keeps the components working either way; this is how to find out
whether to chase the embedding.

## Use: the three-button flow

Place the three components on the canvas and wire them in order.

### 1. Passthrough Export

- **Surface (S):** the surface to send. A NURBS surface or a single Brep face.
- **U Count (U), V Count (V):** the structured grid density. Defaults 24 and 12, the
  Phase 5 wing grid.
- **Folder (F):** where to write `payload.json`.
- **Write (W):** a Button. Press it to mesh, tag, and write the payload.

Outputs the payload path (P) and the meshed `Mesh` (M) so the canvas shows what was
sent.

The mesh is built on a structured UV grid on purpose. The grid is what gives clean quad
connectivity, which is what lets the export produce the identity and topology tags
(node ids, edge adjacency, face winding) the validity gate and the reconstruction
depend on. A raw, unstructured mesh dump would not carry that, and the runner would
reject it. Keep the surface a single untrimmed patch so the grid maps cleanly.

### 2. Passthrough Trigger

- **Payload (P):** the path from Export.
- **Return (R):** the folder the run writes `result.json`, `field.json`, and
  `status.json` into.
- **Command (C):** the runner invocation. The component appends the payload and the
  return folder, so the composed call is `<Command> "<payload>" "<return>"`. The
  default is:

  ```
  uv run python scripts\run_roundtrip.py run
  ```

- **WorkDir (D):** the working directory for the command, usually the repo root, so the
  relative `scripts\run_roundtrip.py` path resolves. Optional but normally needed.
- **Run (X):** a Button. Press it to launch.

The launch is a separate process started through `cmd.exe` and not waited on. The canvas
stays live. The run writes the return files on its own time. Pressing Run also clears
any stale `status.json` from a prior run, so Import shows `not ready` until the fresh
marker lands.

To wire your own invocation: anything you can paste into a terminal that runs the Phase
7a runner works. For example, if you are not using `uv`:

```
python scripts\run_roundtrip.py run
```

with WorkDir set to the repo root. The component adds the payload and return paths.

### 3. Passthrough Import

- **Return (R):** the same return folder.
- **Pull (P):** a Button. Press it to read the marker and pull.

Outputs:

- **Mesh (M):** the returned mesh, colored by deviation through a viridis ramp (the same
  color family as the static render). Gray on a flagged-but-done pass.
- **Surface (Srf):** the reconstructed analytic surface, rebuilt from the result's
  surface block, to set beside the original. Null on a flagged pass.
- **Range (R):** the deviation range, for a stable color scale.
- **CollisionPoints (C):** the vertices of each flagged collision pair, to mark on top.
- **FoldedFaces (F):** the row indices of flagged folded faces.
- **DriftMax (D):** the headline positional drift max from the marker. Absent on a
  flagged pass.
- **CurvatureMax (K):** the headline curvature residual max from the marker. Absent on a
  flagged pass.
- **Status (S):** the status text. `not ready` until the marker appears, `pending` while
  the run is working, `failed: <reason>` on a failure, `done (clean)` or
  `done (flagged) [signals]` on success.

Import reads `status.json` first and refuses to pull until it says `done`. It resolves
the `field` path against the return folder itself, not the working directory. When the
marker names a surface source it reads the result's surface block and rebuilds the
reconstructed `NurbsSurface` from its control net, weights, degrees, and knots
(spec-07-surface). That rebuild is construction, not computation: the analytic surface
was fit in Python and is reconstructed here verbatim. It does no other math: it reads
what the run wrote and draws it.

### The completed round trip

The bench now shows both surfaces: the original surface the user exported (the **Mesh**
the Export component returns, and the source surface itself on the canvas) and the
reconstructed surface returned from the run (the **Surface** output here). Overlay the
two and the trip is visible end to end on real surfaces you can probe: NURBS out, meshed,
morphed, meshed back, NURBS back, with the drift and curvature measured between them. The
**DriftMax** and **CurvatureMax** outputs carry exactly the two numbers the round trip is
judged on, read from the marker, not recomputed. That is the whole reverse-problem
boundary made live: not "here is a colored mesh" but "here is the original, here is what
came back, and here is how far it drifted and where the curvature broke."

## Scope notes

State these plainly; they are the boundaries the project holds, not omissions.

- **Positional deviation colors the mesh.** The field carries one scalar per vertex,
  which colors cleanly. Curvature deviation is sampled on a different grid, so it is not
  used to color the mesh; its headline maximum is surfaced as the **CurvatureMax** number
  and the full curvature field stays in the static render.
- **The solver is synthetic.** The run inside the trigger is the synthetic morph from
  the earlier phases, not a real CFD solve. The bench demonstrates the round-trip
  architecture and the data continuity across the boundary, not a physical solution.
- **Measurement is in Python.** The colors and points here are display and exploration.
  The authoritative numbers (drift, the deviation field, the flags) are computed by the
  tested Python and read from the field and status files. Drift is never recomputed in
  C#.
- **Structured single patch.** The export assumes a single untrimmed surface meshed on a
  regular UV grid. Trimmed or multi-patch surfaces are out of scope here, the same
  single-surface boundary the rest of the project holds.

## The validation you will run

1. `dotnet build` succeeds.
2. The `.gha` loads in Grasshopper with no version or reference error.
3. Export a surface to a payload, and confirm it validates: run
   `uv run python scripts\run_roundtrip.py run <payload> <return>` by hand and see
   `status.json` say `done`.
4. Trigger from the canvas and confirm the run produces the return files without
   freezing Grasshopper.
5. Import and confirm the mesh colors by deviation and matches the static render, and
   that a flagged field shows gray with the collision pairs marked.
6. On a clean pass, confirm the **Surface** output is a reconstructed `NurbsSurface` that
   overlays the original exported surface, and that **DriftMax** and **CurvatureMax** show
   the numbers from `status.json`. On a flagged pass, confirm the Surface output is null.
