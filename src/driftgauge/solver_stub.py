"""The solver side of the exchange. The far side, behind the file contract.

See specs/spec-02-exchange.md, specs/spec-04-identity-validity.md, and AGENT.md
("The two seams"). This is a separate entry point from the geometry side so the
process boundary is genuine, not a function call dressed up as one.

Honesty category 1: instrument logic, authored here.

Named boundaries surfaced in this module:
    - The solver is synthetic. No CFD, ever (AGENT.md hard do-not list). This is
      the real-CFD seam: where a real solver would connect, honoring this same
      file contract. A real solver does not exist here and visibly does not.
    - The exchange is files. No server, no socket, no port.

Modes (spec-04). Each is deterministic: a given input yields exactly one output,
so the violation it produces is a known stimulus.
    identity   returns the mesh unchanged (the Phase 1 mode).
    clean      a smooth normal-direction displacement. Moves vertices, preserves
               topology, passes every validity check. The valid baseline.
    collision  curls the surface chordwise into a closed loop, so the
               non-neighbor leading-edge and trailing-edge rows coincide.
    near_miss  the same curl stopped just short of closing, so the end rows stay
               just clear of the collision tolerance.
    fold       pokes one interior node through to the far side of its neighbors,
               inverting the faces around it.
    remesh     a clean displacement plus a rewrite of one node's identity in the
               returned topology, standing in for a solver that remeshed.

The synthetic field is a stand-in for real physics. The solver moves vertices and
carries identity and topology across unchanged (only the remesh mode alters the
topology, on purpose, to exercise the identity check).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from driftgauge.encode import (
    mesh_filename,
    read_payload,
    read_payload_full,
    write_payload,
)
from driftgauge.geometry import Mesh, Topology


# --- geometry helpers -----------------------------------------------------


def _newell_normal(points: np.ndarray) -> np.ndarray:
    """Newell's normal of a polygon, stable for non-planar quads."""
    n = np.zeros(3)
    k = len(points)
    for a in range(k):
        cur, nxt = points[a], points[(a + 1) % k]
        n[0] += (cur[1] - nxt[1]) * (cur[2] + nxt[2])
        n[1] += (cur[2] - nxt[2]) * (cur[0] + nxt[0])
        n[2] += (cur[0] - nxt[0]) * (cur[1] + nxt[1])
    return n


def _vertex_normals(mesh: Mesh) -> np.ndarray:
    """Per-vertex unit normals, area-weighted by accumulating incident face
    normals (Newell's, unnormalized, so larger faces weigh more)."""
    normals = np.zeros_like(mesh.vertices)
    for face in mesh.faces:
        idx = [int(i) for i in face]
        fn = _newell_normal(mesh.vertices[idx])
        for i in idx:
            normals[i] += fn
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return normals / lengths


# --- morph modes ----------------------------------------------------------


def morph_clean(mesh: Mesh, amplitude: float = 0.02) -> Mesh:
    """A smooth normal-direction displacement, zero on the boundary.

    disp(u, v) = amplitude * sin(pi u) * sin(pi v), a single pressure-like bump.
    Small enough that no non-neighbor nodes approach and no face inverts, so the
    result passes all three validity checks. This is the Phase 3 deformation.
    """
    u, v = mesh.uv[:, 0], mesh.uv[:, 1]
    disp = amplitude * np.sin(np.pi * u) * np.sin(np.pi * v)
    new_verts = mesh.vertices + disp[:, None] * _vertex_normals(mesh)
    return Mesh(vertices=new_verts, faces=mesh.faces.copy(), uv=mesh.uv.copy())


