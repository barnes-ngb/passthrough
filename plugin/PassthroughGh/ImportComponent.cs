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
    ///                         faces as indices. When the marker names a surface source,
    ///                         read the result's surface block and rebuild the
    ///                         reconstructed NurbsSurface so it sits beside the original.
    ///                         On a flagged-but-done pass (deviation empty) the mesh is
    ///                         neutral gray, the surface is null, and the flags still
    ///                         show, mirroring the static render's fallback.
    ///
    /// Building the surface is construction, not computation: the analytic surface was
    /// fit in Python and is rebuilt here verbatim from its control net, weights, degrees,
    /// and knots (spec-07-surface). No geometry math runs in C#.
    ///
    /// The results are cached so they persist across the button's true/false pulses: a
    /// Pull edge re-reads from disk, every solve re-emits the cached outputs.
    /// </summary>
    public class ImportComponent : GH_Component
    {
        private bool _prevPull;

        private Mesh? _mesh;
        private NurbsSurface? _surface;
        private Interval _range = Interval.Unset;
        private List<Point3d> _points = new();
        private List<int> _folded = new();
        private double _drift = double.NaN;
        private double _curvature = double.NaN;
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
            pManager.AddSurfaceParameter("Surface", "Srf",
                "The reconstructed analytic surface, rebuilt from the result's surface " +
                "block, to set beside the original. Null on a flagged pass.",
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
                "The headline positional drift max from status.json, or none on a " +
                "flagged pass.",
                GH_ParamAccess.item);
            pManager.AddNumberParameter("CurvatureMax", "K",
                "The headline curvature residual max from status.json, or none on a " +
                "flagged pass.",
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
            da.SetData(1, _surface);
            if (_range.IsValid) da.SetData(2, _range);
            da.SetDataList(3, _points);
            da.SetDataList(4, _folded);
            if (!double.IsNaN(_drift)) da.SetData(5, _drift);
            if (!double.IsNaN(_curvature)) da.SetData(6, _curvature);
            da.SetData(7, _status);
        }

        private void ReadReturn(string folder)
        {
            // Reset the cache for a fresh pull.
            _mesh = null;
            _surface = null;
            _range = Interval.Unset;
            _points = new List<Point3d>();
            _folded = new List<int>();
            _drift = double.NaN;
            _curvature = double.NaN;

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
            ReadSurface(folder, st);

            _drift = st.DriftMax ?? double.NaN;
            _curvature = st.CurvatureMax ?? double.NaN;
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

        private void ReadSurface(string folder, StatusDto st)
        {
            // On a flagged pass the marker's surface key is null and there is nothing to
            // rebuild: the surface output stays null and the gray-mesh-with-flags behavior
            // is unchanged. On a clean pass the marker names the file holding the surface
            // block (the result file), resolved against the return folder like the field.
            if (string.IsNullOrEmpty(st.Surface)) return;

            string surfacePath = Path.Combine(folder, st.Surface);
            if (!File.Exists(surfacePath)) return;

            try
            {
                var res = JsonSerializer.Deserialize<ResultDto>(
                    File.ReadAllText(surfacePath), JsonOpts);
                if (res?.Surface is { ControlPoints: { } } sdto)
                {
                    BuildSurface(sdto);
                }
            }
            catch
            {
                // A surface that will not parse is not fatal: the mesh and the numbers
                // still show. Leave the surface null, the same as a flagged pass.
            }
        }

        private void BuildSurface(SurfaceDto s)
        {
            if (s.ControlPoints is null || s.KnotsU is null || s.KnotsV is null) return;

            int cu = s.CountU, cv = s.CountV;
            int du = s.DegreeU, dv = s.DegreeV;

            // No geometry math: this is verbatim construction from the carried analytic
            // form (spec-07-surface). The fit happened in Python; this rebuilds the same
            // NurbsSurface so it can sit beside the original and be measured.
            var ns = NurbsSurface.Create(3, true, du + 1, dv + 1, cu, cv);
            if (ns is null) return;

            for (int i = 0; i < cu; i++)
            {
                for (int j = 0; j < cv; j++)
                {
                    double[] p = s.ControlPoints[i][j];
                    double w = (s.Weights is { } ws) ? ws[i][j] : 1.0;
                    ns.Points.SetPoint(i, j, p[0], p[1], p[2], w);
                }
            }

            // The block carries the full clamped knot vector (length count + degree + 1).
            // RhinoCommon's knot list omits the outermost knot at each end, so the first
            // and last entries are dropped here, the same conversion _make_surface does.
            for (int k = 1; k < s.KnotsU.Length - 1; k++) ns.KnotsU[k - 1] = s.KnotsU[k];
            for (int k = 1; k < s.KnotsV.Length - 1; k++) ns.KnotsV[k - 1] = s.KnotsV[k];

            if (ns.IsValid) _surface = ns;
        }

        // --- DTOs for the status marker and the field file ---------------------

        private sealed class StatusDto
        {
            [JsonPropertyName("status")] public string? Status { get; set; }
            [JsonPropertyName("flagged")] public bool? Flagged { get; set; }
            [JsonPropertyName("signals")] public string[]? Signals { get; set; }
            [JsonPropertyName("result")] public string? Result { get; set; }
            [JsonPropertyName("field")] public string? Field { get; set; }
            [JsonPropertyName("surface")] public string? Surface { get; set; }
            [JsonPropertyName("drift_max")] public double? DriftMax { get; set; }
            [JsonPropertyName("curvature_max")] public double? CurvatureMax { get; set; }
            [JsonPropertyName("reason")] public string? Reason { get; set; }
        }

        // The result file, read only for its surface block. Everything else the import
        // side needs (mesh, deviation, flags, headline numbers) is in the field file and
        // the status marker; the result is read only when the marker names it as the
        // surface source, so a flagged pass never opens it.
        private sealed class ResultDto
        {
            [JsonPropertyName("surface")] public SurfaceDto? Surface { get; set; }
        }

        private sealed class SurfaceDto
        {
            [JsonPropertyName("format")] public string? Format { get; set; }
            [JsonPropertyName("degree_u")] public int DegreeU { get; set; }
            [JsonPropertyName("degree_v")] public int DegreeV { get; set; }
            [JsonPropertyName("count_u")] public int CountU { get; set; }
            [JsonPropertyName("count_v")] public int CountV { get; set; }
            [JsonPropertyName("control_points")] public double[][][]? ControlPoints { get; set; }
            [JsonPropertyName("weights")] public double[][]? Weights { get; set; }
            [JsonPropertyName("knots_u")] public double[]? KnotsU { get; set; }
            [JsonPropertyName("knots_v")] public double[]? KnotsV { get; set; }
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
