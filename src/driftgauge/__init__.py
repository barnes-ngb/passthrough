"""driftgauge: an instrument that measures whether a mesh to NURBS to mesh round
trip preserves the surface properties an optimization loop is sensitive to.

It is not a reconstruction solver. See AGENT.md.
"""

from driftgauge.geometry import (
    Mesh,
    Topology,
    build_topology,
    build_wing_surface,
    tessellate,
    evaluate,
    control_net,
)
from driftgauge.metrics import hausdorff, nearest_distances
from driftgauge.reconstruct import (
    Reconstructor,
    ClassicalReconstructor,
    LearnedReconstructor,
    clamped_uniform_knots,
)
from driftgauge.encode import (
    Descriptor,
    encode,
    decode,
    decode_topology,
    write_payload,
    read_payload,
    read_payload_full,
    mesh_filename,
)
from driftgauge.solver_stub import solve, solve_identity, apply_mode
from driftgauge.validity import (
    ValidityResult,
    GateOutcome,
    check_identity_integrity,
    check_collision,
    check_fold,
    run_validity_gate,
)
from driftgauge.pipeline import IterationResult, process_return

__all__ = [
    "Mesh",
    "Topology",
    "build_topology",
    "build_wing_surface",
    "tessellate",
    "evaluate",
    "control_net",
    "hausdorff",
    "nearest_distances",
    "Reconstructor",
    "ClassicalReconstructor",
    "LearnedReconstructor",
    "clamped_uniform_knots",
    "Descriptor",
    "encode",
    "decode",
    "decode_topology",
    "write_payload",
    "read_payload",
    "read_payload_full",
    "mesh_filename",
    "solve",
    "solve_identity",
    "apply_mode",
    "ValidityResult",
    "GateOutcome",
    "check_identity_integrity",
    "check_collision",
    "check_fold",
    "run_validity_gate",
    "IterationResult",
    "process_return",
]
