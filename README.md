# driftgauge

driftgauge is an instrument. It measures whether a mesh to NURBS to mesh round trip, carried across a process boundary, preserves the surface properties an optimization loop is sensitive to. It is not a reconstruction solver.

The full charter is in `AGENT.md`. The phased build and its gates are in `PLAN.md`. Specs are in `specs/`.

## Status

Phase 0 complete. The harness builds a synthetic wing-section surface from a directly-defined control net, tessellates it on a UV grid, and measures drift with a Hausdorff metric. The Phase 0 gate passes: a surface measured against itself reads zero drift. This is the foundation every later measurement is checked against.

Next: Phase 1, the classical reconstructor and the exchange round trip. See `PLAN.md`.

## Setup (Windows, PowerShell)

```powershell
# from the repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

No Docker and no admin rights are required.

## Run the Phase 0 gate

```powershell
python scripts\run_phase0.py
```

Expected: a built surface, an identity drift of zero, and `PHASE 0 GATE: PASS`.

## Run the tests

```powershell
python -m pytest
```

Twelve tests covering surface construction, tessellation, parameterization integrity, metric correctness, and the identity gate. Every test has a known-correct answer because the source surface is constructed, not loaded.

## Layout

```
AGENT.md            project charter (read first, every session)
PLAN.md             phased build with approval gates
specs/              geometry and exchange specs
src/driftgauge/     the engine (geometry, metrics; more per phase)
tests/              validation, one known answer per test
scripts/            run_phase0.py and later phase runners
exchange/           file-contract directories (out/, in/), used from Phase 1
```

## The two seams

driftgauge is built around two swappable black boxes, each one swap from its real version with the contract written down.

- The exterior solver (`solver_stub`, Phase 3) is where real CFD would connect and visibly does not. It applies a synthetic, deterministic deformation so ground truth holds.
- The reconstructor (`reconstruct`, Phase 1) is where a learned differentiable map would connect and visibly does not. A `ClassicalReconstructor` implements it now; a `LearnedReconstructor` is a documented, unbuilt seam.
