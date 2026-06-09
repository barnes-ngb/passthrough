using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text.Json;
using Grasshopper.Kernel;
using Rhino.Geometry;

namespace PassthroughGh
{
    /// <summary>
    /// Button one of the bench. Mesh a surface on a structured UV grid, tag every
    /// vertex with identity and topology, and write the incoming payload JSON the
    /// Phase 7a runner reads.
    ///
    /// The tags are the point. A raw mesh dump is not enough: identity (a stable node
    /// id per vertex) and topology (edge adjacency, face winding) are what the validity
    /// gate and the reconstruction depend on. Meshing on a structured UV grid is the
    /// simplest way to get clean quad connectivity, so that is the constraint this
    /// component holds: it samples a regular (U x V) grid and builds quad faces by grid
    /// adjacency. The payload it writes is exactly the schema encode.py produces, so the
    /// runner validates it with no special case.
    /// </summary>
    public class ExportComponent : GH_Component
    {
        public ExportComponent()
            : base("Passthrough Export", "PXExport",
                   "Mesh a surface on a structured UV grid, tag it with identity and " +
                   "topology, and write the incoming payload JSON the Phase 7a runner reads.",
                   "Passthrough", "Bench")
        {
        }

        public override Guid ComponentGuid => new Guid("4a9d2e10-8b71-4c2e-9a3f-0b1c2d3e4f50");
        public override GH_Exposure Exposure => GH_Exposure.primary;
        protected override System.Drawing.Bitmap Icon => IconLoader.Load("passthrough_export_24.png");

        protected override void RegisterInputParams(GH_Component.GH_InputParamManager pManager)
        {
            pManager.AddSurfaceParameter("Surface", "S",
                "The NURBS surface (or a Brep face) to send across the boundary.",
                GH_ParamAccess.item);
            pManager.AddIntegerParameter("U Count", "U",
                "Grid samples in the surface U direction (chordwise). At least 2.",
                GH_ParamAccess.item, 24);
            pManager.AddIntegerParameter("V Count", "V",
                "Grid samples in the surface V direction (spanwise). At least 2.",
                GH_ParamAccess.item, 12);
            pManager.AddTextParameter("Folder", "F",
                "Output folder. payload.json is written here.",
                GH_ParamAccess.item);
            pManager.AddBooleanParameter("Write", "W",
                "Flip true to mesh, tag, and write payload.json. The export button.",
                GH_ParamAccess.item, false);
        }

        protected override void RegisterOutputParams(GH_Component.GH_OutputParamManager pManager)
        {
            pManager.AddTextParameter("Payload", "P",
                "Path to the written payload.json, empty until Write fires.",
                GH_ParamAccess.item);
            pManager.AddMeshParameter("Mesh", "M",
                "The structured quad mesh that was sent, so the canvas shows what crossed.",
                GH_ParamAccess.item);
        }

        protected override void SolveInstance(IGH_DataAccess da)
        {
            Surface? srf = null;
            int nu = 24, nv = 12;
            string folder = "";
            bool write = false;

            if (!da.GetData(0, ref srf) || srf is null) return;
            da.GetData(1, ref nu);
            da.GetData(2, ref nv);
            da.GetData(3, ref folder);
            da.GetData(4, ref write);

            if (nu < 2 || nv < 2)
            {
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error,
                    "U Count and V Count must each be at least 2.");
                return;
            }

            // Structured grid: evaluate on a regular (nu x nv) grid in the surface's own
            // domain, but carry the normalized (0..1) parameter as the vertex uv. That
            // matches the geometry.py convention: the parameterization travels with the
            // mesh, normalized, so the reconstructor can assume it rather than solve it.
            Interval domU = srf.Domain(0);
            Interval domV = srf.Domain(1);

            int n = nu * nv;
            var verts = new double[n][];
            var uv = new double[n][];
            var mesh = new Mesh();

            for (int a = 0; a < nu; a++)
            {
                double un = (double)a / (nu - 1);
                double u = domU.ParameterAt(un);
                for (int b = 0; b < nv; b++)
                {
                    double vn = (double)b / (nv - 1);
                    double v = domV.ParameterAt(vn);
                    int idx = a * nv + b;
                    Point3d p = srf.PointAt(u, v);
                    verts[idx] = new[] { p.X, p.Y, p.Z };
                    uv[idx] = new[] { un, vn };
                    mesh.Vertices.Add(p);
                }
            }

