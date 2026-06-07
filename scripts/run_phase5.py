"""Run the Phase 5 reporting layer end-to-end and print where it wrote. One command.

    python scripts/run_phase5.py

This drives a clean loop pass and a collision loop pass, assembles a report for
each, writes the Grasshopper field file and the static fallback PNG for each, and
prints the paths. The clean pass clears the validity gate, reconstructs, and carries
a per-vertex deviation field colored in the render. The collision pass stops at the
gate, carries no deviation, and the render marks the coinciding non-neighbor pair.

The static render is headless (matplotlib Agg). It is the slides-only fallback that
survives the live demo dying, and the image a reviewer can open on a phone. No
Rhino, no CFD, no server (AGENT.md hard do-not list).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from passthrough.constraints import (  # noqa: E402
    check_constraints,
    curvature_deviation_field,
)
from passthrough.driftgauge import hausdorff  # noqa: E402
from passthrough.encode import (  # noqa: E402
    Descriptor,
    mesh_filename,
    read_payload,
    read_topology,
    write_payload,
)
from passthrough.geometry import build_wing_surface, tessellate  # noqa: E402
from passthrough.reconstruct import ClassicalReconstructor  # noqa: E402
from passthrough.report import (  # noqa: E402
    assemble_report,
    deviation_field,
    render,
    render_comparison,
    write_field,
    write_report,
)
from passthrough.solver_stub import solve_morph  # noqa: E402
from passthrough.validity import validity_gate  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402

N_U, N_V = 24, 12


def _descriptor(index: int) -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-2, "direction": 1}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": index},
    )


def _tolerance(mesh, topology) -> float:
    """Collision tolerance between the smallest edge and the smallest non-neighbor
    separation, the same valid regime the Phase 4 runner uses."""
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


def _emit(report, artifacts, name):
    """Write the report, the field file, and both renders for one pass. Print paths.

    The single-panel render is the positional fallback; the comparison render is the
    two-panel positional-vs-curvature picture (the thesis centerpiece on a clean pass,
    the gray-with-flag fallback on a flagged one)."""
    report_path = write_report(artifacts / f"{name}_report.json", report)
    field_path = write_field(artifacts / f"{name}_field.json", report)
    png_path = render(report, artifacts / f"{name}.png")
    comparison_path = render_comparison(report, artifacts / f"{name}_comparison.png")
    print(f"  report:     {report_path}")
    print(f"  field:      {field_path}")
    print(f"  render:     {png_path}")
    print(f"  comparison: {comparison_path}")
    return png_path


def main() -> int:
    out_dir, in_dir = ROOT / "exchange" / "out", ROOT / "exchange" / "in"
    artifacts = ROOT / "artifacts"
    source = tessellate(build_wing_surface(), N_U, N_V)
    source_surface = build_wing_surface()
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)

    write_payload(out_dir / mesh_filename(0), source, _descriptor(0))
    sent = read_topology(out_dir / mesh_filename(0))
    tol = _tolerance(source, sent)
    print(f"Collision tolerance (between smallest edge and smallest non-neighbor): {tol:.5f}\n")

    # --- clean pass: gate clears, reconstruct, deviation field colored ---
    print("[clean] valid pass: gate clears, reconstruction runs")
    solve_morph(out_dir, in_dir, 0, "clean")
    returned, desc = read_payload(in_dir / mesh_filename(0))
    returned_topo = read_topology(in_dir / mesh_filename(0))
    gate = validity_gate(sent, returned, returned_topo, tol)

    recon = rc.reconstruct(returned.vertices, returned.uv)
    remesh = tessellate(recon, N_U, N_V)
    drift = hausdorff(returned.vertices, remesh.vertices)
    deviation = deviation_field(remesh.vertices, returned.vertices)
    constraints = check_constraints(desc, recon, source=source_surface)
    curv = curvature_deviation_field(recon, source_surface)
    clean_report = assemble_report(
        remesh,
        gate,
        deviation=deviation,
        drift=drift,
        constraints=constraints,
        provenance=desc.provenance,
        curvature_deviation=curv["deviation"],
        curvature_points=curv["points"],
    )
    print(
        f"  drift max {drift['max']:.5e}, mean {drift['mean']:.5e}; "
        f"positional deviation max {deviation.max():.5e} mm; "
        f"curvature deviation max {curv['deviation'].max():.5e} 1/mm"
    )
    _emit(clean_report, artifacts, "clean")

    # --- collision pass: gate stops, no reconstruction, pair marked ---
    print("\n[collision] flagged pass: gate stops, no reconstruction")
    write_payload(out_dir / mesh_filename(1), source, _descriptor(1))
    sent1 = read_topology(out_dir / mesh_filename(1))
    solve_morph(out_dir, in_dir, 1, "collision")
    returned_c, desc_c = read_payload(in_dir / mesh_filename(1))
    returned_topo_c = read_topology(in_dir / mesh_filename(1))
    gate_c = validity_gate(sent1, returned_c, returned_topo_c, tol)

    collision_report = assemble_report(
        returned_c,
        gate_c,
        node_ids=returned_topo_c.node_ids,
        provenance=desc_c.provenance,
    )
    print(
        f"  signals {gate_c.signals}; flagged pairs "
        f"{collision_report.collision_pairs[:3]}"
        + (" ..." if len(collision_report.collision_pairs) > 3 else "")
    )
    _emit(collision_report, artifacts, "collision")

    print(f"\nArtifacts written under {artifacts}")
    print("  The single-panel PNGs are the positional fallback: the deviation field")
    print("  colored on the geometry (clean) and the non-neighbor pair marked (collision).")
    print("  The comparison PNGs put positional deviation beside curvature deviation:")
    print("  the clean pass reads calm on the left and lit at the leading edge on the")
    print("  right, the same reconstruction. That contrast is the centerpiece slide.")
    print("  The field files are what the live Grasshopper display reads and colors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
