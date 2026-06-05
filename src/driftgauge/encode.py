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

from driftgauge.geometry import Mesh


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


def encode(mesh: Mesh, descriptor: Descriptor) -> dict[str, Any]:
    """Mesh and descriptor to a JSON-serializable dict.

    units and a short frame note travel with the payload so the geometry is
    unambiguous across the boundary.
    """
    return {
        "vertices": mesh.vertices.tolist(),
        "faces": mesh.faces.tolist(),
        "uv": mesh.uv.tolist(),
        "units": "mm",
        "frame": "x chordwise, y spanwise, z thickness; surface UV: u chordwise, v spanwise",
        "descriptor": {
            "constraints": descriptor.constraints,
            "preserve": descriptor.preserve,
            "provenance": descriptor.provenance,
        },
    }


def decode(obj: dict[str, Any]) -> tuple[Mesh, Descriptor]:
    """Inverse of encode. Returns the same data model spec-01 defines."""
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


def mesh_filename(index: int) -> str:
    """mesh_NNN.json with a zero-padded iteration index. The same index in out/
    and in/ is one round trip."""
    return f"mesh_{index:03d}.json"


def write_payload(path: str | Path, mesh: Mesh, descriptor: Descriptor) -> Path:
    """Write the encoded payload to path as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(encode(mesh, descriptor), fh, indent=2)
    return path


def read_payload(path: str | Path) -> tuple[Mesh, Descriptor]:
    """Read and decode a JSON payload from path."""
    with Path(path).open("r", encoding="utf-8") as fh:
        return decode(json.load(fh))
