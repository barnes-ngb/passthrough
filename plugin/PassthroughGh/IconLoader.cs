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
            return new Bitmap(stream);
        }
    }
}
