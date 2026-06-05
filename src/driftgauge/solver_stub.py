"""The solver side of the exchange. The far side, behind the file contract.

See specs/spec-02-exchange.md and AGENT.md ("The two seams"). This is a separate
entry point from the geometry side so the process boundary is genuine, not a
function call dressed up as one.

Honesty category 1: instrument logic, authored here.

Named boundaries surfaced in this module:
    - The solver is synthetic. No CFD, ever (AGENT.md hard do-not list). This is
      the real-CFD seam: where a real solver would connect, honoring this same
      file contract. A real solver does not exist here and visibly does not.
    - The exchange is files. No server, no socket, no port.

Two modes share the contract and both stay:
    - Identity mode (Phase 1): return the mesh unchanged. It isolates the
      exchange from the fit, so the exchange-identity gate stays meaningful. Used
      by the Phase 1 exchange-identity gate.
    - Synthetic mode (Phase 3): apply a smooth, deterministic, normal-direction
      displacement field scaled by a single magnitude parameter, and write the
      deformed mesh back. The field is a stand-in for what a flow solve would do
      to a wing surface. It is not physics. It is a fixed analytic function of
      surface position, so the drift it produces is a known stimulus.

Why the displacement field is built from the mesh, not from a surface: the far
side receives only what the contract carries, a mesh (vertices, faces, uv) and a
descriptor. It does not get the source NurbsSurface. So the surface normal it
deforms along is derived from the mesh's own face geometry. A real solver behind
this same contract would be in the same position: it acts on the mesh it is
handed.

Determinism: the field uses no randomness. Given the same mesh and the same
magnitude, the deformed vertices are identical on repeat (asserted in
tests/test_loop.py). Determinism is the point; it makes the deformation a known
stimulus rather than noise.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from driftgauge.encode import mesh_filename, read_payload, write_payload
from driftgauge.geometry import Mesh

# --- the synthetic field --------------------------------------------------
#
# A fixed pressure-like bump. It is a stand-in for the surface pressure a flow
# solver would compute, not a computed pressure. The shape is fixed; only the
# single magnitude parameter scales it. The bump is a 2D Gaussian in the surface
# parameters, peaked over part of the chord near the leading edge (the region a
# suction peak would sit) and tapering across the span. u runs chordwise, v
# spanwise (the spec-01 convention).
#
# It varies in both u and v on purpose. Varying in u puts the peak over part of
# the chord, the wing-relevant shape. Varying in v means it also bends a ruling
# that runs along v, which is what the budget-caught gate needs: a deformation
# that bows a ruled input off ruled.

BUMP_CENTER_U = 0.25
BUMP_CENTER_V = 0.50
BUMP_WIDTH_U = 0.18
BUMP_WIDTH_V = 0.35


def pressure_bump(uv: np.ndarray) -> np.ndarray:
    """The fixed analytic shape of the synthetic field, evaluated per vertex.

    Returns a scalar in (0, 1] for each (u, v): a smooth Gaussian peaked at
    (BUMP_CENTER_U, BUMP_CENTER_V). This is the stand-in for a pressure field. It
    is deterministic and depends only on the surface parameters that travel with
    the mesh, so the same parameterization always yields the same shape.
    """
    uv = np.asarray(uv, dtype=float)
    du = (uv[:, 0] - BUMP_CENTER_U) / BUMP_WIDTH_U
    dv = (uv[:, 1] - BUMP_CENTER_V) / BUMP_WIDTH_V
    return np.exp(-(du * du + dv * dv))


def surface_normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Per-vertex unit normals from the mesh's own face geometry.

    The solver side has the mesh, not the surface, so the normal it deforms along
    is computed here. Each quad face (v00, v10, v11, v01) carries a normal from
    cross(edge_u, edge_v), where edge_u runs in +u (v00 -> v10) and edge_v in +v
    (v00 -> v01), matching the winding geometry.tessellate writes. The
    unnormalized face normals are accumulated to their vertices (area weighting
    falls out of using the raw cross product) and then normalized. The accumulation
    order is fixed by the face array, so the result is deterministic.
    """
    vertices = np.asarray(vertices, dtype=float)
    faces = np.asarray(faces, dtype=int)
    vn = np.zeros_like(vertices)
    for f in faces:
        p0 = vertices[f[0]]
        edge_u = vertices[f[1]] - p0
        edge_v = vertices[f[-1]] - p0
        fn = np.cross(edge_u, edge_v)
        for idx in f:
            vn[idx] += fn
    lengths = np.linalg.norm(vn, axis=1, keepdims=True)
    lengths[lengths < 1e-15] = 1.0
    return vn / lengths


