"""Reconstruction seam for driftgauge.

This module holds the bounded reconstructor and the architected-but-unbuilt
learned seam. See AGENT.md ("The two seams") and PLAN.md Phase 1.

Honesty categories (AGENT.md):
    - ClassicalReconstructor is category 2. Least-squares NURBS fitting is a
      known method. It was not invented here. It is integrated and validated
      against synthetic ground truth, where the answer is known exactly.
    - LearnedReconstructor is category 3. The seam is architected and
      documented. It is not built. Do not implement it.

Named boundary surfaced in this module:
    The reconstructor consumes the per-vertex UV that travels with the mesh and
    uses it directly as the fit parameterization. It does not solve for a
    parameterization. That fixed UV is the parameterization-gap boundary. A real
    reverse-engineering pipeline has to recover the parameterization; driftgauge
    assumes it, names the assumption, and measures within it.

rhino3dm capability note:
    rhino3dm evaluates NURBS (PointAt) and holds control nets and knot lists,
    but it does not expose basis-function values. The Cox-de Boor basis is
    therefore authored here in numpy. It is validated to match rhino3dm's own
    evaluation to floating-point tolerance (see tests/test_reconstruct.py), so
    control points fit against this basis evaluate correctly when the surface is
    handed back to rhino3dm.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np
import rhino3dm as r3
from scipy.linalg import lstsq


# --- Cox-de Boor basis, authored in numpy ---------------------------------


def clamped_uniform_knots(degree: int, n_ctrl: int) -> np.ndarray:
    """Full clamped-uniform knot vector of length n_ctrl + degree + 1.

    The ends are clamped (degree + 1 repeats) so the surface interpolates its
    corner control points and the domain is [0, 1]. Interior knots are evenly
    spaced. This is the standard ("full") convention. rhino3dm stores the same
    vector with the outermost knot at each end dropped; convert when handing a
    knot vector to rhino3dm (see _make_surface).
    """
    if n_ctrl < degree + 1:
        raise ValueError("n_ctrl must be at least degree + 1")
    n_interior = n_ctrl - degree - 1
    interior = (
        []
        if n_interior <= 0
        else list(np.linspace(0.0, 1.0, n_interior + 2)[1:-1])
    )
    return np.array([0.0] * (degree + 1) + interior + [1.0] * (degree + 1))


def basis_matrix_1d(
    ts: np.ndarray, degree: int, knots: np.ndarray, n_ctrl: int
) -> np.ndarray:
    """Evaluate all n_ctrl basis functions at each parameter in ts.

    Returns an (len(ts), n_ctrl) array. Standard Cox-de Boor recursion. The
    right endpoint t = 1 is folded into the last non-degenerate span, which for
    a clamped knot vector is basis index n_ctrl - 1.
    """
    ts = np.clip(np.asarray(ts, dtype=float), 0.0, 1.0)
    m = len(knots) - 1
    N = np.zeros((ts.shape[0], m))
    for i in range(m):
        N[:, i] = ((knots[i] <= ts) & (ts < knots[i + 1])).astype(float)
    end = ts >= knots[-1]
    N[end, :] = 0.0
    N[end, n_ctrl - 1] = 1.0
    for p in range(1, degree + 1):
        nxt = np.zeros((ts.shape[0], m))
        for i in range(m - p):
            d1 = knots[i + p] - knots[i]
            if d1 > 0:
                nxt[:, i] += (ts - knots[i]) / d1 * N[:, i]
            d2 = knots[i + p + 1] - knots[i + 1]
            if d2 > 0:
                nxt[:, i] += (knots[i + p + 1] - ts) / d2 * N[:, i + 1]
        N = nxt
    return N[:, :n_ctrl]


def basis_derivatives_1d(
    ts: np.ndarray, degree: int, knots: np.ndarray, n_ctrl: int, n_deriv: int
) -> list[np.ndarray]:
    """Basis functions and their parametric derivatives up to order n_deriv.

    Returns a list [N0, N1, ..., N_{n_deriv}], each an (len(ts), n_ctrl) array.
    N0 equals basis_matrix_1d (the values). N1 is the first derivative of each
    basis function with respect to t, N2 the second, and so on.

    This extends the Cox-de Boor basis validated in Phase 1. It is the same basis;
    only the derivative recurrence is added here, so there is one validated basis
    driving evaluation, fitting, and differentiation. The derivatives are
    cross-checked against finite differences of rhino3dm's PointAt before being
    relied on for curvature (see tests/test_constraints.py).

    The derivative recurrence is the standard one:
        d/dt N_{i,p}(t) = p * ( N_{i,p-1}(t) / (knots[i+p] - knots[i])
                              - N_{i+1,p-1}(t) / (knots[i+p+1] - knots[i+1]) )
    Applying it k times lowers the degree by k. Each requested basis function is
    expanded into a linear combination of lower-degree basis values, which are
    computed once and shared.
    """
    ts = np.clip(np.asarray(ts, dtype=float), 0.0, 1.0)
    m = len(knots) - 1

    # Degree-0 indicator, with the right endpoint t = 1 folded into the last
    # non-degenerate span (basis index n_ctrl - 1), matching basis_matrix_1d.
    N0 = np.zeros((ts.shape[0], m))
    for i in range(m):
        N0[:, i] = ((knots[i] <= ts) & (ts < knots[i + 1])).astype(float)
    end = ts >= knots[-1]
    N0[end, :] = 0.0
    N0[end, n_ctrl - 1] = 1.0

    # Basis values at every degree 0..degree, kept for the derivative expansion.
    values_by_degree = {0: N0}
    prev = N0
    for p in range(1, degree + 1):
        ncol = m - p
        cur = np.zeros((ts.shape[0], ncol))
        for i in range(ncol):
            d1 = knots[i + p] - knots[i]
            if d1 > 0:
                cur[:, i] += (ts - knots[i]) / d1 * prev[:, i]
            d2 = knots[i + p + 1] - knots[i + 1]
            if d2 > 0:
                cur[:, i] += (knots[i + p + 1] - ts) / d2 * prev[:, i + 1]
        values_by_degree[p] = cur
        prev = cur

    def derivative(order: int) -> np.ndarray:
        result = np.zeros((ts.shape[0], n_ctrl))
        for i in range(n_ctrl):
            # Expand the order-th derivative of basis i into a combination of
            # degree (degree - order) basis values.
            combo = {i: 1.0}
            pp = degree
            for _ in range(order):
                nxt: dict[int, float] = {}
                for idx, c in combo.items():
                    a = knots[idx + pp] - knots[idx]
                    if a > 0:
                        nxt[idx] = nxt.get(idx, 0.0) + c * pp / a
                    b = knots[idx + pp + 1] - knots[idx + 1]
                    if b > 0:
                        nxt[idx + 1] = nxt.get(idx + 1, 0.0) - c * pp / b
                combo = nxt
                pp -= 1
            base = values_by_degree[pp]
            for idx, c in combo.items():
                if idx < base.shape[1]:
                    result[:, i] += c * base[:, idx]
        return result

    return [derivative(k) for k in range(n_deriv + 1)]


def tensor_basis_matrix(
    uv: np.ndarray,
    degree_u: int,
    degree_v: int,
    knots_u: np.ndarray,
    knots_v: np.ndarray,
    n_ctrl_u: int,
    n_ctrl_v: int,
) -> np.ndarray:
    """Surface basis matrix B with B[k, i*n_ctrl_v + j] = Nu_i(u_k) * Nv_j(v_k).

    Column order is the row-major flatten of the (n_ctrl_u, n_ctrl_v) control
    net, so a fitted column vector reshapes straight back to Points[i, j].
    """
    uv = np.asarray(uv, dtype=float)
    bu = basis_matrix_1d(uv[:, 0], degree_u, knots_u, n_ctrl_u)
    bv = basis_matrix_1d(uv[:, 1], degree_v, knots_v, n_ctrl_v)
    return (bu[:, :, None] * bv[:, None, :]).reshape(uv.shape[0], n_ctrl_u * n_ctrl_v)


def _make_surface(
    ctrl: np.ndarray,
    degree_u: int,
    degree_v: int,
    knots_u: np.ndarray,
    knots_v: np.ndarray,
) -> r3.NurbsSurface:
    """Build a rhino3dm NurbsSurface from a fitted control net (n_u, n_v, 3).

    Weights are 1 (non-rational). rhino3dm's knot list omits the outermost knot
    at each end, so the full knot vector is handed over without its first and
    last entries.
    """
    n_u, n_v = ctrl.shape[0], ctrl.shape[1]
    ns = r3.NurbsSurface.Create(3, False, degree_u + 1, degree_v + 1, n_u, n_v)
    for i in range(n_u):
        for j in range(n_v):
            x, y, z = ctrl[i, j]
            ns.Points[i, j] = r3.Point4d(float(x), float(y), float(z), 1.0)
    for idx, val in enumerate(knots_u[1:-1]):
        ns.KnotsU[idx] = float(val)
    for idx, val in enumerate(knots_v[1:-1]):
        ns.KnotsV[idx] = float(val)
    ns.SetDomain(0, r3.Interval(0.0, 1.0))
    ns.SetDomain(1, r3.Interval(0.0, 1.0))
    return ns


# --- portable analytic form (spec-07-surface) -----------------------------

SURFACE_FORMAT = "passthrough.surface.v1"


def nurbs_surface_to_dict(ns: r3.NurbsSurface) -> dict:
    """Extract a NurbsSurface into the portable analytic form of spec-07-surface.

    The schema (passthrough.surface.v1) is the control net, the weights, the two
    degrees, and the two full knot vectors. That is enough to rebuild the surface
    verbatim, in rhino3dm here or in RhinoCommon on the Grasshopper bench, with no
    fitting and no geometry math. The analytic surface is reconstructed once, in
    Python (ClassicalReconstructor), and carried as data so the bench rebuilds it
    rather than recomputing it.

    Knot convention: this writes the full clamped knot vector (length
    n_ctrl + degree + 1), the same convention clamped_uniform_knots uses, so a
    reader can check the lengths without knowing the library. rhino3dm and
    RhinoCommon both store the knot list with the outermost knot at each end
    dropped, so the first and last entries are restored here; a rebuilder drops them
    again, exactly as _make_surface does.

    Control points are euclidean (x, y, z), not weighted homogeneous coordinates.
    The classical reconstructor is non-rational, so every weight is 1.0; the weights
    grid is carried explicitly anyway so a rational fit could ride the same contract
    without a schema change.
    """
    pts = ns.Points
    n_u, n_v = pts.CountU, pts.CountV
    degree_u, degree_v = ns.OrderU - 1, ns.OrderV - 1

    control = [[[0.0, 0.0, 0.0] for _ in range(n_v)] for _ in range(n_u)]
    weights = [[1.0 for _ in range(n_v)] for _ in range(n_u)]
    for i in range(n_u):
        for j in range(n_v):
            p = pts[i, j]
            control[i][j] = [float(p.X), float(p.Y), float(p.Z)]
            weights[i][j] = float(p.W)

    def full_knots(klist) -> list[float]:
        # rhino3dm drops the outermost knot at each end; restore them to the full
        # clamped vector of length n_ctrl + degree + 1.
        vals = [float(klist[i]) for i in range(len(klist))]
        return [vals[0]] + vals + [vals[-1]]

    return {
        "format": SURFACE_FORMAT,
        "degree_u": int(degree_u),
        "degree_v": int(degree_v),
        "count_u": int(n_u),
        "count_v": int(n_v),
        "control_points": control,
        "weights": weights,
        "knots_u": full_knots(ns.KnotsU),
        "knots_v": full_knots(ns.KnotsV),
    }


def nurbs_surface_from_dict(obj: dict) -> r3.NurbsSurface:
    """Rebuild a NurbsSurface from the spec-07-surface form. The inverse of
    nurbs_surface_to_dict.

    This is the verbatim rebuild the C# Import component performs: it takes the
    carried control net, degrees, and full knot vectors and reconstructs the surface
    with no fitting. Provided here so the Python tests can prove the carried data is
    sufficient, the same path the bench follows in RhinoCommon. Weights are 1
    (the v1 surface is non-rational), so _make_surface is reused directly.
    """
    ctrl = np.asarray(obj["control_points"], dtype=float)
    knots_u = np.asarray(obj["knots_u"], dtype=float)
    knots_v = np.asarray(obj["knots_v"], dtype=float)
    return _make_surface(
        ctrl, int(obj["degree_u"]), int(obj["degree_v"]), knots_u, knots_v
    )


# --- The seam: protocol and implementations -------------------------------


@runtime_checkable
class Reconstructor(Protocol):
    """The reconstruction seam. One method, narrow and stable on purpose.

    reconstruct takes mesh points and the per-vertex UV that traveled with them
    and returns a NurbsSurface. This is the only contract the rest of the
    instrument depends on, so the bounded classical fit and a future learned map
    are one swap apart.
    """

    def reconstruct(self, points: np.ndarray, uv: np.ndarray) -> r3.NurbsSurface:
        ...


class ClassicalReconstructor:
    """Least-squares NURBS fit on fixed knot vectors and fixed parameterization.

    Honesty category 2: known method, integrated and validated here.

    The reconstructor commits up front to a control resolution (n_ctrl_u,
    n_ctrl_v), a degree, and clamped-uniform knot vectors. That fixed spline
    space is the bounded assumption. It does not adapt knots to the data and it
    does not solve for the parameterization. It takes the supplied UV as the
    parameterization directly (the parameterization-gap boundary, see module
    docstring) and solves one linear least-squares system per coordinate for the
    control-point positions:

        minimize || B p - x ||   for x in {X, Y, Z}

    where B is the tensor-product basis matrix evaluated at the supplied UV. The
    system is linear because the knots and parameters are fixed; only the
    control points are unknown.

    When the fixed spline space happens to contain the source surface, the fit
    recovers it to floating-point tolerance. When it does not (a coarser control
    count or different knot placement than the source), the fit returns the best
    L2 projection at the sampled points and the unrepresented part of the source
    shows up as a small, explainable drift. That gap is the subject of the
    Phase 1 honest-fit gate.
    """

    def __init__(
        self,
        n_ctrl_u: int,
        n_ctrl_v: int,
        degree_u: int = 3,
        degree_v: int = 3,
    ) -> None:
        self.n_ctrl_u = n_ctrl_u
        self.n_ctrl_v = n_ctrl_v
        self.degree_u = degree_u
        self.degree_v = degree_v
        self.knots_u = clamped_uniform_knots(degree_u, n_ctrl_u)
        self.knots_v = clamped_uniform_knots(degree_v, n_ctrl_v)

    def reconstruct(self, points: np.ndarray, uv: np.ndarray) -> r3.NurbsSurface:
        points = np.asarray(points, dtype=float)
        uv = np.asarray(uv, dtype=float)
        if points.shape[0] != uv.shape[0]:
            raise ValueError("points and uv must have the same length")

        B = tensor_basis_matrix(
            uv,
            self.degree_u,
            self.degree_v,
            self.knots_u,
            self.knots_v,
            self.n_ctrl_u,
            self.n_ctrl_v,
        )
        # One linear least-squares solve, three right-hand sides (X, Y, Z).
        ctrl_flat, _, _, _ = lstsq(B, points)
        ctrl = ctrl_flat.reshape(self.n_ctrl_u, self.n_ctrl_v, 3)
        return _make_surface(
            ctrl, self.degree_u, self.degree_v, self.knots_u, self.knots_v
        )


class LearnedReconstructor:
    """Documented stub for the learned differentiable reconstructor.

    Honesty category 3: the seam is architected, the implementation is not
    built. Do not implement it (AGENT.md hard do-not list).

    What it would do, if built: predict parametric surface coefficients
    (control points, and in a fuller version knots and weights) directly from
    mesh input, trained to minimize round-trip drift under the declared
    constraints in the descriptor. Being differentiable, it would yield
    per-parameter sensitivities, which is what an optimization loop needs and
    what the classical fit cannot give. This is the gradient-transfer boundary
    named in AGENT.md: the place a learned reconstructor enters. It honors the
    same Reconstructor protocol so it is a drop-in swap for ClassicalReconstructor.
    """

    def reconstruct(self, points: np.ndarray, uv: np.ndarray) -> r3.NurbsSurface:
        raise NotImplementedError(
            "LearnedReconstructor is an architected seam, not built. "
            "See the class docstring and AGENT.md honesty category 3."
        )
