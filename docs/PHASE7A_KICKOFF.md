# Phase 7a kickoff: the run contract and status handshake (Python, fully testable)

This is the cheap half of the round-trip demo, and the half you can validate end to
end with files by hand, before any C# or Rhino exists. Prove the contract here, where
failure is cheap, so the expensive Grasshopper half is built against something solid.

The shape of the demo this serves: three manual steps, each separately triggered,
nothing blocking. (1) export from Grasshopper writes an incoming payload, (2) trigger
the run, which reads the payload, runs the passthrough loop, and writes a status marker
when done, (3) import reads the result once it sees the marker. This kickoff builds
step 2 and the contracts on both sides of it. Grasshopper (7b) comes later.

Read `AGENT.md`, `PLAN.md`, then `encode.py` (the payload schema and identity/topology
tags), `solver_stub.py`, `validity.py`, `reconstruct.py`, `report.py` (the field and
report writers), and the Phase 4 and 5 runners. Hold the charter and voice rules.

PLAN numbering: this is Phase 7a. The C# Grasshopper bench is Phase 7b. The
live-service note becomes Phase 8.

Also do the packaging fix here, since it is Python and overdue: add a
`[dependency-groups]` dev group to `pyproject.toml` carrying `pytest` and
`matplotlib`, update the README so setup is `uv sync --group dev` then
`uv run python -m pytest`. Gate: a clean `uv sync --group dev` then
`uv run python -m pytest` gives the full count with no manual pip install.

---

## What this builds

A run entry point that treats an externally produced, tagged mesh as the ground-truth
input, runs the existing loop on it, and writes a result, a field, and a status marker.
Today the loop generates its own synthetic wing; this inverts it so the input comes
from outside, which is what lets Grasshopper own the source geometry later.

### 1. Accept an external tagged mesh

The premise of passthrough is carrying identity and topology across the boundary, so
the incoming payload is not a raw mesh. It is a mesh plus its tags: vertices, quad
faces, per-vertex node ids, edge adjacency, and face winding, in the same schema
`encode.py` already defines for the outgoing side. Add a reader that loads an incoming
payload in that schema and validates it: that the tags are present and internally
consistent (every face references existing vertices, adjacency matches the faces, node
ids are unique and cover the vertices). If the payload is malformed or missing tags,
that is a structured failure, not a crash. Do not invent tags that are absent; a mesh
without identity cannot make the trip, and saying so cleanly is correct behavior.

### 2. Run the loop on the external input

Given a valid incoming payload, run the loop we already have: the solver step (for the
demo, the synthetic morph modes are fine as the stand-in for an external solve; keep
them), the validity gate, and on a clean pass the reconstruct and the drift and
curvature measurement. Reuse the existing modules unchanged. Do not recompute or
duplicate any measurement; this is orchestration of tested pieces.

### 3. Write the result, the field, and the status marker

On completion, write three things to a return location:
- the result (the reconstructed geometry and the report, reusing `report.py`),
- the field file (`passthrough.deviation_field.v1`, reusing the existing writer, so the
  later Grasshopper import reads exactly what Phase 5 already produces),
- a **status marker**: a small JSON, for example `status.json`, that is the handshake
  the import step waits on. It carries: `status` of `done` or `failed`; on `done`, the
  relative paths to the result and field files and a few headline numbers (drift max,
  whether flagged, any signals); on `failed`, a human-readable `reason`. Write the
  status marker last and atomically (write to a temp name, then rename) so a reader
  never sees a half-written marker. This is the contract that makes the three-step flow
  safe: the import side reads `status.json`, refuses to pull until it sees `done`, and
  surfaces the `reason` if it sees `failed`.

A flagged pass (collision, fold, identity-not-preserved) is a `done` with
`flagged: true` and the signals, not a `failed`. `failed` is for a malformed payload or
an actual error. Keep that distinction clean: a caught collision is a successful run
that found a problem, not a failed run.

### 4. A runner that does it end to end with files

Add `scripts/run_roundtrip.py` that takes an incoming payload path and a return folder,
runs steps 1 to 3, and prints what it wrote and the status. This is the thing you
exercise by hand: feed it a payload, watch it produce a result, a field, and a
`status.json` saying `done`. Then feed it a deliberately malformed payload and watch it
produce a `status.json` saying `failed` with a reason, without crashing.

To make hand-testing real, also add a tiny helper that emits a valid incoming payload
from the existing synthetic wing (so you have a known-good input to feed the runner
before Grasshopper can produce one). This is the stand-in for the eventual Grasshopper
export, and it doubles as the fixture for the tests.

## Gates (with tests, all in Python, all runnable on your machine)

- Round-trip on a valid payload: feeding the synthetic-wing payload through the runner
  produces a result, a field that round-trips against the report, and a `status.json`
  with `status: done` and a drift max matching the existing Phase 5 numbers.
- External input matches internal: the loop run on the externally supplied payload
  gives the same drift as the loop run on the internally generated wing, confirming the
  inversion changed the source of the mesh, not the math.
- Flagged is done, not failed: a collision payload yields `status: done`,
  `flagged: true`, signals naming the pair, and no reconstruction, distinct from a
  failed status.
- Malformed payload fails cleanly: a payload missing tags or with inconsistent faces
  yields `status: failed` with a reason, and does not crash.
- Atomic marker: assert the status writer writes to a temp path and renames, so a
  partial marker is never visible.

Run `uv run python -m pytest`. Report the new count (prior plus the new tests, prior
unchanged) and the runner output for both the good and the malformed payload.

## Note for Nathan (not the agent)

This is the half you can prove by hand with files, exactly where you wanted to lean.
Once `run_roundtrip.py` turns a payload into a result and an honest `status.json`, both
done and failed, the contract is solid and the Grasshopper bench in 7b has something
real to export to and import from. The expensive Rhino work then only has to write that
payload schema and read that status marker, not invent a protocol. If anything about
the handshake feels wrong when you exercise it, we fix it here in the cheap half before
a line of C# is written.

---

## What was built (notes for the record)

- `src/passthrough/run.py`: the run entry point. `validate_incoming` and
  `read_incoming_payload` accept and check an external tagged mesh, raising
  `PayloadError` (a structured failure) on a missing tag, a face past the mesh, a
  non-unique node id, or an adjacency that does not match the faces. `run_loop` runs the
  synthetic solver step, the validity gate, and on a clean pass the reconstruction,
  drift, deviation field, constraints, and curvature deviation, all from the existing
  modules. The run holds only meshes, so the source surface for the curvature and
  constraint comparison is reconstructed from the incoming mesh with the same bounded
  fit. `write_status` writes the marker to a temp name and renames it into place.
  `run_roundtrip` ties it together and always writes the marker last. `emit_synthetic_payload`
  is the stand-in for the Grasshopper export.
- `scripts/run_roundtrip.py`: `emit` writes a good or malformed payload, `run` runs the
  loop and prints the status and the marker.
- `tests/test_run.py`: the five gates plus validation unit checks.
- `pyproject.toml`: the `[dependency-groups]` dev group, so `uv sync --group dev` then
  `uv run python -m pytest` runs the full suite with no manual pip install.
