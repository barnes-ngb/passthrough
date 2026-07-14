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

from passthrough.encode import (
    mesh_filename,
    read_payload,
    read_topology,
    write_payload,
)
from passthrough.geometry import Mesh

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


# --- Phase 4 morph modes: deterministic, known violations -----------------
#
# Three modes, each a known stimulus for one of the three validity signals
# (specs/spec-04-identity-validity.md). None renames a vertex or rewires a face,
# so identity is preserved by all three. The geometry is what each one breaks (or
# keeps clean), so the validity gate reads exactly one signal per mode.

# The small clean base each morph builds on, so a morph is a real deformation
# field and not a bare geometric trick.
CLEAN_MAGNITUDE = 0.02


def clean_morph(mesh: Mesh, magnitude: float = CLEAN_MAGNITUDE) -> Mesh:
    """A valid deformation. The Phase 3 smooth normal bump, small enough to pass
    all three validity checks. Reconstruction proceeds and drift is produced as in
    Phase 3."""
    return deform(mesh, magnitude)


def collision_morph(mesh: Mesh, closure: float = 1.0) -> Mesh:
    """Wrap the span into a loop so the two free span edges meet: a collision.

    The span coordinate (y, the spanwise axis) is bent into a circle in the (y, z)
    plane. At full closure the bend sweeps a complete turn, so the leading-edge
    corner at one span end lands exactly on the leading-edge corner at the other
    span end. Those two nodes are not topological neighbors (opposite ends of the
    span, no edge between them), so their coincidence is a genuine self-intersection
    the carried topology can catch.

    The bend is smooth, so no face inverts: this trips the collision check alone.
    Thickness (z) rides radially outward, so the airfoil sections sweep around the
    loop without renumbering or rewiring anything. closure scales the turn: 1.0
    closes it (the corners coincide); a value just under 1.0 is the near-miss that
    stops just short so the corners stay just clear of the tolerance.

    Determinism: a fixed analytic map of position. The same mesh and the same
    closure give identical vertices on repeat.
    """
    v = mesh.vertices
    y = v[:, 1]
    y_min = float(y.min())
    span = float(y.max() - y_min)
    radius = span / (2.0 * np.pi)
    phi = (2.0 * np.pi * closure) * (y - y_min) / span
    r = radius + v[:, 2]  # thickness rides radially
    moved = np.empty_like(v)
    moved[:, 0] = v[:, 0]  # chord stays the loop axis
    moved[:, 1] = r * np.sin(phi)
    moved[:, 2] = r * np.cos(phi)
    return Mesh(vertices=moved, faces=mesh.faces.copy(), uv=mesh.uv.copy())


def _interior_vertex_row(mesh: Mesh) -> int:
    """A deterministic interior vertex: the one whose UV is nearest (0.5, 0.5).

    Interior so it has faces on both sides to fold, and chosen by parameter so it
    is the same vertex for any tessellation of the same surface.
    """
    target = np.array([0.5, 0.5])
    return int(np.argmin(np.linalg.norm(mesh.uv - target, axis=1)))


def fold_morph(mesh: Mesh, reach: float = 2.5) -> Mesh:
    """Crease one interior vertex past its spanwise neighbor: a local fold.

    After the clean base bump, a single interior vertex is pushed along the edge to
    its next spanwise neighbor (the +v direction), reach times the length of that
    edge. With reach above two it overshoots the neighbor, so the two faces that
    share the vertex in v fold over and their orientation inverts against the
    carried winding, while the rest of the mesh stays put. This trips the fold check
    alone.

    The push is along the spanwise edge on purpose. Spanwise cells are coarse here,
    so the overshooting vertex lands well clear of any non-neighbor node (reach 2.5
    sits between the second and third nodes along the span), and the crease does not
    read as a collision. Pushing along the fine chordwise edge would instead land on
    the next chord node and entangle the two signals. Nothing is renumbered, so
    identity holds.

    Determinism: the vertex is chosen by parameter (nearest UV to the center) and
    the push is a fixed multiple of the spanwise edge length.
    """
    base = deform(mesh, CLEAN_MAGNITUDE)
    moved = base.vertices.copy()
    row = _interior_vertex_row(mesh)

    # The +v neighbor shares a spanwise edge with this vertex. The grid lays out
    # index = a * n_v + b with b the span index, so the +v neighbor is row + 1.
    neighbor = row + 1
    edge = base.vertices[neighbor] - base.vertices[row]
    moved[row] = base.vertices[row] + reach * edge
    return Mesh(vertices=moved, faces=base.faces.copy(), uv=base.uv.copy())


FFD_STRENGTH = 0.04  # handle displacement as a fraction of the bounding-box diagonal


def _bernstein(n: int, i: int, t: np.ndarray) -> np.ndarray:
    """The i-th Bernstein polynomial of degree n, evaluated at t in [0, 1]."""
    from math import comb

    return comb(n, i) * (t**i) * ((1.0 - t) ** (n - i))


