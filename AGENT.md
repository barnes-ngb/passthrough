# AGENT.md — passthrough

> The project is passthrough. `driftgauge` is the drift-measurement module inside the passthrough system; it keeps its name as a named sub-component, while the system identity (repo, package, charter, docs) is passthrough.

This file is the project charter. Read it at the start of every session before writing or changing code. It defines what this project is, what it is not, and how work proceeds. If a request conflicts with this charter, stop and raise the conflict rather than resolving it silently.

## Purpose

driftgauge is an instrument. It measures whether a mesh to NURBS to mesh round trip, carried across a process boundary, preserves the surface properties an optimization loop is sensitive to.

It is not a reconstruction solver. It is the harness around the reconstruction: the part that declares what must be preserved and measures whether it was. The reconstructor itself is bounded and swappable, not the point.

## The thesis this serves

A reconstruction earns its place in an optimization loop by behaving like the part across iterations, not by resembling it. That behavior is decided before you fit anything, by what you declare the translation is not allowed to lose.

The whole project exists to make that sentence executable: declare the loss budget, run the round trip, measure the drift, check the budget held.

## The boundary that must not move

The hardest discipline in this project is staying an instrument and not drifting into a solver.

General mesh-to-feature-CAD reconstruction is an unsolved research problem owned by the team this is built for. If driftgauge starts to look like a reconstruction engine, it crosses a line and a research audience will find the seam immediately. So:

- The reconstructor is deliberately bounded. It assumes a fixed parameterization and reconstructs a single surface. Those assumptions are not hidden. They are the named frontier (see "Named boundaries").
- The exterior solver is a synthetic stub. No real CFD. Ever. The point is a known, deterministic deformation that preserves ground truth, not a physically accurate one.
- The exchange is a file contract. No server. No running service.

These three boundaries are what keep the project honest and finishable. Do not relax them to make the demo more impressive. The boundaries are the credibility.

## Named boundaries (the frontier, stated on purpose)

Every simplifying assumption maps to a real open problem the audience works on. State each one plainly in code comments and docs. Do not paper over them.

- Fixed parameterization (mesh points carry their source UV). This assumption is the parameterization gap. Naming it is honesty, not weakness.
- Single surface, not a multi-patch B-rep with preserved relationships. This boundary is the reverse problem proper.
- Classical fit, not a learned differentiable map. This boundary is the gradient-transfer problem and the place a learned reconstructor would enter.

## Honesty categories (apply to every component)

Each module carries one of three honesty levels. Keep them accurate in comments and commit messages. This is non-negotiable.

1. Authored by the owner, agent helped type. The instrument logic: metrics, constraints, exchange, harness.
2. Known method, owner integrated and validated. The bounded classical reconstructor (least-squares NURBS fit).
3. Not built, seam architected. The learned reconstructor. A documented stub with the same interface. Do not implement it.

If a piece of work does not fit category 1 or 2 honestly, it does not ship.

## Architecture

```
passthrough/
  AGENT.md                  # this charter
  PLAN.md                   # phased build, approval gates
  README.md                 # instrument-not-solver, stated first
  specs/
    spec-01-geometry.md
    spec-02-exchange.md
    spec-03-metrics-constraints.md   # written when Phase 2 starts
  exchange/
    out/                    # geometry side writes mesh + descriptor here
    in/                     # solver side writes updated mesh here
    schema.md               # the contract (mirrors spec-02)
  src/passthrough/
    geometry.py             # source NURBS, UV-grid tessellation, I/O (rhino3dm)
    encode.py               # mesh + descriptor <-> exchange format
    solver_stub.py          # reads out/, applies synthetic deformation, writes in/
    reconstruct.py          # Reconstructor protocol + ClassicalReconstructor  [SEAM]
    driftgauge.py           # the drift meter: Hausdorff (max/mean/median), per-point deviation field
    constraints.py          # the loss budget made executable; pass/fail + residual
    report.py               # drift + residuals + deviation field for display
  tests/
    test_geometry.py
    test_encode.py
    test_metrics.py
    test_constraints.py
    test_roundtrip.py
```

## The pipeline

source NURBS -> tessellate -> encode -> exchange/out -> solver_stub (synthetic deform) -> exchange/in -> decode -> reconstruct -> re-tessellate -> drift + constraints -> report -> display

## The two seams

Both are swappable. Both are honest about what is and is not built.

1. `solver_stub` is where real CFD would live and visibly does not. Interface: read a mesh + descriptor from `exchange/out`, return a deformed mesh to `exchange/in`. Default implementation applies a synthetic, deterministic deformation.
2. `reconstruct.Reconstructor` is where a learned differentiable map would live and visibly does not. Protocol with one method: `reconstruct(points, uv) -> NurbsSurface`. `ClassicalReconstructor` implements it now. `LearnedReconstructor` is a documented stub, same interface, not implemented.

Keep both interfaces narrow and stable. The value of the project is that each black box is one swap away from its real version, with the contract written down.

## Working rules

- Validation-first. Tests before trust. "Looks right" is not sufficient. Synthetic ground truth means every test has a known answer (see PLAN.md gates).
- Bounded delegation. Work in steps small enough that the owner understands each encapsulation, even if not every internal line.
- Architecture-emphasis. The structure and the specs carry the rigor. Get the seams and contracts right before optimizing any single computation.
- Approval gates. Each phase ends at a human approval gate defined in PLAN.md. Do not begin the next phase until the current gate passes and the owner approves. Stop at the gate and report the result against the gate's pass test.
- Verify tool capability before relying on it. rhino3dm is a data and evaluation library, not an algorithms library. Confirm a given call exists before building on it. If it does not, author the operation in numpy/scipy rather than reaching for a heavier dependency.

## Voice rules (for all prose this project produces: docs, comments, commit messages)

- No AI-tell vocabulary: leverage, seamless, streamline, passionate, thrives, highly versatile.
- No em dashes. Use periods, colons, semicolons, or spaced hyphens.
- No three-part parallel constructions.
- Senior IC posture. Direct, not apologetic. State what is true and defend it without hedging.

## Environment

- Windows, PowerShell in VS Code. All shell commands in PowerShell form.
- No Docker. No admin rights on the machine. Do not propose anything requiring either.
- Python is the engine. numpy and scipy for math. rhino3dm for NURBS and mesh data and file I/O.
- Rhino and Grasshopper are display only, fed from files. Headless Python is where correctness is proven.
- A C# Grasshopper plugin is an earned stretch (Phase 6), not a dependency.

## Hard do-not list

- Do not run or integrate real CFD. The solver is synthetic.
- Do not stand up a server or a running service. The exchange is files.
- Do not add OpenSCAD or CSG tooling. It cannot represent the geometry this project preserves.
- Do not implement `LearnedReconstructor`. Architect the seam, document it, leave it unbuilt.
- Do not claim to solve the reverse problem. driftgauge measures; it does not solve.
- Do not move computation into a compiled Grasshopper component to get a nicer picture. Math stays in tested Python.

## Session protocol

1. Read this charter and the current state of PLAN.md.
2. Identify the active phase and its gate.
3. Do the smallest unit of work that moves toward the gate.
4. Write or update tests alongside the code.
5. Stop at the gate. Report the result against the pass test. Wait for approval.
