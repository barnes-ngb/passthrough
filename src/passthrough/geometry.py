"""Geometry foundation for driftgauge.

Builds the synthetic source surface and tessellates it. The source surface is
constructed by defining its control net directly, so every property is owned
and the ground truth is exact. See specs/spec-01-geometry.md.

Convention held everywhere:
    u = chordwise (around the section)
    v = spanwise  (along the wing)

Named boundaries surfaced in this module:
    - Single surface, not a multi-patch B-rep. This is the reverse-problem-proper
      boundary. driftgauge measures a single-surface round trip on purpose.
    - The per-vertex UV array produced here travels with the mesh. Carrying a
      fixed parameterization is the parameterization-gap boundary, named so the
      reconstructor can assume it rather than solve it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import rhino3dm as r3


@dataclass
class Mesh:
    """A tessellated surface.

    vertices: (N, 3) float array of evaluated surface points.
    faces:    (M, 4) int array of quad vertex indices.
    uv:       (N, 2) float array, one (u, v) per vertex. The parameterization
              that produced each vertex, carried with the mesh.
    """

    vertices: np.ndarray
    faces: np.ndarray
    uv: np.ndarray

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=float)
        self.faces = np.asarray(self.faces, dtype=int)
        self.uv = np.asarray(self.uv, dtype=float)
        if self.vertices.shape[0] != self.uv.shape[0]:
            raise ValueError("vertices and uv must have the same length")


# Base section profile in (chord, thickness). Rounded leading edge, tapering
# trailing edge, so curvature is high near the LE and a curvature-continuity
# constraint is a meaningful thing to preserve later.
_SECTION = np.array(
    [
        [0.00, 0.000],  # leading edge
        [0.02, 0.060],
        [0.15, 0.100],
        [0.45, 0.070],
        [0.75, 0.035],
        [0.92, 0.012],
        [0.98, 0.004],
        [1.00, 0.000],  # trailing edge
    ]
)

_SPAN_STATIONS = (0.0, 1.2, 2.4, 3.6)
_TAPERS = (1.0, 0.88, 0.74, 0.60)

_DEGREE_U = 3
_DEGREE_V = 3


def build_wing_surface() -> r3.NurbsSurface:
    """Construct the synthetic wing-section surface from a direct control net.

    Deterministic. Two calls return identical control nets. The surface is
    clamped in both directions, so its domain corners interpolate the corner
    control points, and the domain is the unit square in both u and v.
    """
    cv_u = len(_SECTION)
    cv_v = len(_SPAN_STATIONS)

    ns = r3.NurbsSurface.Create(3, False, _DEGREE_U + 1, _DEGREE_V + 1, cv_u, cv_v)
    for j, (y, taper) in enumerate(zip(_SPAN_STATIONS, _TAPERS)):
        for i, (chord, thick) in enumerate(_SECTION):
            ns.Points[i, j] = r3.Point4d(chord * taper, y, thick * taper, 1.0)

    ns.KnotsU.CreateUniformKnots(1.0)
    ns.KnotsV.CreateUniformKnots(1.0)
    ns.SetDomain(0, r3.Interval(0.0, 1.0))
    ns.SetDomain(1, r3.Interval(0.0, 1.0))
    return ns


def control_net(ns: r3.NurbsSurface) -> np.ndarray:
    """Return the (cv_u, cv_v, 4) control net as [x, y, z, w]. Used for the
    reproducibility check and, later, for serialization."""
    cu = ns.Points.CountU
    cv = ns.Points.CountV
    net = np.empty((cu, cv, 4))
    for i in range(cu):
        for j in range(cv):
            p = ns.Points.GetControlPoint(i, j)
            net[i, j] = [p.X, p.Y, p.Z, p.W]
    return net


def tessellate(ns: r3.NurbsSurface, n_u: int, n_v: int) -> Mesh:
    """Evaluate the surface on a regular (n_u x n_v) UV grid and build quad faces
    by grid connectivity. The regular grid is what keeps the parameterization
    explicit and the round trip honest.
    """
    if n_u < 2 or n_v < 2:
        raise ValueError("n_u and n_v must be at least 2")

    us = np.linspace(0.0, 1.0, n_u)
    vs = np.linspace(0.0, 1.0, n_v)

    verts = np.empty((n_u * n_v, 3))
    uv = np.empty((n_u * n_v, 2))
    for a, u in enumerate(us):
        for b, v in enumerate(vs):
            idx = a * n_v + b
            p = ns.PointAt(float(u), float(v))
            verts[idx] = (p.X, p.Y, p.Z)
            uv[idx] = (u, v)

    faces = []
    for a in range(n_u - 1):
        for b in range(n_v - 1):
            v00 = a * n_v + b
            v01 = a * n_v + (b + 1)
            v10 = (a + 1) * n_v + b
            v11 = (a + 1) * n_v + (b + 1)
            faces.append((v00, v10, v11, v01))

    return Mesh(vertices=verts, faces=np.array(faces, dtype=int), uv=uv)


def evaluate(ns: r3.NurbsSurface, u: float, v: float) -> np.ndarray:
    """Evaluate a single surface point. Thin wrapper for readable tests."""
    p = ns.PointAt(float(u), float(v))
    return np.array([p.X, p.Y, p.Z])
