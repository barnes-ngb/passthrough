using System;
using System.Drawing;

namespace PassthroughGh
{
    /// <summary>
    /// A viridis-family color ramp. This is an analytic approximation of matplotlib's
    /// viridis (the polynomial fit by Matt Zucker, shadertoy WlfXRN), not the exact
    /// 256-stop table. It is the same color family the static render uses, which is
    /// what the kickoff asks for: the import colors by deviation in viridis so the
    /// canvas matches the slides-only render closely without recomputing anything.
    /// </summary>
    internal static class Colormap
    {
        // Polynomial coefficients, one triple (r, g, b) per power of t.
        private static readonly double[] C0 = { 0.2777273272234177, 0.005407344544966578, 0.3340998053353061 };
        private static readonly double[] C1 = { 0.1050930431085774, 1.404613529898575, 1.384590162594685 };
        private static readonly double[] C2 = { -0.3308618287255563, 0.214847559468213, 0.09509516302823659 };
        private static readonly double[] C3 = { -4.634230498983486, -5.799100973351585, -19.33244095627987 };
        private static readonly double[] C4 = { 6.228269936347081, 14.17993336680509, 56.69055260068105 };
        private static readonly double[] C5 = { 4.776384997670288, -13.74514537774601, -65.35303263337234 };
        private static readonly double[] C6 = { -5.435455855934631, 4.645852612178535, 26.3124352495832 };

        /// <summary>Map a scalar in [0, 1] to a viridis color. Values outside are clamped.</summary>
        public static Color Viridis(double t)
        {
            if (double.IsNaN(t)) t = 0.0;
            t = Math.Clamp(t, 0.0, 1.0);

            int r = Channel(0, t);
            int g = Channel(1, t);
            int b = Channel(2, t);
            return Color.FromArgb(r, g, b);
        }

        private static int Channel(int k, double t)
        {
            // Horner evaluation of the degree-6 polynomial for one channel.
            double v = C0[k] + t * (C1[k] + t * (C2[k] + t * (C3[k] + t * (C4[k] + t * (C5[k] + t * C6[k])))));
            int c = (int)Math.Round(Math.Clamp(v, 0.0, 1.0) * 255.0);
            return Math.Clamp(c, 0, 255);
        }
    }
}
