"""The exchange contract: mesh and descriptor to JSON and back.

See specs/spec-02-exchange.md. The exchange is a file contract, not a server.
The geometry side writes a mesh and a descriptor; the solver side reads it and
writes a modified mesh back; the geometry side reads that. The contract lives on
disk, which makes it testable and honest.

Honesty category 1: this is instrument logic, authored here.

Named boundary surfaced in this module:
    The exchange is files, not a service. A live service, if ever wanted, is a
    wrapper around this same file contract (PLAN.md Phase 6), not a redesign.

Encoding is lossless within floating-point tolerance: decode(encode(x)) returns
the same arrays. Python's json writes the full repr of each double, which
round-trips exactly, so the only tolerance in play is the one the tests assert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from passthrough.geometry import Mesh


@dataclass
class Topology:
    """Identity and connectivity that ride the exchange alongside the mesh.

    See specs/spec-04-identity-validity.md. The geometry side derives this once
    from the sent mesh and carries it across the boundary. The far side returns it
    unchanged: it moves vertices and leaves the names and the wiring alone. The
    validity gate (validity.py) reads it to separate three failures: a topology
    change (identity not preserved), a collision (non-neighbors coincide), and a
    fold (a face inverted against its winding).

    node_ids:  (N,) int. One stable ID per vertex, in vertex order. The ID is the
               vertex's name. It travels out and must come back attached to the
               same vertex. Renumbering or dropping an ID breaks identity.
    adjacency: {node_id: tuple of sorted neighbor node_ids}. The edge graph keyed
               by name, not by row index. The collision check reads it to know
               which closeness is legal; the identity check compares it to decide
               whether the wiring survived.
    winding:   (M, K) int of node_ids per face, in the face's vertex order. The
               cyclic order is the winding. The fold check reads it to compute each
               face's orientation on the returned mesh against the order sent.
    """

    node_ids: np.ndarray
    adjacency: dict[int, tuple[int, ...]]
    winding: np.ndarray

    def __post_init__(self) -> None:
        self.node_ids = np.asarray(self.node_ids, dtype=int)
        self.winding = np.asarray(self.winding, dtype=int)


def _adjacency_from_winding(winding: np.ndarray) -> dict[int, tuple[int, ...]]:
    """Edge graph keyed by node ID, derived from the face winding.

    Two node IDs are neighbors when they are consecutive (cyclically) in some
    face's winding, that is, when an edge of a face joins them. Each neighbor list
    is sorted and de-duplicated so two topologies compare equal exactly when their
    wiring matches.
    """
    neighbors: dict[int, set[int]] = {}
    for face in winding:
        k = len(face)
        for a in range(k):
            i = int(face[a])
            j = int(face[(a + 1) % k])
            neighbors.setdefault(i, set()).add(j)
            neighbors.setdefault(j, set()).add(i)
    return {nid: tuple(sorted(nbrs)) for nid, nbrs in neighbors.items()}


def topology_from_mesh(
    mesh: Mesh, node_ids: np.ndarray | None = None
) -> Topology:
    """Derive identity and topology from a mesh.

    node_ids default to the vertex order (0..N-1): the geometry side names each
    vertex by its position when it first builds the mesh. The winding is the faces
    expressed in node IDs, and the adjacency follows from the winding. Passing
    node_ids explicitly is how a returned mesh keeps the names it was given rather
    than being renamed by its row order.
    """
    n = mesh.vertices.shape[0]
    ids = np.arange(n, dtype=int) if node_ids is None else np.asarray(node_ids, dtype=int)
    if ids.shape[0] != n:
        raise ValueError("node_ids must have one entry per vertex")
    winding = ids[mesh.faces]
    return Topology(node_ids=ids, adjacency=_adjacency_from_winding(winding), winding=winding)


@dataclass
class Descriptor:
    """The loss budget, serialized. Rides alongside the mesh and declares what
    the far side is not allowed to lose.

    constraints: declared constraints, each a dict with at least a type and a
        tolerance. The constraint checker (Phase 2) reads the budget from here,
        not from a hardcoded value.
    preserve: named regions or properties that must survive, for example the
        leading-edge curvature class.
    provenance: source surface identity and iteration index, so a returned mesh
        can be tied back to what was sent.

    The fields are carried verbatim across the boundary in this phase. Their
    meaning is exercised in Phase 2; the contract is defined now so the seam is
    stable.
    """

    constraints: list[dict[str, Any]] = field(default_factory=list)
    preserve: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)


def encode(
    mesh: Mesh, descriptor: Descriptor, topology: Topology | None = None
) -> dict[str, Any]:
    """Mesh, descriptor, and identity/topology to a JSON-serializable dict.

    units and a short frame note travel with the payload so the geometry is
    unambiguous across the boundary.

    The identity and topology (node_ids, adjacency, winding) ride alongside the
    mesh. When topology is None it is derived from the mesh, so the sent payload
    always carries it. Adjacency keys are written as strings because JSON object
    keys are strings; decode_topology converts them back to integers.
    """
    if topology is None:
        topology = topology_from_mesh(mesh)
    return {
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist(),
        "uv": mesh.uv.tolist(),
        "units": "mm",
        "frame": "x chordwise, y spanwise, z thickness; surface UV: u chordwise, v spanwise",
        "topology": {
            "node_ids": topology.node_ids.tolist(),
            "adjacency": {
                str(nid): list(nbrs) for nid, nbrs in topology.adjacency.items()
            },
            "winding": topology.winding.tolist(),
        },
        "descriptor": {
            "constraints": descriptor.constraints,
            "preserve": descriptor.preserve,
            "provenance": descriptor.provenance,
        },
    }


def decode(obj: dict[str, Any]) -> tuple[Mesh, Descriptor]:
    """Inverse of encode for the mesh and descriptor. Returns the same data model
    spec-01 defines. The identity and topology are read with decode_topology, kept
    a separate call so existing two-value callers stay unchanged."""
    mesh = Mesh(
        vertices=np.asarray(obj["vertices"], dtype=float),
        faces=np.asarray(obj["faces"], dtype=int),
        uv=np.asarray(obj["uv"], dtype=float),
    )
    d = obj["descriptor"]
    descriptor = Descriptor(
        constraints=d.get("constraints", []),
        preserve=d.get("preserve", {}),
        provenance=d.get("provenance", {}),
    )
    return mesh, descriptor


def decode_topology(obj: dict[str, Any]) -> Topology:
    """Read the carried identity and topology from a payload.

    If the payload predates this phase and has no topology block, it is derived
    from the mesh, so an old payload still yields a usable topology. The carried
    node_ids are used as the names; adjacency keys are converted from JSON strings
    back to integers.
    """
    if "topology" not in obj:
        mesh, _ = decode(obj)
        return topology_from_mesh(mesh)
    t = obj["topology"]
    node_ids = np.asarray(t["node_ids"], dtype=int)
    adjacency = {
        int(nid): tuple(int(n) for n in nbrs) for nid, nbrs in t["adjacency"].items()
    }
    winding = np.asarray(t["winding"], dtype=int)
    return Topology(node_ids=node_ids, adjacency=adjacency, winding=winding)


def mesh_filename(index: int) -> str:
    """mesh_NNN.json with a zero-padded iteration index. The same index in out/
    and in/ is one round trip."""
    return f"mesh_{index:03d}.json"


def write_payload(
    path: str | Path,
    mesh: Mesh,
    descriptor: Descriptor,
    topology: Topology | None = None,
) -> Path:
    """Write the encoded payload to path as JSON. Identity and topology ride along;
    when topology is None encode derives it from the mesh."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(encode(mesh, descriptor, topology), fh, indent=2)
    return path


def read_payload(path: str | Path) -> tuple[Mesh, Descriptor]:
    """Read and decode the mesh and descriptor from a JSON payload."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return decode(json.load(fh))


def read_topology(path: str | Path) -> Topology:
    """Read the carried identity and topology from a JSON payload."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return decode_topology(json.load(fh))
