"""Run the Phase 3 loop end-to-end and print the result. One command.

    python scripts/run_phase3.py

This drives the geometry side of the loop: build the source, tessellate, write
the payload to exchange/out, call the solver side in synthetic mode as a separate
step, read the deformed payload from exchange/in, reconstruct, re-tessellate, and
report. It runs the stimulus sweep, the reporting divergence, and the
budget-caught demonstration, and checks the Phase 3 gate.

The synthetic solver is not physics. It applies a fixed, deterministic,
normal-direction bump scaled by one magnitude parameter. No CFD (AGENT.md hard
do-not list). Known input means known output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import rhino3dm as r3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from passthrough.constraints import check_constraints, curvature_residual  # noqa: E402
from passthrough.encode import Descriptor, mesh_filename, read_payload, write_payload  # noqa: E402
from passthrough.geometry import build_wing_surface, tessellate  # noqa: E402
from passthrough.driftgauge import hausdorff  # noqa: E402
from passthrough.reconstruct import ClassicalReconstructor  # noqa: E402
from passthrough.solver_stub import solve_synthetic  # noqa: E402

N_U, N_V = 40, 16
MAGNITUDES = [0.0, 0.02, 0.04, 0.08, 0.16]


def _loop(source_surface, reconstructor, magnitude, out_dir, in_dir, index, curvature=True):
    """One round trip at a magnitude, across the real file exchange."""
    source_mesh = tessellate(source_surface, N_U, N_V)
    desc = Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-2, "direction": 1}],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": index},
    )
    write_payload(out_dir / mesh_filename(index), source_mesh, desc)
    solve_synthetic(out_dir, in_dir, index, magnitude)
    deformed, returned_desc = read_payload(in_dir / mesh_filename(index))

    recon = reconstructor.reconstruct(deformed.vertices, deformed.uv)
    remesh = tessellate(recon, N_U, N_V)
    return {
        "deformed": deformed,
        "recon": recon,
        "descriptor": returned_desc,
        "drift": hausdorff(deformed.vertices, remesh.vertices),
        "curvature": curvature_residual(recon, source_surface) if curvature else None,
    }


def make_ruled_surface() -> r3.NurbsSurface:
    pts_a = [r3.Point3d(0, 0, 0), r3.Point3d(1, 0, 0.3), r3.Point3d(2, 0, 0.1)]
    pts_b = [r3.Point3d(0, 2, 0.2), r3.Point3d(1, 2, 0.5), r3.Point3d(2, 2, 0.4)]
    ca = r3.Curve.CreateControlPointCurve(pts_a, 2)
    cb = r3.Curve.CreateControlPointCurve(pts_b, 2)
    return r3.NurbsSurface.CreateRuledSurface(ca, cb)


def main() -> int:
    out_dir = ROOT / "exchange" / "out"
    in_dir = ROOT / "exchange" / "in"
    ns = build_wing_surface()

    # --- stimulus response: drift vs magnitude, Phase 1 reconstructor ---
    # The fixed 7x4 cubic basis, so magnitude zero ties to the Phase 1 honest-fit
    # number exactly.
    rc_stim = ClassicalReconstructor(n_ctrl_u=7, n_ctrl_v=4)
    source_mesh = tessellate(ns, N_U, N_V)
    fit_surf = rc_stim.reconstruct(source_mesh.vertices, source_mesh.uv)
    fit_only = hausdorff(source_mesh.vertices, tessellate(fit_surf, N_U, N_V).vertices)

    print("Stimulus response (7x4 fixed fit, drift of reconstruction vs deformed mesh):")
    print(f"  {'magnitude':>10} {'drift_max':>14}")
    stim_drifts = []
    for i, m in enumerate(MAGNITUDES):
        rt = _loop(ns, rc_stim, m, out_dir, in_dir, i, curvature=False)
        stim_drifts.append(rt["drift"]["max"])
        print(f"  {m:10.3f} {rt['drift']['max']:14.5e}")

    zero_is_fit_only = abs(stim_drifts[0] - fit_only["max"]) < 1e-12
    nonzero_floor = stim_drifts[0] > 1e-5
    monotonic = all(b > a for a, b in zip(stim_drifts, stim_drifts[1:]))
    print(f"  magnitude 0 == fit-only drift ({fit_only['max']:.5e}): {zero_is_fit_only}")
    print(f"  magnitude 0 nonzero (fixed-fit floor):                 {nonzero_floor}")
    print(f"  drift monotonic in magnitude:                          {monotonic}")

    # --- reporting divergence: curvature residual grows faster than drift ---
    # A richer 12x8 basis: it fits the undeformed source closely, so both
    # baselines reflect the deformation and the divergence reads against a clean
    # zero. The point of interest the presentation is built on.
    rc_div = ClassicalReconstructor(n_ctrl_u=12, n_ctrl_v=8)
    print("\nReporting divergence (12x8 fixed fit):")
    print(
        f"  {'magnitude':>10} {'drift_max':>14} {'curv_max':>14} "
        f"{'drift/d0':>10} {'curv/c0':>10}"
    )
    div_drift, div_curv = [], []
    for i, m in enumerate(MAGNITUDES):
        rt = _loop(ns, rc_div, m, out_dir, in_dir, i)
        div_drift.append(rt["drift"]["max"])
        div_curv.append(rt["curvature"]["max"])
    d0, c0 = div_drift[0], div_curv[0]
    for m, d, c in zip(MAGNITUDES, div_drift, div_curv):
        print(f"  {m:10.3f} {d:14.5e} {c:14.5e} {d / d0:10.3f} {c / c0:10.3f}")
    diverges = all(c / c0 > d / d0 for d, c in zip(div_drift[1:], div_curv[1:]))
    print("  curvature residual grows faster than drift (curv/c0 > drift/d0):")
    print(f"    {diverges}")
    print(
        f"  resemblance holds: max drift {div_drift[-1]:.3e} on a unit chord, "
        f"while curvature residual reaches {div_curv[-1]:.3e}"
    )

    # --- budget caught: a ruled input bowed off ruled ---
    rc_ruled = ClassicalReconstructor(n_ctrl_u=5, n_ctrl_v=5)
    ruled = make_ruled_surface()
    print("\nBudget caught (ruled input, tolerance 1e-2 on the ruled constraint):")
    print(f"  {'magnitude':>10} {'ruled_resid':>14} {'verdict':>10}")
    verdicts = {}
    for label, m, i in [("zero", 0.0, 0), ("within", 0.01, 1), ("over", 0.05, 2)]:
        rt = _loop(ruled, rc_ruled, m, out_dir, in_dir, i, curvature=False)
        result = check_constraints(rt["descriptor"], rt["recon"])[0]
        verdicts[label] = result
        verdict = "PASS" if result.passed else "CAUGHT"
        print(f"  {m:10.3f} {result.residual:14.5e} {verdict:>10}")
    budget_caught = (
        verdicts["zero"].passed
        and verdicts["within"].passed
        and not verdicts["over"].passed
    )
    print(f"  small deformation within budget, large one caught:     {budget_caught}")

    passed = zero_is_fit_only and nonzero_floor and monotonic and diverges and budget_caught
    print(f"\nPHASE 3 GATE: {'PASS' if passed else 'FAIL'}")
    print("  Stimulus response: drift monotonic in a known deformation, reducing")
    print("  to the fit-only case at zero. Budget caught: a ruled input bowed off")
    print("  ruled is flagged by the unchanged Phase 2 checker.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
