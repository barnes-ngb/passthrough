"""The run entry point: an external tagged mesh in, a result and a status marker out.

See docs/PHASE7A_KICKOFF.md and PLAN.md Phase 7a. This is step 2 of the three-step
file demo. Step 1 (a Grasshopper export) writes an incoming payload; step 2 reads
it, runs the passthrough loop, and writes a status marker when done; step 3 (a
Grasshopper import) waits on the marker and pulls the result. Phase 7a builds step 2
and the contracts on both sides of it, all in Python so the handshake can be proven
by hand with files before any C# or Rhino exists.

Honesty category 1: this is instrument logic, authored here. It adds no measurement.
The drift, the deviation field, the curvature deviation, the validity gate, and the
report all come from the existing modules unchanged. This module orchestrates them
on a mesh that arrives from outside instead of one the loop generates itself.

The inversion this phase makes: the earlier runners build the source mesh internally
with tessellate(build_wing_surface()). Here the source mesh arrives in the payload,
which is what lets Grasshopper own the source geometry later. The rest of the loop
(the synthetic solver step, the gate, the reconstruction, the measurement) is the
same code on the same contract.

The done/failed distinction is kept clean. A caught problem is a successful run that
found a problem: a collision or a fold is `done` with `flagged` true and the signals
named, not `failed`. `failed` is reserved for a malformed payload or an actual error.
The status marker is written last and atomically so a reader never sees a partial
marker (write_status).

Named boundaries surfaced in this module:
    - The solver step is synthetic. The morph modes stand in for an external solve
      (AGENT.md hard do-not list: no CFD, ever). This is the same seam the earlier
      phases use.
    - The run holds only meshes, never the source NurbsSurface. Curvature and the
      constraint residuals compare against a source surface reconstructed from the
      incoming mesh, the same bounded fit the reconstruction side already uses. A
      real pipeline behind this contract is in the same position: it gets meshes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from passthrough.constraints import check_constraints, curvature_deviation_field
from passthrough.driftgauge import hausdorff
from passthrough.encode import (
    Descriptor,
    Topology,
    _adjacency_from_winding,
    decode,
    decode_topology,
    encode,
    write_payload,
)
from passthrough.geometry import Mesh, build_wing_surface, tessellate
from passthrough.reconstruct import ClassicalReconstructor
from passthrough.report import (
    LoopReport,
    assemble_report,
    deviation_field,
    write_field,
    write_report,
)
from passthrough.solver_stub import MORPHS
from passthrough.validity import validity_gate

# The Phase 5 loop parameters, held here so the synthetic-wing payload run reproduces
# the Phase 5 drift numbers exactly.
N_U, N_V = 24, 12
N_CTRL_U, N_CTRL_V = 7, 4

RESULT_NAME = "result.json"
FIELD_NAME = "field.json"
STATUS_NAME = "status.json"


class PayloadError(ValueError):
    """A malformed or untagged incoming payload.

    Raised by the reader when the payload cannot make the trip: a missing tag, a
    face that points past the mesh, an adjacency that does not match the faces. The
    runner turns it into a `failed` status with the message as the reason rather than
    letting it crash. A mesh without identity cannot cross the boundary, and saying so
    cleanly is correct behavior, not an error to suppress.
    """


# --- the reader and its validation ----------------------------------------


def validate_incoming(obj: dict[str, Any]) -> None:
    """Check an incoming payload against the exchange schema. Raise PayloadError if it
    is malformed or missing tags.

    The incoming payload is not a raw mesh. It is a mesh plus its tags, in the same
    schema encode.py writes on the outgoing side: vertices, quad faces, per-vertex
    node IDs, edge adjacency, and face winding. The checks here are the ones the
    contract depends on:

        - the tags are present (a mesh without identity cannot make the trip),
        - every face references existing vertices,
        - node IDs are unique and cover the vertices (one distinct name per vertex),
        - the winding is consistent with the faces and node IDs,
        - the adjacency matches the faces.

    Absent tags are not invented. A payload without identity fails here, by design.
    """
    if not isinstance(obj, dict):
        raise PayloadError("payload is not a JSON object")

    for key in ("vertices", "faces", "uv"):
        if key not in obj:
            raise PayloadError(f"missing required key: {key!r}")

    if "topology" not in obj:
        raise PayloadError(
            "missing identity and topology tags: a mesh without identity "
            "cannot make the trip"
        )
    topo = obj["topology"]
    if not isinstance(topo, dict):
        raise PayloadError("topology block is not an object")
    for key in ("node_ids", "adjacency", "winding"):
        if key not in topo:
            raise PayloadError(f"missing topology tag: {key!r}")

    try:
        vertices = np.asarray(obj["vertices"], dtype=float)
        faces = np.asarray(obj["faces"], dtype=int)
        uv = np.asarray(obj["uv"], dtype=float)
        node_ids = np.asarray(topo["node_ids"], dtype=int)
        winding = np.asarray(topo["winding"], dtype=int)
    except (ValueError, TypeError) as exc:
        raise PayloadError(f"payload arrays are not well formed: {exc}")

    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise PayloadError("vertices must be an (N, 3) array")
    n = vertices.shape[0]
    if uv.shape != (n, 2):
        raise PayloadError(f"uv must be an ({n}, 2) array, one (u, v) per vertex")
    if faces.ndim != 2:
        raise PayloadError("faces must be a 2D array of vertex indices")

    # Node IDs unique and covering the vertices: one distinct name per vertex.
    if node_ids.shape != (n,):
        raise PayloadError("node_ids must have one entry per vertex")
    if len({int(i) for i in node_ids}) != n:
        raise PayloadError(
            "node_ids are not unique: identity requires one distinct name per vertex"
        )

    # Every face references existing vertices.
    if faces.size and (faces.min() < 0 or faces.max() >= n):
        raise PayloadError("a face references a vertex index outside the mesh")

    # The winding is the faces expressed in node IDs; it must agree with both.
    if winding.shape != faces.shape:
        raise PayloadError("winding shape does not match faces")
    if faces.size and not np.array_equal(winding, node_ids[faces]):
        raise PayloadError("winding is inconsistent with the faces and node_ids")

    # The adjacency must match the faces. Derive the edge graph from the winding and
    # compare it to the carried adjacency; a mismatch means the wiring was tampered.
    try:
        stored = {
            int(k): tuple(int(x) for x in v) for k, v in topo["adjacency"].items()
        }
    except (ValueError, TypeError, AttributeError) as exc:
        raise PayloadError(f"adjacency is not well formed: {exc}")
    if stored != _adjacency_from_winding(winding):
        raise PayloadError("adjacency does not match the faces")


def read_incoming_payload(
    path: str | Path,
) -> tuple[Mesh, Descriptor, Topology]:
    """Read and validate an incoming payload. Return the mesh, descriptor, and topology.

    Raises PayloadError on bad JSON, a missing tag, or an inconsistent payload. The
    runner catches it and writes a `failed` status; nothing crashes.
    """
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"payload is not valid JSON: {exc}")

    validate_incoming(obj)
    mesh, descriptor = decode(obj)
    topology = decode_topology(obj)
    return mesh, descriptor, topology


# --- the loop, run on the external input ----------------------------------


def collision_tolerance(mesh: Mesh, topology: Topology) -> float:
    """The collision tolerance in the valid regime: the geometric mean of the smallest
    edge and the smallest non-neighbor separation, so it sits between them.

    Below the non-neighbor spacing (a clean return has no non-neighbor within it) and
    above the smallest edge (legal neighbor-closeness is present and ignored). This is
    the same regime the Phase 4 and Phase 5 runners use to gate the wing.
    """
    v, ids, adj = mesh.vertices, topology.node_ids, topology.adjacency
    tree = cKDTree(v)
    edge_min, nonneighbor_min = np.inf, np.inf
    for i, j in tree.query_pairs(r=0.5):
        a, b = int(ids[i]), int(ids[j])
        d = float(np.linalg.norm(v[i] - v[j]))
        if b in adj.get(a, ()):
            edge_min = min(edge_min, d)
        else:
            nonneighbor_min = min(nonneighbor_min, d)
    return float(np.sqrt(edge_min * nonneighbor_min))


def run_loop(
    mesh: Mesh,
    descriptor: Descriptor,
    sent_topology: Topology,
    morph: str = "clean",
    *,
    n_u: int = N_U,
    n_v: int = N_V,
    n_ctrl_u: int = N_CTRL_U,
    n_ctrl_v: int = N_CTRL_V,
) -> LoopReport:
    """Run the existing loop on an externally supplied mesh and return one report.

    The synthetic solver morph stands in for the external solve and carries identity
    and topology through unchanged, so the returned topology is the sent topology. The
    validity gate runs against it. On a clean pass the reconstruction, the drift, the
    deviation field, the constraints, and the curvature deviation run. On a flagged
    pass none of those run and the report carries the returned mesh and the flagged
    regions.

    No measurement is recomputed here. Each number comes from the module that owns it.
    """
    if morph not in MORPHS:
        raise PayloadError(f"unknown solver morph: {morph!r}")

    returned = MORPHS[morph](mesh)
    # The morph moves vertices only, so the sent identity and topology ride through.
    returned_topology = sent_topology

    tol = collision_tolerance(mesh, sent_topology)
    gate = validity_gate(sent_topology, returned, returned_topology, tol)

    rc = ClassicalReconstructor(n_ctrl_u=n_ctrl_u, n_ctrl_v=n_ctrl_v)
    if not gate.reconstruct:
        return assemble_report(
            returned,
            gate,
            node_ids=returned_topology.node_ids,
            provenance=descriptor.provenance,
        )

    recon = rc.reconstruct(returned.vertices, returned.uv)
    remesh = tessellate(recon, n_u, n_v)
    drift = hausdorff(returned.vertices, remesh.vertices)
    deviation = deviation_field(remesh.vertices, returned.vertices)

    # Curvature and the constraints compare against the source surface. The run holds
    # only meshes, so the source surface is reconstructed from the incoming mesh with
    # the same bounded fit. This is the parameterization-gap boundary, applied to the
    # source side of the trip.
    source_surface = rc.reconstruct(mesh.vertices, mesh.uv)
    constraints = check_constraints(descriptor, recon, source=source_surface)
    curv = curvature_deviation_field(recon, source_surface)

    return assemble_report(
        remesh,
        gate,
        deviation=deviation,
        drift=drift,
        constraints=constraints,
        provenance=descriptor.provenance,
        curvature_deviation=curv["deviation"],
        curvature_points=curv["points"],
    )


# --- the status marker, written last and atomically -----------------------


def write_status(path: str | Path, status: dict[str, Any]) -> Path:
    """Write the status marker atomically: full content to a temp name, then rename.

    The rename is the point. A reader on the import side waits on status.json and must
    never see a half-written marker, so the bytes are written to a sibling temp file,
    flushed to disk, and then moved into place with a single os.replace. os.replace is
    atomic on a given filesystem: the marker appears whole or not at all.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def _status_payload(report: LoopReport) -> dict[str, Any]:
    """The `done` status body for a completed run.

    Carries whether the pass was flagged, the signals raised, the relative paths to
    the result and field files, and the headline drift max. A flagged pass has no
    reconstruction, so drift_max is null and the signals name the problem.
    """
    flagged = not report.validity.valid
    return {
        "status": "done",
        "flagged": flagged,
        "signals": list(report.validity.signals),
        "result": RESULT_NAME,
        "field": FIELD_NAME,
        "drift_max": None if report.drift is None else float(report.drift["max"]),
    }