            // Quad faces by grid connectivity, in the same winding geometry.py uses:
            // (v00, v10, v11, v01).
            var faces = new List<int[]>((nu - 1) * (nv - 1));
            for (int a = 0; a < nu - 1; a++)
            {
                for (int b = 0; b < nv - 1; b++)
                {
                    int v00 = a * nv + b;
                    int v01 = a * nv + (b + 1);
                    int v10 = (a + 1) * nv + b;
                    int v11 = (a + 1) * nv + (b + 1);
                    faces.Add(new[] { v00, v10, v11, v01 });
                    mesh.Faces.AddFace(v00, v10, v11, v01);
                }
            }
            mesh.Normals.ComputeNormals();
            mesh.Compact();

            // Identity: node ids are the vertex order (0..n-1), the same default the
            // Python side uses. With identity node ids the winding equals the faces and
            // the adjacency is the quad-edge graph, so validate_incoming's two equality
            // checks (winding == node_ids[faces], adjacency == _adjacency_from_winding)
            // hold by construction.
            var nodeIds = new int[n];
            for (int i = 0; i < n; i++) nodeIds[i] = i;
            var adjacency = AdjacencyFromFaces(faces);

            string payloadPath = "";
            if (write)
            {
                if (string.IsNullOrWhiteSpace(folder))
                {
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Error,
                        "Folder is required to write the payload.");
                }
                else
                {
                    try
                    {
                        Directory.CreateDirectory(folder);
                        payloadPath = Path.Combine(folder, "payload.json");
                        WritePayload(payloadPath, verts, faces, uv, nodeIds, adjacency);
                    }
                    catch (Exception ex)
                    {
                        AddRuntimeMessage(GH_RuntimeMessageLevel.Error,
                            $"Failed to write payload: {ex.Message}");
                        payloadPath = "";
                    }
                }
            }

            da.SetData(0, payloadPath);
            da.SetData(1, mesh);
        }

        /// <summary>
        /// Edge graph keyed by node id, derived from the quad faces. Mirrors
        /// encode._adjacency_from_winding: two node ids are neighbors when they are
        /// cyclically consecutive in some face. Each neighbor list is sorted and
        /// de-duplicated. With identity node ids the key is the vertex row.
        /// </summary>
        private static Dictionary<string, int[]> AdjacencyFromFaces(List<int[]> faces)
        {
            var neighbors = new Dictionary<int, SortedSet<int>>();

            void Add(int i, int j)
            {
                if (!neighbors.TryGetValue(i, out var set))
                {
                    set = new SortedSet<int>();
                    neighbors[i] = set;
                }
                set.Add(j);
            }

            foreach (var f in faces)
            {
                int k = f.Length;
                for (int a = 0; a < k; a++)
                {
                    int i = f[a];
                    int j = f[(a + 1) % k];
                    Add(i, j);
                    Add(j, i);
                }
            }

            var result = new Dictionary<string, int[]>(neighbors.Count);
            foreach (var kv in neighbors)
            {
                var arr = new int[kv.Value.Count];
                kv.Value.CopyTo(arr);
                result[kv.Key.ToString(CultureInfo.InvariantCulture)] = arr;
            }
            return result;
        }

        /// <summary>
        /// Serialize the payload to the encode.py schema: vertices, quad faces, uv,
        /// units, frame, the topology block (node_ids, adjacency, winding), and the
        /// descriptor. The winding equals the faces because the node ids are identity.
        /// The descriptor mirrors run.default_descriptor so the run reproduces the
        /// Phase 5 loop on a Grasshopper-exported wing.
        /// </summary>
        private static void WritePayload(
            string path,
            double[][] verts,
            List<int[]> faces,
            double[][] uv,
            int[] nodeIds,
            Dictionary<string, int[]> adjacency)
        {
            var payload = new Dictionary<string, object?>
            {
                ["vertices"] = verts,
                ["faces"] = faces,
                ["uv"] = uv,
                ["units"] = "mm",
                ["frame"] =
                    "x chordwise, y spanwise, z thickness; surface UV: u chordwise, v spanwise",
                ["topology"] = new Dictionary<string, object?>
                {
                    ["node_ids"] = nodeIds,
                    ["adjacency"] = adjacency,
                    ["winding"] = faces,
                },
                ["descriptor"] = new Dictionary<string, object?>
                {
                    ["constraints"] = new object[]
                    {
                        new Dictionary<string, object?>
                        {
                            ["type"] = "ruled",
                            ["tolerance"] = 0.01,
                            ["direction"] = 1,
                        },
                    },
                    ["preserve"] = new Dictionary<string, object?>
                    {
                        ["leading_edge"] = "curvature_class",
                    },
                    ["provenance"] = new Dictionary<string, object?>
                    {
                        ["source"] = "grasshopper_export",
                        ["iteration"] = 0,
                    },
                },
            };

            var opts = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(path, JsonSerializer.Serialize(payload, opts));
        }
    }
}
