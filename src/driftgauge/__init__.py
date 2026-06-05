"""driftgauge: an instrument that measures whether a mesh to NURBS to mesh round
trip preserves the surface properties an optimization loop is sensitive to.

It is not a reconstruction solver. See AGENT.md.
"""

from driftgauge.geometry import Mesh, build_wing_surface, tessellate, evaluate, control_net
from driftgauge.metrics import hausdorff, nearest_distances

__all__ = [
    "Mesh",
    "build_wing_surface",
    "tessellate",
    "evaluate",
    "control_net",
    "hausdorff",
    "nearest_distances",
]