def morph_curl(mesh: Mesh, theta_max: float, radius: float = 0.5) -> Mesh:
    """Curl the surface chordwise about a spanwise axis into a circular arc.

    The chordwise parameter u drives the wrap angle, theta = u * theta_max. The
    section thickness (z in the wing frame) rides outward as radial offset, and the
    span (y) is preserved:

        radial = radius + z
        x' = radial * sin(theta)
        z' = radius - radial * cos(theta)
        y' = y

    The leading and trailing edges sit at z = 0 (the section closes at both ends),
    so at theta = 0 and theta = theta_max they reach the same radius. With
    theta_max = 2*pi the two end rows land on the same points: a closed loop where
    the non-neighbor end rows coincide exactly. The bend is smooth, so no face
    inverts. Stopping short of 2*pi leaves the end rows a known distance apart.
    """
    u = mesh.uv[:, 0]
    z = mesh.vertices[:, 2]
    y = mesh.vertices[:, 1]
    theta = u * theta_max
    radial = radius + z
    new_verts = np.empty_like(mesh.vertices)
    new_verts[:, 0] = radial * np.sin(theta)
    new_verts[:, 1] = y
    new_verts[:, 2] = radius - radial * np.cos(theta)
    return Mesh(vertices=new_verts, faces=mesh.faces.copy(), uv=mesh.uv.copy())


def morph_collision(mesh: Mesh, radius: float = 0.5) -> Mesh:
    """Full curl: theta_max = 2*pi, so the end rows coincide. A self-intersection
    on intact topology."""
    return morph_curl(mesh, theta_max=2.0 * np.pi, radius=radius)


def morph_near_miss(
    mesh: Mesh, clearance: float, radius: float = 0.5
) -> Mesh:
    """Curl stopped just short of closing, so the end rows stay `clearance` apart.

    For a small gap angle delta the end-row separation is approximately
    radius * delta, so delta = clearance / radius leaves the rows about `clearance`
    apart, by construction just clear of a tolerance set below it.
    """
    delta = clearance / radius
    return morph_curl(mesh, theta_max=2.0 * np.pi - delta, radius=radius)


def _interior_node_index(mesh: Mesh, topology: Topology) -> int:
    """Vertex index of an interior node nearest the mesh centroid.

    Interior nodes have the full neighbor ring (degree 4 on the quad grid). The
    one nearest the centroid is chosen deterministically so the fold is placed in
    the body of the surface, not at an edge."""
    id_to_idx = {int(n): i for i, n in enumerate(topology.node_ids)}
    degree = {nid: len(nbrs) for nid, nbrs in topology.adjacency.items()}
    max_degree = max(degree.values())
    interior = [nid for nid, d in degree.items() if d == max_degree]
    centroid = mesh.vertices.mean(axis=0)
    best = min(
        interior,
        key=lambda nid: (
            np.linalg.norm(mesh.vertices[id_to_idx[nid]] - centroid),
            nid,
        ),
    )
    return id_to_idx[best]


def morph_fold(mesh: Mesh, topology: Topology) -> Mesh:
    """Cross one interior node with a neighbor to invert a face locally.

    Two adjacent nodes exchange positions. The map from parameter space to 3D now
    sends those adjacent parameters to crossed locations, so the face on their
    shared edge winds backwards: a local inversion. This is a true fold, not a dip.
    Poking a single vertex out of plane only tilts the incident quads (their
    normals rotate toward perpendicular); crossing two parameters reverses a face's
    winding outright, which is what a fold is.

    The interior node nearest the centroid is crossed with its lowest-numbered
    interior neighbor, deterministically. The fold check flags the inverted face.
    Identity and collision pass: the topology is untouched and no two vertices
    coincide (the positions are exchanged, not collapsed).
    """
    idx = _interior_node_index(mesh, topology)
    nid = int(topology.node_ids[idx])
    id_to_idx = {int(n): i for i, n in enumerate(topology.node_ids)}
    degree = {k: len(v) for k, v in topology.adjacency.items()}
    max_degree = max(degree.values())
    interior_neighbors = [
        n for n in topology.adjacency[nid] if degree[n] == max_degree
    ]
    partner = id_to_idx[min(interior_neighbors)]

    new_verts = mesh.vertices.copy()
    new_verts[[idx, partner]] = new_verts[[partner, idx]]
    return Mesh(vertices=new_verts, faces=mesh.faces.copy(), uv=mesh.uv.copy())


