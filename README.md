# passthrough

passthrough is an instrument. It measures whether a mesh to NURBS to mesh round trip, carried across a process boundary, preserves the surface properties an optimization loop is sensitive to. It is not a reconstruction solver.

The full charter is in `AGENT.md`. The phased build and its gates are in `PLAN.md`. Specs are in `specs/`.

## Status

Phases 0 through 6 are built: the harness and drift metric, the classical reconstructor and the exchange, the loss budget, the synthetic-solver loop, the identity and validity gate, the reporting layer with its field file and static render, and the reverse-problem boundary ladder. Phase 7a adds the run contract: a run entry point that takes an externally produced tagged mesh, runs the loop on it, and writes a result, a field, and a status marker, with the whole handshake provable by hand with files. See `PLAN.md` for the phased build and its gates.

Next: Phase 7b, the C# Grasshopper bench that exports the payload and imports the status marker this phase defines.

## Setup

```powershell
# from the repo root
uv sync --group dev
```

This installs the project and the dev group (pytest and matplotlib) into a managed
environment in one step. No manual pip install, no Docker, no admin rights.

## Run the tests

```powershell
uv run python -m pytest
```

Every test has a known-correct answer because the source surface is constructed, not
loaded. They cover surface construction, tessellation, the drift metric, the exchange,
the validity gate, the reporting layer, the reverse-problem ladder, and the Phase 7a
run contract.

## Run a phase gate

```powershell
uv run python scripts\run_phase0.py
```

Expected: a built surface, an identity drift of zero, and `PHASE 0 GATE: PASS`. The
later phases have their own runners under `scripts/`.

## Run the round trip by hand (Phase 7a)

The run entry point takes an externally produced, tagged mesh, runs the loop on it,
and writes a result, a deviation field, and a status marker. Prove it with files:

```powershell
uv run python scripts\run_roundtrip.py emit incoming.json
uv run python scripts\run_roundtrip.py run incoming.json return
```

The first command writes a known-good payload from the synthetic wing (the stand-in
for a Grasshopper export). The second runs the loop and writes `return\status.json`
saying `done`. Feed it a malformed payload (`emit incoming.json --kind malformed`) and
the status says `failed` with a reason, without crashing.

## Layout

```
AGENT.md            project charter (read first, every session)
PLAN.md             phased build with approval gates
specs/              geometry and exchange specs
src/passthrough/    the engine (geometry, driftgauge meter; more per phase)
tests/              validation, one known answer per test
scripts/            run_phase0.py and later phase runners
exchange/           file-contract directories (out/, in/), used from Phase 1
```

## The two seams

passthrough is built around two swappable black boxes, each one swap from its real version with the contract written down.

- The exterior solver (`solver_stub`, Phase 3) is where real CFD would connect and visibly does not. It applies a synthetic, deterministic deformation so ground truth holds.
- The reconstructor (`reconstruct`, Phase 1) is where a learned differentiable map would connect and visibly does not. A `ClassicalReconstructor` implements it now; a `LearnedReconstructor` is a documented, unbuilt seam.