def displacement(mesh: Mesh, magnitude: float) -> np.ndarray:
    """The (N, 3) displacement applied to the mesh: magnitude * bump * normal.

    Smooth (a Gaussian times a smooth normal field), normal-directed, and scaled
    by the single magnitude parameter. At magnitude 0 the displacement is exactly
    zero, so synthetic mode reduces to identity and the loop reduces to the
    fit-only case.
    """
    normals = surface_normals(mesh.vertices, mesh.faces)
    scale = float(magnitude) * pressure_bump(mesh.uv)
    return scale[:, None] * normals


def deform(mesh: Mesh, magnitude: float) -> Mesh:
    """Return a new mesh with the synthetic displacement applied.

    The uv array and faces ride along unchanged: the deformation moves vertices
    along the surface normal and does not touch the parameterization or the
    connectivity. The deformed mesh is the new target the reconstruction is asked
    to match.
    """
    moved = mesh.vertices + displacement(mesh, magnitude)
    return Mesh(vertices=moved, faces=mesh.faces.copy(), uv=mesh.uv.copy())


# --- the two entry-point modes --------------------------------------------


def solve_identity(out_dir: str | Path, in_dir: str | Path, index: int) -> Path:
    """Identity solver for one iteration.

    Reads exchange/out/mesh_NNN.json, writes the same mesh to
    exchange/in/mesh_NNN.json, preserving UV and descriptor. The returned mesh is
    unchanged, so the boundary crossing adds nothing. This isolates the exchange
    from the fit and keeps the Phase 1 exchange-identity gate meaningful.
    """
    src = Path(out_dir) / mesh_filename(index)
    dst = Path(in_dir) / mesh_filename(index)
    mesh, descriptor = read_payload(src)
    # Identity: return the mesh exactly as received. UV and descriptor ride along
    # so the returned mesh stays tied to its parameterization and its budget.
    return write_payload(dst, mesh, descriptor)


def solve_synthetic(
    out_dir: str | Path, in_dir: str | Path, index: int, magnitude: float
) -> Path:
    """Synthetic solver for one iteration.

    Reads exchange/out/mesh_NNN.json, applies the smooth deterministic
    normal-direction displacement field scaled by magnitude, and writes the
    deformed mesh to exchange/in/mesh_NNN.json. The UV array and the descriptor
    are preserved, so the returned mesh stays tied to its parameterization and its
    declared budget.

    The field is synthetic. It is the seam where a real solver would connect; a
    real solver would read this same payload and return a deformed mesh through
    this same file. No CFD runs here and none ever will (AGENT.md hard do-not
    list).
    """
    src = Path(out_dir) / mesh_filename(index)
    dst = Path(in_dir) / mesh_filename(index)
    mesh, descriptor = read_payload(src)
    deformed = deform(mesh, magnitude)
    return write_payload(dst, deformed, descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="driftgauge solver side. Identity or synthetic mode."
    )
    parser.add_argument("--out-dir", default="exchange/out")
    parser.add_argument("--in-dir", default="exchange/in")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument(
        "--mode",
        choices=("identity", "synthetic"),
        default="identity",
        help="identity returns the mesh unchanged; synthetic applies the bump.",
    )
    parser.add_argument(
        "--magnitude",
        type=float,
        default=0.0,
        help="synthetic mode only: the single scale on the fixed bump field.",
    )
    args = parser.parse_args(argv)

    if args.mode == "identity":
        dst = solve_identity(args.out_dir, args.in_dir, args.index)
        print(f"solver_stub identity wrote {dst}")
    else:
        dst = solve_synthetic(
            args.out_dir, args.in_dir, args.index, args.magnitude
        )
        print(
            f"solver_stub synthetic (magnitude {args.magnitude}) wrote {dst}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