def morph_remesh(
    mesh: Mesh, topology: Topology, amplitude: float = 0.02
) -> tuple[Mesh, Topology]:
    """A clean displacement plus a rewrite of one node's identity.

    Stands in for a solver that remeshed: an interior node is given a brand-new ID
    not present in what was sent, and the adjacency is rewritten to match. The
    geometry is admissible; the identity is not preserved. The identity check flags
    this as identity_not_preserved, a different signal than collision.
    """
    new_mesh = morph_clean(mesh, amplitude=amplitude)

    idx = _interior_node_index(mesh, topology)
    old_id = int(topology.node_ids[idx])
    new_id = int(topology.node_ids.max()) + 1

    new_ids = topology.node_ids.copy()
    new_ids[idx] = new_id

    new_adj: dict[int, list[int]] = {}
    for nid, nbrs in topology.adjacency.items():
        key = new_id if nid == old_id else nid
        new_adj[key] = [new_id if n == old_id else n for n in nbrs]

    face_nodes = np.where(topology.face_nodes == old_id, new_id, topology.face_nodes)
    new_topo = Topology(node_ids=new_ids, adjacency=new_adj, face_nodes=face_nodes)
    return new_mesh, new_topo


# --- the solver entry points ----------------------------------------------


def apply_mode(
    mesh: Mesh, topology: Topology, mode: str, **params
) -> tuple[Mesh, Topology]:
    """Dispatch a mode to its morph. Returns the morphed mesh and the topology to
    carry back. Every mode but remesh returns the topology unchanged."""
    if mode == "identity":
        return mesh, topology
    if mode == "clean":
        return morph_clean(mesh, **params), topology
    if mode == "collision":
        return morph_collision(mesh, **params), topology
    if mode == "near_miss":
        return morph_near_miss(mesh, **params), topology
    if mode == "fold":
        return morph_fold(mesh, topology, **params), topology
    if mode == "remesh":
        return morph_remesh(mesh, topology, **params)
    raise ValueError(f"unknown solver mode {mode!r}")


def solve(
    out_dir: str | Path,
    in_dir: str | Path,
    index: int,
    mode: str = "clean",
    **params,
) -> Path:
    """Run one iteration in the given mode.

    Reads exchange/out/mesh_NNN.json with its carried identity, applies the mode,
    and writes exchange/in/mesh_NNN.json with the descriptor and topology. The
    descriptor rides along unchanged so the returned mesh stays tied to its budget.
    """
    src = Path(out_dir) / mesh_filename(index)
    dst = Path(in_dir) / mesh_filename(index)
    mesh, descriptor, topology = read_payload_full(src)
    new_mesh, new_topology = apply_mode(mesh, topology, mode, **params)
    return write_payload(dst, new_mesh, descriptor, new_topology)


def solve_identity(out_dir: str | Path, in_dir: str | Path, index: int) -> Path:
    """Identity solver for one iteration. Retained from Phase 1.

    Reads exchange/out/mesh_NNN.json, writes the same mesh to
    exchange/in/mesh_NNN.json, preserving UV and descriptor. The returned mesh is
    unchanged, so the boundary crossing adds nothing. This reads with the Phase 1
    reader so its behavior is exactly as it was before topology was carried.
    """
    src = Path(out_dir) / mesh_filename(index)
    dst = Path(in_dir) / mesh_filename(index)
    mesh, descriptor = read_payload(src)
    return write_payload(dst, mesh, descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="driftgauge solver side.")
    parser.add_argument("--out-dir", default="exchange/out")
    parser.add_argument("--in-dir", default="exchange/in")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument(
        "--mode",
        default="clean",
        choices=["identity", "clean", "collision", "near_miss", "fold", "remesh"],
    )
    args = parser.parse_args(argv)
    dst = solve(args.out_dir, args.in_dir, args.index, args.mode)
    print(f"solver_stub mode={args.mode} wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
