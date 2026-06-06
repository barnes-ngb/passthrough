"""The loop, with the validity gate wired in. See spec-04.

Honesty category 1: instrument logic, authored here.

The flow the team actually runs: carry identity and topology out, the solver
morphs, the mesh returns, the validity gate runs, and only a valid return is
reconstructed and gauged for drift. The gate sits after decode and before
reconstruction. An inadmissible mesh is never reconstructed.

This module holds no new geometry math. It reads the returned payload, runs the
gate from validity.py, and on a clean return calls the existing reconstructor,
re-tessellates, and measures drift and constraint residuals exactly as the earlier
phases did. Validity, drift, and residuals stay separate fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from driftgauge.constraints import ConstraintResult, check_constraints
from driftgauge.encode import read_payload_full
from driftgauge.geometry import Topology, tessellate
from driftgauge.metrics import hausdorff
from driftgauge.reconstruct import Reconstructor
from driftgauge.validity import GateOutcome, run_validity_gate

import rhino3dm as r3


@dataclass
class IterationResult:
    """One returned iteration, evaluated.

    valid:       True when the validity gate passed and reconstruction ran.
    gate:        the GateOutcome, always present. On an invalid return its
                 first_violation names what went wrong.
    drift:       the Hausdorff drift of the reconstruction against the returned
                 mesh, or None when the gate stopped the iteration.
    constraints: the constraint residuals, or None when the gate stopped.

    The three results are distinct fields. Validity is not folded into drift, and
    neither is folded into the constraint residuals.
    """

    valid: bool
    gate: GateOutcome
    drift: dict[str, float] | None = None
    constraints: list[ConstraintResult] | None = field(default=None)


def process_return(
    in_path: str | Path,
    sent_topology: Topology,
    reconstructor: Reconstructor,
    source_surface: r3.NurbsSurface,
    n_u: int,
    n_v: int,
    tolerance: float,
) -> IterationResult:
    """Decode a returned payload, gate it, and reconstruct only if it is valid.

    On a flagged return the iteration stops and reports the violation: no
    reconstruction is attempted on an inadmissible mesh. On a clean return the
    reconstruction proceeds, the surface is re-tessellated on the same grid, and a
    drift number plus the declared constraint residuals are produced as before.
    """
    mesh, descriptor, returned_topology = read_payload_full(in_path)

    gate = run_validity_gate(sent_topology, mesh, returned_topology, tolerance)
    if not gate.valid:
        return IterationResult(valid=False, gate=gate)

    surface = reconstructor.reconstruct(mesh.vertices, mesh.uv)
    remesh = tessellate(surface, n_u, n_v)
    drift = hausdorff(mesh.vertices, remesh.vertices)
    constraints = check_constraints(descriptor, surface, source_surface)
    return IterationResult(
        valid=True, gate=gate, drift=drift, constraints=constraints
    )
