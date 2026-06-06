"""Run the Phase 4 loop end-to-end and print the result. One command.

    python scripts/run_phase4.py

This drives the geometry side of the loop with the validity gate wired in after
decode and before reconstruction. For each solver morph mode (clean, collision,
fold) and for a constructed remesh, it writes the payload to exchange/out, calls
the solver side as a separate step, reads the returned payload from exchange/in,
runs the validity gate, and only reconstructs and gauges drift on a clean return.

The flow: carry identity and topology out, the solver morphs, return, validity
gate, then reconstruct and gauge drift only if valid. The morphs are synthetic and
deterministic. No CFD (AGENT.md hard do-not list).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from driftgauge.encode import (  # noqa: E402
    Descriptor,
    mesh_filename,
    read_payload,
    read_topology,
    topology_from_mesh,
    write_payload,
)
from driftgauge.geometry import build_wing_surface, tessellate  # noqa: E402
from driftgauge.metrics import hausdorff  # noqa: E402
from driftgauge.reconstruct import ClassicalReconstructor  # noqa: E402
from driftgauge.solver_stub import solve_morph  # noqa: E402
from driftgauge.validity import check_identity, validity_gate  # noqa: E402

N_U, N_V = 24, 12


def _descriptor(index: int) -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-2, "direction": 1}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": index},
    )


def _tolerance(mesh, topology) -> float:
    """Collision tolerance in the valid regime: the geometric mean of the smallest
    edge and the smallest non-neighbor separation, so it is between them. Below the
    non-neighbor spacing (a clean return has no non-neighbor within it), above the
    smallest edge (legal neighbor-closeness is present and ignored)."""
    from scipy.spatial import cKDTree

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


def main() -> int:
    out_dir, in_dir = ROOT / "exchange" / "out", ROOT / "exchange" / "in"
    source = tessellate(build_wing_surface(), N_U, N_V)
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)

    write_payload(out_dir / mesh_filename(0), source, _descriptor(0))
    sent = read_topology(out_dir / mesh_filename(0))
    tol = _tolerance(source, sent)
    print(f"Collision tolerance (between smallest edge and smallest non-neighbor): {tol:.5f}\n")

    expected = {"clean": "valid", "collision": "collision", "fold": "fold"}
    all_ok = True

    for i, (morph, want) in enumerate(expected.items()):
        write_payload(out_dir / mesh_filename(i), source, _descriptor(i))
        sent = read_topology(out_dir / mesh_filename(i))
        solve_morph(out_dir, in_dir, i, morph)
        returned, _ = read_payload(in_dir / mesh_filename(i))
        returned_topo = read_topology(in_dir / mesh_filename(i))

        gate = validity_gate(sent, returned, returned_topo, tol)
        print(f"[{morph}] valid={gate.valid} signals={gate.signals}")

        if gate.reconstruct:
            recon = rc.reconstruct(returned.vertices, returned.uv)
            drift = hausdorff(returned.vertices, tessellate(recon, N_U, N_V).vertices)
            print(f"    gate cleared: reconstructed, drift_max={drift['max']:.5e}")
            ok = want == "valid"
        else:
            for r in gate.results:
                if not r.passed:
                    if r.check == "collision":
                        print(f"    flagged {r.signal}: non-neighbor pair {r.node_ids[0]} coincided")
                    elif r.check == "fold":
                        print(f"    flagged {r.signal}: inverted face(s) {r.faces}")
                    else:
                        print(f"    flagged {r.signal}: node ids {r.node_ids}")
            print("    gate stopped: reconstruction did not run")
            ok = want in gate.signals
        all_ok = all_ok and ok

    # The identity-not-preserved case: a constructed remesh, the one thing the
    # synthetic solver does not do. Drop the single face of the corner node so its
    # connectivity changes.
    faces = source.faces
    drop = np.where((faces == 0).any(axis=1))[0][0]
    mask = np.ones(faces.shape[0], dtype=bool)
    mask[drop] = False
    remeshed = type(source)(vertices=source.vertices, faces=faces[mask], uv=source.uv)
    returned_topo = topology_from_mesh(remeshed)
    gate = validity_gate(sent, remeshed, returned_topo, tol)
    print(f"\n[remesh] valid={gate.valid} signals={gate.signals}")
    identity = gate.results[0]
    print(f"    flagged {identity.signal}: node ids {identity.node_ids}")
    print(f"    gate stopped before the geometry checks (only {len(gate.results)} check ran)")
    remesh_ok = (
        not gate.valid
        and gate.signals == ["identity_not_preserved"]
        and identity.signal != check_identity(sent, sent).signal  # distinct from "ok"
    )
    all_ok = all_ok and remesh_ok

    print(f"\nPHASE 4 GATE: {'PASS' if all_ok else 'FAIL'}")
    print("  Clean passes and reconstructs. Collision and fold are each caught as")
    print("  their own signal, naming the pair or the face. A remesh is caught as")
    print("  identity-not-preserved, distinct from a collision, and stops the gate")
    print("  before reconstruction.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
