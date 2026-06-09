using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using Grasshopper.Kernel;
using Rhino.Geometry;

namespace PassthroughGh
{
    /// <summary>
    /// Button three of the bench. Read status.json from the return folder; once it says
    /// done, pull the field and color the mesh by deviation, laying the flagged regions
    /// on top. This component does no computation. It reads what the Phase 7a run wrote.
    /// The single source of truth stays in Python.
    ///
    /// Behavior on the marker:
    ///   absent or not done -> a "not ready" status, nothing pulled.
    ///   failed             -> the reason, nothing pulled.
    ///   done               -> resolve the field path against the folder, read it, build
    ///                         the mesh, color vertices by deviation through a viridis
    ///                         ramp, surface the collision pairs as points and the folded
    ///                         faces as indices. On a flagged-but-done pass (deviation
    ///                         empty) the mesh is neutral gray and the flags still show,
    ///                         mirroring the static render's fallback.
    ///
    /// The results are cached so they persist across the button's true/false pulses: a
    /// Pull edge re-reads from disk, every solve re-emits the cached outputs.
    /// </summary>
    public class ImportComponent : GH_Component
    {
        private bool _prevPull;

        private Mesh? _mesh;
        private Interval _range = Interval.Unset;
        private List<Point3d> _points = new();
        private List<int> _folded = new();
        private double _drift = double.NaN;
        private string _status = "idle";

        private static readonly JsonSerializerOptions JsonOpts = new()
        {
            PropertyNameCaseInsensitive = true,
        };

        public ImportComponent()
            : base("Passthrough Import", "PXImport",
                   "Read status.json; once it says done, pull the field, color the mesh " +
                   "by deviation, and surface the flagged regions. No computation here.",
                   "Passthrough", "Bench")
        {
        }

        public override Guid ComponentGuid => new Guid("6c7b4012-6953-4e0a-9c5d-2d3e4f506172");
        public override GH_Exposure Exposure => GH_Exposure.primary;
        protected override System.Drawing.Bitmap Icon => IconLoader.Load("passthrough_import_24.png");

        protected override void RegisterInputParams(GH_Component.GH_InputParamManager pManager)
        {
            pManager.AddTextParameter("Return", "R",
                "The return folder, where status.json and field.json live.",
                GH_ParamAccess.item);
            pManager.AddBooleanParameter("Pull", "P",
                "Flip true to read status.json and pull the field if it says done.",
                GH_ParamAccess.item, false);
        }

