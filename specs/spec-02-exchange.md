# spec-02-exchange.md — The exchange contract

Scope: the protocol that carries geometry across a process boundary and back. This is the encode/decode work in Phase 1 and the synthetic solver in Phase 3. It is the part that makes driftgauge mirror the team's actual workflow shape: they take meshes out to a solver and need updated meshes back.

## Principle

The exchange is a file contract, not a server. Two processes share a directory and a schema. The geometry side writes a mesh and a descriptor; the solver side reads it, returns a modified mesh; the geometry side reads that back. The contract is visible on disk, which makes it testable and honest. A live service, if ever wanted, is a wrapper around this same contract (PLAN.md Phase 8), not a different design.

The far side is a black box behind the contract. It does not need to be a different application to prove tool-agnosticism. It needs to be a different process behind a defined format. It stays Python.

## The protocol

Directories:
- `exchange/out/` — geometry side writes here. The payload going to the solver.
- `exchange/in/` — solver side writes here. The updated payload coming back.

File naming:
- `mesh_NNN.json` where NNN is a zero-padded iteration index. The same index in `out/` and `in/` is one round trip.

Lifecycle (one iteration):
1. Geometry side tessellates the current surface and writes `exchange/out/mesh_NNN.json`.
2. Solver side reads `exchange/out/mesh_NNN.json`, applies its deformation, writes `exchange/in/mesh_NNN.json`.
3. Geometry side reads `exchange/in/mesh_NNN.json`, decodes, reconstructs, measures drift and constraint residuals.

Keep the two sides as separate entry points (separate scripts or commands) so the boundary is real, not a function call dressed up as one.

## Mesh encoding

The payload is plain JSON. Readable, diffable, tool-agnostic.

Fields:
- `vertices`: array of [x, y, z].
- `faces`: array of index tuples (quads native; triangles allowed).
- `uv`: array of [u, v], one per vertex. The parameterization travels with the mesh. This is the fixed-parameterization assumption made physical, and it is what the reconstructor consumes.
- `units` and a short `frame` note, so the geometry is unambiguous across the boundary.

Encoding is lossless. Decode of an encode returns the same arrays within floating-point tolerance. This is asserted as a test (Phase 1 exchange-identity gate).

## The descriptor (the loss budget, serialized)

This is the idea that makes the exchange more than plumbing. The descriptor rides alongside the mesh and declares what the far side is not allowed to lose. It is the loss budget written into the protocol the two sides share.

Fields:
- `constraints`: a list of declared constraints, each with a type and a tolerance. Examples:
  - `{ "type": "ruled", "tolerance": 1e-4 }`
  - `{ "type": "g2_spanwise", "tolerance": <curvature continuity tolerance> }`
- `preserve`: named regions or properties that must survive (for example, the leading-edge region's curvature class).
- `provenance`: the source surface identity and the iteration index, so a returned mesh can be tied back to what was sent.

The constraint checker reads the budget from the descriptor, not from a hardcoded value (Phase 2 budget-carried gate). The thing you are not allowed to lose is declared in the contract, then checked against the returned geometry. Declaration and verification share one source of truth.

## encode / decode

- `encode(mesh, descriptor) -> json` writes the payload and the descriptor (one file with both, or a mesh file plus a sidecar; pick one and hold it).
- `decode(json) -> (mesh, descriptor)` returns the same data model spec-01 defines.
- Round-trip lossless within tolerance. Tested.

## solver_stub

The far side. No CFD. A known, deterministic deformation that preserves ground truth.

Behavior:
- Reads `exchange/out/mesh_NNN.json`.
- Identity mode (Phase 1): returns the mesh unchanged. Drift across the boundary must be zero. This isolates the exchange from the fit.
- Synthetic mode (Phase 3): applies a smooth, deterministic normal-direction displacement field, shaped to resemble what a pressure field would do to a wing surface. Deterministic means a given input yields exactly one output, so the drift it produces is a known stimulus.
- Writes `exchange/in/mesh_NNN.json`, preserving the UV array and the descriptor so the returned mesh is still tied to its parameterization and its budget.

The synthetic field is a stand-in for real physics. State that plainly in the module. It is the seam where a real solver would connect, and the contract is what a real solver would have to honor.

## Validation requirements

- Encode/decode lossless: decode(encode(x)) equals x within tolerance.
- Exchange identity: with the identity solver, round-trip drift equals fit-only drift. The boundary adds nothing. (Phase 1 gate.)
- Descriptor carried: the budget written to `out/` is present and unchanged when read back from `in/`, and the checker reads constraints from it. (Phase 2 gate.)
- Stimulus response: a larger synthetic deformation yields a larger, monotonic drift; a deformation that breaks the declared budget is caught. (Phase 3 gate.)

## Implementation notes

- JSON for readability and diffability. If payload size becomes a problem at fine tessellation, a binary sidecar for the vertex array is allowed, but the descriptor stays human-readable.
- Two entry points (geometry side, solver side) so the boundary is genuine. PowerShell commands for both.
- No server, no socket, no port. Files only. No Docker, no admin.

## Named boundaries surfaced here

- The solver is synthetic. The real-CFD boundary, and the first of the two seams.
- The exchange is files. The deployment boundary; a service is a later wrapper, not a redesign.

State both where `solver_stub.py` and the exchange entry points live.
