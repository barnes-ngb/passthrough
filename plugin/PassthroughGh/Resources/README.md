# Component icons

Drop the three 24×24 PNGs here. The csproj embeds `Resources\*.png` into the
assembly and each component loads its own by manifest name
(`PassthroughGh.Resources.<filename>`) via `IconLoader.Load`:

- `passthrough_export_24.png`  — Passthrough Export
- `passthrough_trigger_24.png` — Passthrough Trigger
- `passthrough_import_24.png`  — Passthrough Import

Until these files are present the wiring compiles, but the icons resolve to null
at load time. Once they land, rebuild the `.gha` and the components show their
icons instead of the default checkerboard.
