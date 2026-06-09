using System;
using System.Drawing;
using Grasshopper.Kernel;

namespace PassthroughGh
{
    /// <summary>
    /// Plugin metadata Grasshopper reads when it loads the .gha. The bench is three
    /// components on one tab: export a tagged mesh, trigger the Phase 7a runner,
    /// import the colored deviation field. No math lives here; the components write
    /// the payload schema and read the field and status marker the tested Python owns.
    /// </summary>
    public class PassthroughGhInfo : GH_AssemblyInfo
    {
        public override string Name => "Passthrough";

        public override Bitmap Icon => null!;

        public override string Description =>
            "Round-trip bench for passthrough. Export a NURBS surface as a tagged mesh, " +
            "trigger the Phase 7a runner across the boundary, import the result and color " +
            "the mesh by deviation once the status marker says done.";

        public override Guid Id => new Guid("7b1e9c40-2c0a-4d3b-9f51-1a2b3c4d5e6f");

        public override string AuthorName => "passthrough";

        public override string AuthorContact => "";

        public override string Version => "0.1.0";
    }
}
