"""Phase 0 gate.

The instrument must read zero when nothing changed. Tessellate the source
surface, then measure it against itself. Drift must be zero within
floating-point tolerance. If this fails, the metric is wrong and nothing
downstream can be trusted.
"""

from __future__ import annotations

from passthrough.geometry import build_wing_surface, tessellate
from passthrough.driftgauge import hausdorff

GATE_TOL = 1e-9


def test_phase0_identity_gate():
    ns = build_wing_surface()
    mesh = tessellate(ns, 40, 16)
    drift = hausdorff(mesh.vertices, mesh.vertices)
    assert drift["max"] < GATE_TOL
    assert drift["mean"] < GATE_TOL
    assert drift["median"] < GATE_TOL
