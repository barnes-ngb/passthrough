"""The solver side, as a separate command. Identity mode for Phase 1.

    python scripts/run_solver.py --index 0

Reads exchange/out/mesh_NNN.json, writes the mesh back unchanged to
exchange/in/mesh_NNN.json, preserving uv and descriptor. This is a separate entry
point from the geometry side (scripts/run_phase1.py) so the process boundary is
real. No CFD: the solver is synthetic, and in this phase it is the identity.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from passthrough.solver_stub import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