def run_roundtrip(
    payload_path: str | Path,
    return_dir: str | Path,
    morph: str = "clean",
    *,
    n_u: int = N_U,
    n_v: int = N_V,
    n_ctrl_u: int = N_CTRL_U,
    n_ctrl_v: int = N_CTRL_V,
) -> dict[str, Any]:
    """Read an incoming payload, run the loop, write the result, the field, and the
    status marker. Return a summary of what was written.

    Steps 1 to 3 of the kickoff, in order. A malformed payload or an actual error
    yields a `failed` status with a human-readable reason and no crash: the result and
    field are not written, because there is nothing to write. A clean or flagged pass
    yields a `done` status with the paths and the headline numbers. The status marker
    is always written last and atomically.
    """
    return_dir = Path(return_dir)
    return_dir.mkdir(parents=True, exist_ok=True)
    status_path = return_dir / STATUS_NAME

    try:
        mesh, descriptor, sent_topology = read_incoming_payload(payload_path)
        report = run_loop(
            mesh,
            descriptor,
            sent_topology,
            morph,
            n_u=n_u,
            n_v=n_v,
            n_ctrl_u=n_ctrl_u,
            n_ctrl_v=n_ctrl_v,
        )
    except PayloadError as exc:
        reason = str(exc)
        written = write_status(status_path, {"status": "failed", "reason": reason})
        return {"status": "failed", "reason": reason, "status_path": written, "wrote": [written]}
    except Exception as exc:  # an actual error is a failed run, not a crash
        reason = f"run error: {type(exc).__name__}: {exc}"
        written = write_status(status_path, {"status": "failed", "reason": reason})
        return {"status": "failed", "reason": reason, "status_path": written, "wrote": [written]}

    result_path = write_report(return_dir / RESULT_NAME, report)
    field_path = write_field(return_dir / FIELD_NAME, report)
    status = _status_payload(report)
    written = write_status(status_path, status)
    return {
        "status": "done",
        "flagged": status["flagged"],
        "signals": status["signals"],
        "drift_max": status["drift_max"],
        "result_path": result_path,
        "field_path": field_path,
        "status_path": written,
        "wrote": [result_path, field_path, written],
    }


