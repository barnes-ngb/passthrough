"""The solver side of the exchange. The far side, behind the file contract.

See specs/spec-02-exchange.md and AGENT.md ("The two seams"). This is a separate
entry point from the geometry side so the process boundary is genuine, not a
function call dressed up as one.

Honesty category 1: instrument logic, authored here.

Named boundaries surfaced in this module:
    - The solver is synthetic. No CFD, ever (AGENT.md hard do-not list). This is
      the real-CFD seam: where a real solver would connect, honoring this same
      file contract. A real solver does not exist here and visibly does not.
    - The exchange is files. No server, no socket, no port.

Phase 1 implements identity mode only: read exchange/out/mesh_NNN.json, write the
mesh back unchanged to exchange/in/mesh_NNN.json, preserving the UV array and the
descriptor. Identity mode isolates the exchange from the fit: with it, drift
across the boundary must be zero, so any round-trip drift is the fit alone.

Synthetic mode (a smooth, deterministic normal-direction displacement field) is
Phase 3. It is not built here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from driftgauge.encode import mesh_filename, read_payload, write_payload


def solve_identity(out_dir: str | Path, in_dir: str | Path, index: int) -> Path:
    """Identity solver for one iteration.

    Reads exchange/out/mesh_NNN.json, writes the same mesh to
    exchange/in/mesh_NNN.json, preserving UV and descriptor. The returned mesh is
    unchanged, so the boundary crossing adds nothing.
    """
    src = Path(out_dir) / mesh_filename(index)
    dst = Path(in_dir) / mesh_filename(index)
    mesh, descriptor = read_payload(src)
    # Identity: return the mesh exactly as received. UV and descriptor ride along
    # so the returned mesh stays tied to its parameterization and its budget.
    return write_payload(dst, mesh, descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="driftgauge solver side (identity mode, Phase 1)."
    )
    parser.add_argument("--out-dir", default="exchange/out")
    parser.add_argument("--in-dir", default="exchange/in")
    parser.add_argument("--index", type=int, default=0)
    args = parser.parse_args(argv)
    dst = solve_identity(args.out_dir, args.in_dir, args.index)
    print(f"solver_stub identity wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
