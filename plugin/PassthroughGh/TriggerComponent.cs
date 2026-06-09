using System;
using System.Diagnostics;
using System.IO;
using Grasshopper.Kernel;

namespace PassthroughGh
{
    /// <summary>
    /// Button two of the bench. Launch the Phase 7a runner on a payload as a separate,
    /// non-blocking process and return. The run writes its result, field, and
    /// status.json to the return folder on its own time. The canvas never freezes:
    /// the component starts the process and does not wait on it.
    ///
    /// The launch goes through cmd.exe so the Command input can be any invocation form,
    /// for example "uv run python scripts\run_roundtrip.py run". The payload path and
    /// the return folder are appended, so the composed call is
    /// `&lt;Command&gt; "&lt;payload&gt;" "&lt;return&gt;"`. Output is not redirected:
    /// an unread redirect buffer would block the child, the opposite of what is wanted.
    /// </summary>
    public class TriggerComponent : GH_Component
    {
        private bool _prevRun;

        public TriggerComponent()
            : base("Passthrough Trigger", "PXTrigger",
                   "Launch the Phase 7a runner on a payload as a separate, non-blocking " +
                   "process. The canvas stays live; the run writes status.json when done.",
                   "Passthrough", "Bench")
        {
        }

        public override Guid ComponentGuid => new Guid("5b8c3f21-7a62-4d1f-8b4e-1c2d3e4f5061");
        public override GH_Exposure Exposure => GH_Exposure.primary;
        protected override System.Drawing.Bitmap Icon => null!;

        protected override void RegisterInputParams(GH_Component.GH_InputParamManager pManager)
        {
            pManager.AddTextParameter("Payload", "P",
                "Path to payload.json from the Export component.",
                GH_ParamAccess.item);
            pManager.AddTextParameter("Return", "R",
                "Return folder. result.json, field.json, and status.json land here.",
                GH_ParamAccess.item);
            pManager.AddTextParameter("Command", "C",
                "The runner invocation. Payload and Return are appended to it. " +
                @"Example: uv run python scripts\run_roundtrip.py run",
                GH_ParamAccess.item, @"uv run python scripts\run_roundtrip.py run");
            pManager.AddTextParameter("WorkDir", "D",
                "Working directory for the command, usually the repo root. Optional.",
                GH_ParamAccess.item, "");
            pManager.AddBooleanParameter("Run", "X",
                "Flip true to launch the runner. The trigger button. Non-blocking.",
                GH_ParamAccess.item, false);
        }

        protected override void RegisterOutputParams(GH_Component.GH_OutputParamManager pManager)
        {
            pManager.AddTextParameter("Launched", "L",
                "The command that was launched, or idle when Run is false.",
                GH_ParamAccess.item);
            pManager.AddTextParameter("Return", "R",
                "The return folder, passed through for the Import component.",
                GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            string payload = "", ret = "", command = "", workdir = "";
            bool run = false;

            da.GetData(0, ref payload);
            da.GetData(1, ref ret);
            da.GetData(2, ref command);
            da.GetData(3, ref workdir);
            da.GetData(4, ref run);

            string launched = "idle";

            // Act on the rising edge only, so holding Run true or an upstream recompute
            // does not relaunch. A Grasshopper Button pulses true then false; the edge
            // catches the true and launches once.
            if (run && !_prevRun)
            {
                if (string.IsNullOrWhiteSpace(payload) ||
                    string.IsNullOrWhiteSpace(ret) ||
                    string.IsNullOrWhiteSpace(command))
                {
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Error,
                        "Payload, Return, and Command are all required to launch.");
                }
                else
                {
                    try
                    {
                        Directory.CreateDirectory(ret);

                        // Clear any stale marker from a prior run so the Import side shows
                        // "not ready" until this run writes a fresh status.json.
                        string statusPath = Path.Combine(ret, "status.json");
                        try { if (File.Exists(statusPath)) File.Delete(statusPath); }
                        catch { /* a locked stale marker is not fatal; the run overwrites it */ }

                        string full = $"{command} \"{payload}\" \"{ret}\"";
                        var psi = new ProcessStartInfo
                        {
                            FileName = "cmd.exe",
                            Arguments = $"/c {full}",
                            UseShellExecute = false,
                            CreateNoWindow = true,
                        };
                        if (!string.IsNullOrWhiteSpace(workdir))
                            psi.WorkingDirectory = workdir;

                        // Start and return. No WaitForExit, no redirected streams: the
                        // process runs on its own and writes the return files. This is
                        // the non-blocking trigger.
                        Process.Start(psi);
                        launched = full;
                    }
                    catch (Exception ex)
                    {
                        AddRuntimeMessage(GH_RuntimeMessageLevel.Error,
                            $"Launch failed: {ex.Message}");
                        launched = "launch failed";
                    }
                }
            }

            _prevRun = run;
            da.SetData(0, launched);
            da.SetData(1, ret);
        }
    }
}