# --- the stand-in for the Grasshopper export ------------------------------


def default_descriptor(index: int = 0) -> Descriptor:
    """The loss-budget declaration the synthetic payload carries, the same one the
    Phase 4 and Phase 5 runners use: a ruled constraint with its tolerance, the
    leading-edge curvature class to preserve, and the source provenance."""
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-2, "direction": 1}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": index},
    )


def emit_synthetic_payload(
    path: str | Path,
    n_u: int = N_U,
    n_v: int = N_V,
    descriptor: Descriptor | None = None,
) -> Path:
    """Write a valid incoming payload from the synthetic wing.

    This is the stand-in for the eventual Grasshopper export: a known-good input to
    feed the runner before Grasshopper can produce one. It tessellates the synthetic
    wing, attaches the default descriptor, and writes the payload through the same
    encoder the outgoing side uses, so identity and topology ride along. It doubles as
    the fixture for the tests.
    """
    source = tessellate(build_wing_surface(), n_u, n_v)
    return write_payload(path, source, descriptor or default_descriptor())


def make_malformed_payload(n_u: int = N_U, n_v: int = N_V) -> dict[str, Any]:
    """A structurally broken payload: a face that points past the last vertex.

    Used by the runner demo and the tests to exercise the `failed` path. The mesh and
    tags are otherwise the synthetic wing, so the single break is the only reason the
    payload is rejected.
    """
    source = tessellate(build_wing_surface(), n_u, n_v)
    obj = encode(source, default_descriptor())
    obj["faces"][0][0] = len(obj["vertices"])  # index past the last vertex
    return obj


def write_malformed_payload(path: str | Path, n_u: int = N_U, n_v: int = N_V) -> Path:
    """Write the malformed payload to path, for the failed-path demo."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(make_malformed_payload(n_u, n_v), fh, indent=2)
    return path