        protected override void RegisterOutputParams(GH_Component.GH_OutputParamManager pManager)
        {
            pManager.AddMeshParameter("Mesh", "M",
                "The returned mesh, colored by deviation, or gray on a flagged pass.",
                GH_ParamAccess.item);
            pManager.AddIntervalParameter("Range", "R",
                "The deviation range (min, max) from the field, for a stable color scale.",
                GH_ParamAccess.item);
            pManager.AddPointParameter("CollisionPoints", "C",
                "The vertices of each flagged collision pair, for marking on top.",
                GH_ParamAccess.list);
            pManager.AddIntegerParameter("FoldedFaces", "F",
                "The row indices of flagged folded faces.",
                GH_ParamAccess.list);
            pManager.AddNumberParameter("DriftMax", "D",
                "The headline drift max from status.json, or none on a flagged pass.",
                GH_ParamAccess.item);
            pManager.AddTextParameter("Status", "S",
                "The status text: not ready, pending, failed with reason, or done.",
                GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            string folder = "";
            bool pull = false;
            da.GetData(0, ref folder);
            da.GetData(1, ref pull);

            // Re-read from disk on the rising edge of Pull. Otherwise re-emit the cache,
            // so the colored mesh survives the button releasing.
            if (pull && !_prevPull)
            {
                ReadReturn(folder);
            }
            _prevPull = pull;

            da.SetData(0, _mesh);
            if (_range.IsValid) da.SetData(1, _range);
            da.SetDataList(2, _points);
            da.SetDataList(3, _folded);
            if (!double.IsNaN(_drift)) da.SetData(4, _drift);
            da.SetData(5, _status);
        }

        private void ReadReturn(string folder)
        {
            // Reset the cache for a fresh pull.
            _mesh = null;
            _range = Interval.Unset;
            _points = new List<Point3d>();
            _folded = new List<int>();
            _drift = double.NaN;

            if (string.IsNullOrWhiteSpace(folder))
            {
                _status = "not ready: no folder";
                return;
            }

            string statusPath = Path.Combine(folder, "status.json");
            if (!File.Exists(statusPath))
            {
                _status = "not ready: no status.json yet";
                return;
            }

            StatusDto? st;
            try
            {
                st = JsonSerializer.Deserialize<StatusDto>(File.ReadAllText(statusPath), JsonOpts);
            }
            catch (Exception ex)
            {
                _status = $"status unreadable: {ex.Message}";
                return;
            }
            if (st is null || string.IsNullOrEmpty(st.Status))
            {
                _status = "status unreadable";
                return;
            }

            if (st.Status == "failed")
            {
                _status = $"failed: {st.Reason}";
                return;
            }
            if (st.Status != "done")
            {
                _status = $"pending: {st.Status}";
                return;
            }

            // done. Resolve the field path against the status file's own folder, not the
            // working directory, exactly as the contract specifies.
            string fieldName = string.IsNullOrEmpty(st.Field) ? "field.json" : st.Field;
            string fieldPath = Path.Combine(folder, fieldName);
            if (!File.Exists(fieldPath))
            {
                _status = $"done, but field file missing: {fieldName}";
                return;
            }

            FieldDto? fd;
            try
            {
                fd = JsonSerializer.Deserialize<FieldDto>(File.ReadAllText(fieldPath), JsonOpts);
            }
            catch (Exception ex)
            {
                _status = $"field unreadable: {ex.Message}";
                return;
            }
            if (fd is null || fd.Vertices is null || fd.Faces is null)
            {
                _status = "field unreadable";
                return;
            }

            BuildMesh(fd);
            SurfaceFlags(fd);

            _drift = st.DriftMax ?? double.NaN;
            bool flagged = st.Flagged ?? false;
            string flag = flagged ? "flagged" : "clean";
            string signals =
                (st.Signals is { Length: > 0 }) ? $" [{string.Join(", ", st.Signals)}]" : "";
            _status = $"done ({flag}){signals}";
        }

        private void BuildMesh(FieldDto fd)
        {
            var mesh = new Mesh();
            foreach (var v in fd.Vertices!)
            {
                if (v.Length >= 3) mesh.Vertices.Add(v[0], v[1], v[2]);
            }
            foreach (var f in fd.Faces!)
            {
                if (f.Length == 4) mesh.Faces.AddFace(f[0], f[1], f[2], f[3]);
                else if (f.Length == 3) mesh.Faces.AddFace(f[0], f[1], f[2]);
            }

            int count = mesh.Vertices.Count;
            mesh.VertexColors.Clear();

            if (fd.Deviation is { } dev && dev.Length == count && count > 0)
            {
                double lo, hi;
                if (fd.DeviationRange is { Length: 2 } dr)
                {
                    lo = dr[0];
                    hi = dr[1];
                }
                else
                {
                    lo = dev.Min();
                    hi = dev.Max();
                }
                _range = new Interval(lo, hi);

                double span = hi - lo;
                for (int i = 0; i < count; i++)
                {
                    double t = span > 1e-15 ? (dev[i] - lo) / span : 0.0;
                    mesh.VertexColors.Add(Colormap.Viridis(t));
                }
            }
            else
            {
                // Flagged-but-done, or a field with no deviation: neutral gray. The flags
                // are still surfaced below, mirroring the static render's fallback.
                var gray = Color.FromArgb(179, 179, 179);
                for (int i = 0; i < count; i++) mesh.VertexColors.Add(gray);
                _range = Interval.Unset;
            }

            mesh.Normals.ComputeNormals();
            _mesh = mesh;
        }

        private void SurfaceFlags(FieldDto fd)
        {
            if (_mesh is null) return;

            // Map node id to vertex row so a flagged pair (in node-id space) resolves to
            // mesh vertices, the same resolution the static render's overlay does.
            var idToRow = new Dictionary<int, int>();
            if (fd.NodeIds is { } ids)
            {
                for (int row = 0; row < ids.Length; row++) idToRow[ids[row]] = row;
            }

            if (fd.Flagged?.CollisionPairs is { } pairs)
            {
                foreach (var pair in pairs)
                {
                    foreach (int nid in pair)
                    {
                        if (idToRow.TryGetValue(nid, out int row) &&
                            row >= 0 && row < _mesh.Vertices.Count)
                        {
                            Point3f p = _mesh.Vertices[row];
                            _points.Add(new Point3d(p.X, p.Y, p.Z));
                        }
                    }
                }
            }

            if (fd.Flagged?.FoldedFaces is { } folded)
            {
                _folded.AddRange(folded);
            }
        }

        // --- DTOs for the status marker and the field file ---------------------

        private sealed class StatusDto
        {
            [JsonPropertyName("status")] public string? Status { get; set; }
            [JsonPropertyName("flagged")] public bool? Flagged { get; set; }
            [JsonPropertyName("signals")] public string[]? Signals { get; set; }
            [JsonPropertyName("result")] public string? Result { get; set; }
            [JsonPropertyName("field")] public string? Field { get; set; }
            [JsonPropertyName("drift_max")] public double? DriftMax { get; set; }
            [JsonPropertyName("reason")] public string? Reason { get; set; }
        }

        private sealed class FieldDto
        {
            [JsonPropertyName("format")] public string? Format { get; set; }
            [JsonPropertyName("units")] public string? Units { get; set; }
            [JsonPropertyName("vertices")] public double[][]? Vertices { get; set; }
            [JsonPropertyName("faces")] public int[][]? Faces { get; set; }
            [JsonPropertyName("node_ids")] public int[]? NodeIds { get; set; }
            [JsonPropertyName("deviation")] public double[]? Deviation { get; set; }
            [JsonPropertyName("deviation_range")] public double[]? DeviationRange { get; set; }
            [JsonPropertyName("flagged")] public FlaggedDto? Flagged { get; set; }
        }

        private sealed class FlaggedDto
        {
            [JsonPropertyName("collision_pairs")] public int[][]? CollisionPairs { get; set; }
            [JsonPropertyName("folded_faces")] public int[]? FoldedFaces { get; set; }
        }
    }
}
