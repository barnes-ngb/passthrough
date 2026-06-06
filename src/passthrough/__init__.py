"""driftgauge: an instrument that measures whether a mesh to NURBS to mesh round
trip preserves the surface properties an optimization loop is sensitive to.

It is not a reconstruction solver. See AGENT.md.
"""

from passthrough.geometry import Mesh, build_wing_surface, tessellate, evaluate, control_net
from passthrough.driftgauge import hausdorff, nearest_distances
from passthrough.reconstruct import (
    Reconstructor,
    ClassicalReconstructor,
    LearnedReconstructor,
    clamped_uniform_knots,
)
from passthrough.encode import Descriptor, encode, decode, write_payload, read_payload, mesh_filename
from passthrough.solver_stub import (
    solve_identity,
    solve_synthetic,
    deform,
    displacement,
    pressure_bump,
    surface_normals,
)

__all__ = [
    "Mesh",
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
    "write_payload",
    "read_payload",
    "mesh_filename",
    "solve_identity",
    "solve_synthetic",
    "deform",
    "displacement",
    "pressure_bump",
    "surface_normals",
]
