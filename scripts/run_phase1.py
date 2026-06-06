"""Run the Phase 1 gates end-to-end and print the result. One command.

    python scripts/run_phase1.py

This drives the geometry side: build the source, tessellate, write the payload to
exchange/out, call the solver side (identity mode) as a separate step, read the
returned payload from exchange/in, reconstruct, re-tessellate, and report drift.
It checks both Phase 1 gates: exchange identity and honest fit.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from passthrough.encode import Descriptor, mesh_filename, read_payload, write_payload  # noqa: E402
from passthrough.geometry import build_wing_surface, tessellate  # noqa: E402
from passthrough.driftgauge import hausdorff  # noqa: E402
from passthrough.reconstruct import ClassicalReconstructor  # noqa: E402
from passthrough.solver_stub import solve_identity  # noqa: E402

N_U, N_V = 40, 16
HONEST_FIT_MAX_BOUND = 3e-3
HONEST_FIT_NONZERO_FLOOR = 1e-5
EXCHANGE_EQUAL_TOL = 1e-12


def _drift(source_mesh, source_for_fit, rc):
    surf = rc.reconstruct(source_for_fit.vertices, source_for_fit.uv)
    remesh = tessellate(surf, N_U, N_V)
    return hausdorff(source_mesh.vertices, remesh.vertices)


def main() -> int:
    out_dir = ROOT / "exchange" / "out"
    in_dir = ROOT / "exchange" / "in"

    ns = build_wing_surface()
    source = tessellate(ns, N_U, N_V)
    rc = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)

    print("Source built and tessellated.")
    print(f"  vertices: {source.vertices.shape[0]}, quad faces: {source.faces.shape[0]}")
    print(f"  reconstructor: classical least-squares, fixed 7x4 cubic control net")

    # Fit-only path.
    fit_only = _drift(source, source, rc)

    # Round-trip path across the file boundary.
    desc = Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-4}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": 0},
    )
    index = 0
    written = write_payload(out_dir / mesh_filename(index), source, desc)
    solve_identity(out_dir, in_dir, index)
    returned, _ = read_payload(in_dir / mesh_filename(index))
    round_trip = _drift(source, returned, rc)

    print(f"\nWrote payload: {written.relative_to(ROOT)}")
    print("Fit-only drift (reconstruct straight from source):")
    print(f"  max: {fit_only['max']:.4e}  mean: {fit_only['mean']:.4e}")
    print("Round-trip drift (through exchange + identity solver):")
    print(f"  max: {round_trip['max']:.4e}  mean: {round_trip['mean']:.4e}")

    exchange_identity = all(
        abs(round_trip[k] - fit_only[k]) < EXCHANGE_EQUAL_TOL
        for k in ("max", "mean", "median")
    )
    honest_fit = (
        fit_only["max"] > HONEST_FIT_NONZERO_FLOOR
        and fit_only["max"] < HONEST_FIT_MAX_BOUND
    )

    print("\nGate 1 exchange identity (round-trip drift == fit-only drift):")
    print(f"  {'PASS' if exchange_identity else 'FAIL'}")
    print("Gate 2 honest fit (small, nonzero, bounded drift on unperturbed source):")
    print(
        f"  nonzero floor {HONEST_FIT_NONZERO_FLOOR:.0e} < {fit_only['max']:.4e} "
        f"< bound {HONEST_FIT_MAX_BOUND:.0e}: {'PASS' if honest_fit else 'FAIL'}"
    )
    print("  Drift is nonzero because the fixed 7x4 basis cannot represent the")
    print("  source's 8x4 control net; the fit returns the best projection.")

    passed = exchange_identity and honest_fit
    print(f"\nPHASE 1 GATES: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
