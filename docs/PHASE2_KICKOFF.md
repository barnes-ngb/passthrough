# Phase 2 kickoff for Claude Code

Paste into Claude Code web after Phase 1 is committed. Hands the build from Phase 1 (reconstructor + exchange, tests green) to Phase 2: the loss budget made executable. This is Move 2 of the presentation in code.

---

Read `AGENT.md` and `PLAN.md` first, then `specs/spec-01-geometry.md` and `specs/spec-02-exchange.md`, then skim the Phase 1 modules (`reconstruct.py`, `encode.py`, `solver_stub.py`) and the Cox-de Boor basis you authored. Hold the charter throughout, including the voice rules and the hard do-not list. Stay an instrument, not a solver.

Phase 1 is done and its 26 tests pass. Build Phase 2 only. Stop at the Phase 2 gate and report against its pass test. Do not start Phase 3 (no synthetic deformation solver yet).

First, write `specs/spec-03-metrics-constraints.md`. Define precisely what each constraint means and how its residual is computed, before writing the checker. Get the definitions reviewed in the spec, not buried in code.

Then build the constraint checker (`src/driftgauge/constraints.py`, honesty category 1). Two constraints:

1. Ruled preservation (the clean binary unit).
   - A ruled surface has straight rulings in one direction. Residual = the maximum deviation of the ruling isocurves from straight lines, sampled along each ruling.
   - The wing source surface is not ruled, so build a separate ruled fixture for this test. Use `NurbsSurface.CreateRuledSurface` if it behaves, or construct a degree-1-in-v surface directly. Verify the fixture is actually ruled before testing against it.
   - `IsoCurve` is available on the surface and is the natural way to extract rulings. Confirm it before relying on it.

2. Curvature preservation (the wing-relevant headline, the solver-sensitive property).
   - The loss budget for a wing protects the surface properties a flow solver is sensitive to. Curvature is the headline one. Residual = the deviation between the curvature field of the reconstruction and the curvature field of the source, sampled on a grid, reported as max and mean, with attention to the leading-edge region where curvature is highest.
   - This needs second derivatives. rhino3dm does not expose curvature (no `CurvatureAt`; it has `NormalAt` and `FrameAt` only). So author first and second parametric derivatives by extending the Cox-de Boor basis you already validated, and compute curvature from them. Cross-check the analytic curvature against finite differences on `PointAt` and assert they agree. This is the same verify-before-trust pattern Phase 1 used for the basis. Do not add a heavy dependency.

The checker reads the declared budget from the descriptor, not from hardcoded values. The descriptor's `constraints` list (each with a type and a tolerance) is the source of truth. The checker iterates the declared constraints, computes each residual, and reports pass/fail against the declared tolerance plus the raw residual. Declaration and verification share one source.

Keep constraint residuals semantically distinct from positional drift. A residual is the degree of constraint violation, reported separately from the Hausdorff drift number. Do not fold them together.

Gates (both must pass, with tests):
   - Ruled binary: the ruled fixture reads ruled (residual at or near zero, below its declared tolerance). Apply a known off-ruled deformation and the residual rises monotonically with the deformation magnitude. The checker detects what it claims to detect.
   - Budget carried: the descriptor written to `exchange/out` is present and unchanged when read back from `exchange/in`, and the checker reads its constraints from the descriptor rather than from a literal in the code. Add a test that changes a tolerance in the descriptor and confirms the checker's pass/fail flips accordingly.

Write tests alongside the code, each backed by a known answer from synthetic ground truth. Run `python -m pytest` and report the result. Then stop at the gate.

Place this file in `docs/` alongside the Phase 1 kickoff.
