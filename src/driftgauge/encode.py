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

from driftgauge.geometry import Mesh, Topology, build_topology


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
    """Mesh, descriptor, and carried topology to a JSON-serializable dict.

    units and a short frame note travel with the payload so the geometry is
    unambiguous across the boundary.

    The topology block carries identity across the boundary (see spec-04). If a
    topology is not supplied it is built from the mesh, so identity rides along by
    default. adjacency keys are written as strings because JSON object keys are
    strings; decode_topology parses them back to integers.
    """
    if topology is None:
        topology = build_topology(mesh)
    return {
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist(),
        "uv": mesh.uv.tolist(),
        "units": "mm",
        "frame": "x chordwise, y spanwise, z thickness; surface UV: u chordwise, v spanwise",
        "topology": {
            "node_ids": topology.node_ids.tolist(),
            "adjacency": {
                str(nid): nbrs for nid, nbrs in topology.adjacency.items()
            },
            "face_nodes": topology.face_nodes.tolist(),
        },
        "descriptor": {
            "constraints": descriptor.constraints,
            "preserve": descriptor.preserve,
            "provenance": descriptor.provenance,
        },
    }


def decode(obj: dict[str, Any]) -> tuple[Mesh, Descriptor]:
    """Inverse of encode for mesh and descriptor.

    The return arity is held at (mesh, descriptor) so the Phase 1 to 3 call sites
    are unchanged. The carried topology is read separately by decode_topology.
    """
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
    """Read the carried topology from a payload.

    A payload written before Phase 4 has no topology block, so the topology is
    rebuilt from the decoded mesh and old files still load. adjacency string keys
    are parsed back to integers here.
    """
    if "topology" not in obj:
        mesh, _ = decode(obj)
        return build_topology(mesh)
    t = obj["topology"]
    adjacency = {int(k): list(v) for k, v in t["adjacency"].items()}
    return Topology(
        node_ids=np.asarray(t["node_ids"], dtype=int),
        adjacency=adjacency,
        face_nodes=np.asarray(t["face_nodes"], dtype=int),
    )


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
    """Write the encoded payload to path as JSON. Topology rides along; it is
    built from the mesh when not supplied."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(encode(mesh, descriptor, topology), fh, indent=2)
    return path


def read_payload(path: str | Path) -> tuple[Mesh, Descriptor]:
    """Read and decode mesh and descriptor from path. The Phase 1 to 3 reader."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return decode(json.load(fh))


def read_payload_full(path: str | Path) -> tuple[Mesh, Descriptor, Topology]:
    """Read mesh, descriptor, and carried topology from path. The Phase 4 reader."""
    with Path(path).open("r", encoding="utf-8") as fh:
        obj = json.load(fh)
    mesh, descriptor = decode(obj)
    return mesh, descriptor, decode_topology(obj)
