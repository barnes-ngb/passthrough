using System.Drawing;
using System.Reflection;

namespace PassthroughGh
{
    /// <summary>
    /// Loads a component icon from an embedded PNG. The three bench components share
    /// this so the manifest-name convention lives in one place: each PNG ships under
    /// Resources\ and is embedded as PassthroughGh.Resources.&lt;filename&gt; (see the
    /// EmbeddedResource item in the csproj and the RootNamespace it matches). The
    /// stream is opened, read into the bitmap, and disposed; the bitmap is the icon
    /// Grasshopper draws in place of the default checkerboard.
    /// </summary>
    internal static class IconLoader
    {
        public static Bitmap Load(string fileName)
        {
            Assembly asm = typeof(IconLoader).Assembly;
            using var stream = asm.GetManifestResourceStream("PassthroughGh.Resources." + fileName);
            // A missing icon must never take down a working component. When the embedded
            // resource is absent the stream is null; return null so Grasshopper falls back
            // to its default icon instead of the Bitmap constructor throwing on a null
            // stream. See plugin/README.md for how to list the embedded resource names and
            // confirm whether the PNGs were embedded at build time.
            return stream == null ? null : new Bitmap(stream);
        }
    }
}
