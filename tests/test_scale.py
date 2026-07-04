"""Scale invariance of the validity regime.

Found 2026-07-04 by running the loop on a real Rhino surface at millimeter scale
(36 x 10, a 24 x 12 grid): the collision tolerance derivation used a hardcoded
absolute search radius, so any mesh whose spacing exceeded that radius produced an
infinite tolerance and every non-neighbor pair read as a collision. A clean surface
at drawing scale was flagged. The tolerance must scale with the geometry: the same
mesh in millimeters and in meters must gate identically.
"""

import dataclasses

import numpy as np
import pytest

from passthrough.encode import topology_from_mesh
from passthrough.geometry import build_wing_surface, tessellate
from passthrough.run import collision_tolerance, default_descriptor, run_loop


@pytest.fixture()
def wing_mesh():
    return tessellate(build_wing_surface(), 24, 12)


def _scaled(mesh, factor: float):
    return dataclasses.replace(mesh, vertices=mesh.vertices * factor)


def test_collision_tolerance_scales_with_the_geometry(wing_mesh):
    topo = topology_from_mesh(wing_mesh)
    base = collision_tolerance(wing_mesh, topo)
    for factor in (0.03, 10.0, 36.0, 500.0):
        scaled = collision_tolerance(_scaled(wing_mesh, factor), topo)
        assert np.isfinite(scaled)
        assert scaled == pytest.approx(base * factor, rel=1e-9)


def test_scaled_clean_mesh_passes_the_gate(wing_mesh):
    """The failure seen live: a clean 36x mesh must reconstruct, not flag."""
    big = _scaled(wing_mesh, 36.0)
    topo = topology_from_mesh(big)
    report = run_loop(big, default_descriptor(), topo, morph="clean")
    assert report.validity.reconstruct
    assert report.surface is not None


def test_scaled_collision_is_still_flagged(wing_mesh):
    """Scale invariance must not soften the gate: the collision morph on the
    scaled mesh is still a caught, named failure."""
    big = _scaled(wing_mesh, 36.0)
    topo = topology_from_mesh(big)
    report = run_loop(big, default_descriptor(), topo, morph="collision")
    assert not report.validity.reconstruct
    assert "collision" in report.validity.signals


def test_running_status_is_written_at_launch(tmp_path):
    """run_roundtrip announces itself before the work: a reader polling the return
    folder sees running, then done, never an absent marker mid-run."""
    import json

    from passthrough.run import emit_synthetic_payload, run_roundtrip

    payload = tmp_path / "payload.json"
    emit_synthetic_payload(payload)
    return_dir = tmp_path / "return"

    observed = []
    from passthrough import run as run_mod

    original = run_mod.run_loop

    def spying_run_loop(*args, **kwargs):
        status = json.loads((return_dir / "status.json").read_text())
        observed.append(status["status"])
        return original(*args, **kwargs)

    run_mod.run_loop = spying_run_loop
    try:
        summary = run_roundtrip(payload, return_dir, morph="clean")
    finally:
        run_mod.run_loop = original

    assert observed == ["running"]
    assert summary["status"] == "done"
