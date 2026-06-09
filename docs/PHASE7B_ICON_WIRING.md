# Phase 7b — Component icon wiring

Wires the three bench components to embedded PNG icons so Grasshopper draws them
instead of the default checkerboard. Mechanical change only; no behavior or logic
changes.

## What changed

- **`plugin/PassthroughGh/PassthroughGh.csproj`** — confirmed the
  `EmbeddedResource Include="Resources\*.png"` item group (with a comment noting
  the manifest-name convention). `RootNamespace` stays `PassthroughGh`, so each
  PNG embeds as `PassthroughGh.Resources.<filename>`. `UseWindowsForms` is still
  present, which is what makes `System.Drawing.Bitmap` available.
- **`plugin/PassthroughGh/IconLoader.cs`** (new) — `IconLoader.Load(fileName)`
  opens the embedded stream by manifest name, reads it into a `Bitmap`, disposes
  the stream, and returns the bitmap.
- **`ExportComponent.cs` / `TriggerComponent.cs` / `ImportComponent.cs`** — each
  `Icon` property changed from `=> null!` to
  `=> IconLoader.Load("passthrough_<name>_24.png")`, keeping the existing
  expression-bodied `protected override System.Drawing.Bitmap` signature.
- **`plugin/PassthroughGh/Resources/`** (new) — folder with a README; the three
  PNGs are dropped here.

The assembly-level icon in `PassthroughGhInfo.cs` is intentionally left as-is;
this change only covers the three component icons.

## Not verified here

This cannot be built or loaded in the sandbox — it needs Rhino 8 on Windows. The
code is wired but unverified.

## The gate (Nathan)

1. Drop the three PNGs into `plugin/PassthroughGh/Resources/`:
   `passthrough_export_24.png`, `passthrough_trigger_24.png`,
   `passthrough_import_24.png`.
2. `dotnet build -c Release`.
3. Recopy the emitted `.gha` to the Grasshopper Libraries folder
   (`%APPDATA%\Grasshopper\Libraries`).
4. `Unblock-File` the `.gha`.
5. Restart Grasshopper.
6. Confirm the three components show their icons instead of the checkerboard.
