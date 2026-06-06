"""The loss budget, made executable. See specs/spec-03-metrics-constraints.md.

Honesty category 1: this is instrument logic, authored here.

The descriptor declares what the translation may not lose. This module verifies
it. Declaration and verification share one source of truth: the descriptor's
`constraints` list. The checker reads each constraint's type and tolerance from
the descriptor, computes the matching residual against the returned geometry, and
reports the residual next to the declared tolerance with a pass/fail flag. No
tolerance is hardcoded here.

A residual is the degree of constraint violation. It is kept semantically
distinct from positional drift (the Hausdorff number in driftgauge.py). This module
returns residuals only. It does not compute or fold in drift.

Two constraint types are defined this phase:
    - ruled:     straight rulings preserved. The clean binary unit.
    - curvature: the source curvature field preserved. The wing headline, the
                 property a flow solver is sensitive to.

Named boundaries surfaced in this module:
    - Non-rational surfaces only. The curvature math reads control points
      directly and assumes all weights are 1. The source surface and every
      classical reconstruction are non-rational, so this holds. A rational
      surface would need the weighted form; that is a named bound, not a defect.
    - Rulings lie on isoparametric curves. True for the fixtures by construction.
      The ruled checker assumes it. A general ruled surface whose rulings cross
      the parameter lines is a known extension, not a hidden assumption.

rhino3dm capability note:
    rhino3dm exposes PointAt, NormalAt, FrameAt, and IsoCurve, but no curvature
    and no parametric derivatives. Curvature is therefore computed in numpy from
    derivatives authored by extending the Phase 1 Cox-de Boor basis. The
    derivatives and the resulting curvature are cross-checked against finite
    differences of PointAt before being trusted (tests/test_constraints.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import rhino3dm as r3

from passthrough.encode import Descriptor
from passthrough.reconstruct import basis_derivatives_1d


# --- result type ----------------------------------------------------------


@dataclass
class ConstraintResult:
    """One declared constraint, checked.

    type:      the constraint name, copied from the descriptor.
    tolerance: the declared tolerance, copied from the descriptor. The checker
               does not own this number; the budget does.
    residual:  the raw headline residual, in the constraint's own units. Reported
               whether or not it passes, so the margin is visible.
    passed:    residual <= tolerance.
    detail:    secondary numbers (mean, leading-edge max, Gaussian, and so on).
               Reported, never used for pass/fail.
    """

    type: str
    tolerance: float
    residual: float
    passed: bool
    detail: dict[str, float] = field(default_factory=dict)


# --- surface derivatives, authored in numpy -------------------------------


def _full_knots(knot_list: Any) -> np.ndarray:
    """Recover the full clamped knot vector from a rhino3dm knot list.

    rhino3dm stores the knot list with the outermost knot at each end dropped, so
    the full vector (length n_ctrl + degree + 1) is recovered by duplicating each
    end. See reconstruct._make_surface for the inverse of this.
    """
    k = [knot_list[i] for i in range(len(knot_list))]
    return np.array([k[0]] + k + [k[-1]], dtype=float)


def _control_net_xyz(surface: r3.NurbsSurface) -> np.ndarray:
    """Return the (n_u, n_v, 3) control net positions.

    Asserts the surface is non-rational. The curvature math assumes weights are 1
    (the non-rational bound stated in the module docstring).
    """
    if surface.IsRational:
        raise ValueError(
            "curvature is defined here for non-rational surfaces only "
            "(the non-rational bound; see module docstring)"
        )
    n_u, n_v = surface.Points.CountU, surface.Points.CountV
    net = np.empty((n_u, n_v, 3))
    for i in range(n_u):
        for j in range(n_v):
            p = surface.Points.GetControlPoint(i, j)
            net[i, j] = (p.X, p.Y, p.Z)
    return net


def surface_derivatives(
    surface: r3.NurbsSurface, us: np.ndarray, vs: np.ndarray
) -> dict[str, np.ndarray]:
    """Evaluate the surface and its first and second parametric derivatives.

    us and vs are equal-length parameter arrays; the surface is evaluated at the
    paired (us[k], vs[k]). Returns S, Su, Sv, Suu, Suv, Svv, each (len, 3).

    Degree, control net, and the full knot vectors are read from the surface
    itself, so this works for any non-rational NurbsSurface (the source, a ruled
    fixture, a reconstruction), not only the reconstructor's uniform-knot fits.
    """
    us = np.asarray(us, dtype=float)
    vs = np.asarray(vs, dtype=float)
    degree_u, degree_v = surface.OrderU - 1, surface.OrderV - 1
    net = _control_net_xyz(surface)
    n_u, n_v = net.shape[0], net.shape[1]
    knots_u = _full_knots(surface.KnotsU)
    knots_v = _full_knots(surface.KnotsV)

    bu = basis_derivatives_1d(us, degree_u, knots_u, n_u, 2)
    bv = basis_derivatives_1d(vs, degree_v, knots_v, n_v, 2)

    def comb(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        # S^(a,b)[k] = sum_ij a[k,i] b[k,j] P[i,j]
        return np.einsum("ti,tj,ijk->tk", a, b, net)

    return {
        "S": comb(bu[0], bv[0]),
        "Su": comb(bu[1], bv[0]),
        "Sv": comb(bu[0], bv[1]),
        "Suu": comb(bu[2], bv[0]),
        "Suv": comb(bu[1], bv[1]),
        "Svv": comb(bu[0], bv[2]),
    }


def curvature_field(
    surface: r3.NurbsSurface, us: np.ndarray, vs: np.ndarray
) -> dict[str, np.ndarray]:
    """Mean and Gaussian curvature at the paired (us, vs) parameters.

    Mean H = (E N - 2 F M + G L) / (2 (E G - F^2)).
    Gaussian K = (L N - M^2) / (E G - F^2).
    E, F, G are the first fundamental form; L, M, N the second, against the unit
    normal n = (Su x Sv) / |Su x Sv|. Returns {"H": ..., "K": ...}.
    """
    d = surface_derivatives(surface, us, vs)
    Su, Sv = d["Su"], d["Sv"]
    Suu, Suv, Svv = d["Suu"], d["Suv"], d["Svv"]

    normal = np.cross(Su, Sv)
    n_hat = normal / np.linalg.norm(normal, axis=1, keepdims=True)

    E = (Su * Su).sum(1)
    F = (Su * Sv).sum(1)
    G = (Sv * Sv).sum(1)
    L = (Suu * n_hat).sum(1)
    M = (Suv * n_hat).sum(1)
    N = (Svv * n_hat).sum(1)

    det = E * G - F * F
    H = (E * N - 2 * F * M + G * L) / (2 * det)
    K = (L * N - M * M) / det
    return {"H": H, "K": K}


# --- ruled residual -------------------------------------------------------


def _max_line_deviation(points: np.ndarray) -> float:
    """Maximum perpendicular distance of points from the chord through the ends."""
    p0, p1 = points[0], points[-1]
    chord = p1 - p0
    length = np.linalg.norm(chord)
    if length < 1e-12:
        # Degenerate ruling: deviation is the spread from the start point.
        return float(np.linalg.norm(points - p0, axis=1).max())
    direction = chord / length
    rel = points - p0
    proj = rel @ direction
    perp = rel - np.outer(proj, direction)
    return float(np.linalg.norm(perp, axis=1).max())


def _ruled_residual_one_direction(
    surface: r3.NurbsSurface, direction: int, n_rulings: int, n_samples: int
) -> float:
    """Max ruling deviation when rulings are taken along the given direction.

    A ruling is IsoCurve(direction, c). For each of n_rulings positions c spaced
    across [0, 1], the isocurve is sampled at n_samples points and its deviation
    from the chord through its endpoints is measured. The residual is the worst
    such deviation. IsoCurve is the natural extractor; the tests confirm it
    returns linear curves for a known ruled fixture before this relies on it.
    """
    positions = np.linspace(0.0, 1.0, n_rulings)
    worst = 0.0
    for c in positions:
        iso = surface.IsoCurve(direction, float(c))
        dom = iso.Domain
        ts = np.linspace(dom.T0, dom.T1, n_samples)
        pts = np.empty((n_samples, 3))
        for k, t in enumerate(ts):
            p = iso.PointAt(float(t))
            pts[k] = (p.X, p.Y, p.Z)
        worst = max(worst, _max_line_deviation(pts))
    return worst


def ruled_residual(
    surface: r3.NurbsSurface,
    direction: int | None = None,
    n_rulings: int = 9,
    n_samples: int = 11,
) -> float:
    """Ruled residual: max deviation of the ruling isocurves from straight lines.

    If direction is given (0 or 1), rulings are measured along it. If it is None,
    both directions are measured and the smaller residual is returned, because a
    surface is ruled if either parametric direction carries straight isocurves.
    A genuinely ruled surface reads a residual at floating-point zero.

    Units: length, in the surface's own units.
    """
    dirs = [direction] if direction is not None else [0, 1]
    return min(
        _ruled_residual_one_direction(surface, d, n_rulings, n_samples) for d in dirs
    )


# --- curvature residual ---------------------------------------------------


def curvature_residual(
    reconstruction: r3.NurbsSurface,
    source: r3.NurbsSurface,
    n_u: int = 25,
    n_v: int = 9,
    le_fraction: float = 0.15,
) -> dict[str, float]:
    """Deviation between the reconstruction and source curvature fields.

    Both surfaces are sampled on the same regular n_u by n_v grid on [0, 1]^2, so
    points correspond by parameter (the fixed-parameterization assumption the
    instrument already carries). The pointwise deviation is the absolute
    difference in mean curvature, |H_reconstruction - H_source|.

    Returns:
        max:          headline residual, the worst pointwise deviation. This is
                      what the declared tolerance is compared against.
        mean:         mean pointwise deviation, a typical-case reading.
        le_max:       worst deviation in the leading-edge region (u <= le_fraction),
                      where curvature is highest. u runs chordwise, LE at u = 0.
        gaussian_max: worst deviation of Gaussian curvature, for context only.

    Units: inverse length (one over a radius).
    """
    grid_u = np.linspace(0.0, 1.0, n_u)
    grid_v = np.linspace(0.0, 1.0, n_v)
    us = np.repeat(grid_u, n_v)
    vs = np.tile(grid_v, n_u)

    cr = curvature_field(reconstruction, us, vs)
    cs = curvature_field(source, us, vs)

    dH = np.abs(cr["H"] - cs["H"])
    dK = np.abs(cr["K"] - cs["K"])
    le = us <= le_fraction

    return {
        "max": float(dH.max()),
        "mean": float(dH.mean()),
        "le_max": float(dH[le].max()) if le.any() else float("nan"),
        "gaussian_max": float(dK.max()),
    }


# --- the checker ----------------------------------------------------------


def check_constraints(
    descriptor: Descriptor,
    reconstruction: r3.NurbsSurface,
    source: r3.NurbsSurface | None = None,
) -> list[ConstraintResult]:
    """Verify every constraint the descriptor declares.

    Iterates descriptor.constraints. For each, reads the type and tolerance from
    the descriptor (the single source of truth), computes the matching residual
    against the reconstruction (and the source, where the constraint compares the
    two), and returns a ConstraintResult with the raw residual beside the declared
    tolerance and a pass/fail flag.

    An unknown constraint type is an error: a budget that declares a property the
    checker cannot verify is a contract the checker must refuse, not skip.
    """
    results: list[ConstraintResult] = []
    for c in descriptor.constraints:
        ctype = c["type"]
        tolerance = float(c["tolerance"])

        if ctype == "ruled":
            residual = ruled_residual(reconstruction, direction=c.get("direction"))
            detail: dict[str, float] = {}

        elif ctype == "curvature":
            if source is None:
                raise ValueError(
                    "the 'curvature' constraint compares against the source "
                    "surface, which was not supplied"
                )
            res = curvature_residual(reconstruction, source)
            residual = res["max"]
            detail = res

        else:
            raise ValueError(
                f"unknown constraint type {ctype!r}. The checker refuses a "
                "declared property it cannot verify."
            )

        results.append(
            ConstraintResult(
                type=ctype,
                tolerance=tolerance,
                residual=residual,
                passed=residual <= tolerance,
                detail=detail,
            )
        )
    return results