def ffd_morph(mesh: Mesh, strength: float = FFD_STRENGTH) -> Mesh:
    """Free-form deformation (Sederberg-Parry): a trivariate Bernstein lattice over
    the mesh's bounding box, with a deterministic handle displacement standing in
    for a simulation-driven shape change.

    The lattice starts at the identity: control points sit on the regular grid of
    the box, and by Bernstein partition-of-unity and linear precision the blend
    reproduces every vertex exactly. The deformation is a cantilever-style spanwise
    bend with washout twist: sections lift quadratically from root to tip and
    rotate slightly, the shape a wing takes under aerodynamic load.

    Two properties matter to the contract. The map acts on space, so identity,
    topology, and uv are untouched by construction: this is the morph where
    parameterization survives for free and all the interesting deviation is
    representation (curvature), not correspondence. And the displacement scales
    with the bounding-box diagonal, so the same relative deformation appears at
    unit scale and at drawing scale alike.
    """
    v = mesh.vertices
    lo, hi = v.min(axis=0), v.max(axis=0)
    span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
    local = (v - lo) / span  # (s, t, u) in the unit cube
    diag = float(np.linalg.norm(hi - lo))

    deg_s, deg_t, deg_u = 3, 2, 1  # lattice of 4 x 3 x 2 control points
    lattice = np.zeros((deg_s + 1, deg_t + 1, deg_u + 1, 3))
    for i in range(deg_s + 1):
        for j in range(deg_t + 1):
            for k in range(deg_u + 1):
                lattice[i, j, k] = lo + span * np.array(
                    [i / deg_s, j / deg_t, k / deg_u]
                )

    d = strength * diag
    for i in range(deg_s + 1):
        ramp = (i / deg_s) ** 2  # cantilever-like: zero at the root, full at the tip
        lattice[i, :, :, 2] += d * ramp  # spanwise bend, whole section lifts
        lattice[i, 0, :, 1] += 0.3 * d * ramp  # washout twist: leading edge aside
        lattice[i, deg_t, :, 1] -= 0.3 * d * ramp  # trailing edge opposite

    basis_s = np.stack(
        [_bernstein(deg_s, i, local[:, 0]) for i in range(deg_s + 1)], axis=1
    )
    basis_t = np.stack(
        [_bernstein(deg_t, j, local[:, 1]) for j in range(deg_t + 1)], axis=1
    )
    basis_u = np.stack(
        [_bernstein(deg_u, k, local[:, 2]) for k in range(deg_u + 1)], axis=1
    )
    deformed = np.einsum("vi,vj,vk,ijkc->vc", basis_s, basis_t, basis_u, lattice)
    return Mesh(vertices=deformed, faces=mesh.faces.copy(), uv=mesh.uv.copy())

def identity_morph(mesh: Mesh) -> Mesh:
    """The null solve: positions unchanged. The round trip then measures the
    conversion tax alone: what tessellation plus the bounded refit cost before
    any simulation touched anything. The calibration zero of the instrument."""
    return Mesh(vertices=mesh.vertices.copy(), faces=mesh.faces.copy(), uv=mesh.uv.copy())

# Registry so the entry point and the loop name modes the same way.
MORPHS = {
    "identity": identity_morph,
    "clean": clean_morph,
    "collision": collision_morph,
    "fold": fold_morph,
    "ffd": ffd_morph,
}


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


def solve_morph(
    out_dir: str | Path, in_dir: str | Path, index: int, morph: str, **kwargs
) -> Path:
    """Phase 4 solver for one iteration: apply a named morph mode.

    Reads exchange/out/mesh_NNN.json, applies the morph (clean, collision, or
    fold), and writes the result to exchange/in/mesh_NNN.json. The carried identity
    and topology ride through unchanged: the morph moves vertices only, so the sent
    node_ids, adjacency, and winding are written back verbatim. That is what lets
    the validity gate read the returned geometry against the identity it sent.

    The morphs are synthetic. This is the same seam where a real solver would
    connect; a real solver that remeshes is the identity-not-preserved case the gate
    is built to catch. No CFD runs here (AGENT.md hard do-not list).
    """
    src = Path(out_dir) / mesh_filename(index)
    dst = Path(in_dir) / mesh_filename(index)
    mesh, descriptor = read_payload(src)
    topology = read_topology(src)
    morphed = MORPHS[morph](mesh, **kwargs)
    return write_payload(dst, morphed, descriptor, topology=topology)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="driftgauge solver side. Identity or synthetic mode."
    )
    parser.add_argument("--out-dir", default="exchange/out")
    parser.add_argument("--in-dir", default="exchange/in")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument(
        "--mode",
        choices=("identity", "synthetic", "clean", "collision", "fold", "ffd"),
        default="identity",
        help=(
            "identity returns the mesh unchanged; synthetic applies the bump; "
            "clean/collision/fold are the Phase 4 morph modes."
        ),
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
    elif args.mode == "synthetic":
        dst = solve_synthetic(
            args.out_dir, args.in_dir, args.index, args.magnitude
        )
        print(
            f"solver_stub synthetic (magnitude {args.magnitude}) wrote {dst}"
        )
    else:
        dst = solve_morph(args.out_dir, args.in_dir, args.index, args.mode)
        print(f"solver_stub {args.mode} morph wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
