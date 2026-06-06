"""Run the Phase 0 gate and print the result. One command, human-readable.

    python scripts/run_phase0.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from passthrough.geometry import build_wing_surface, tessellate  # noqa: E402
from passthrough.driftgauge import hausdorff  # noqa: E402

GATE_TOL = 1e-9


def main() -> int:
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)

    print("Source surface built.")
    print(f"  valid: {ns.IsValid}")
    print(f"  vertices: {mesh.vertices.shape[0]}, quad faces: {mesh.faces.shape[0]}")
    lo, hi = mesh.vertices.min(0), mesh.vertices.max(0)
    print(f"  bbox min: {lo.round(4).tolist()}  max: {hi.round(4).tolist()}")

    drift = hausdorff(mesh.vertices, mesh.vertices)
    print("\nIdentity drift (surface measured against itself):")
    print(f"  max:    {drift['max']:.3e}")
    print(f"  mean:   {drift['mean']:.3e}")
    print(f"  median: {drift['median']:.3e}")

    passed = drift["max"] < GATE_TOL
    print(f"\nPHASE 0 GATE: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
