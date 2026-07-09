"""The FFD morph: a trivariate Bernstein lattice standing in for a
simulation-driven free-form deformation.

The two claims a reader should be able to check: the undisplaced lattice is the
identity map (Bernstein partition of unity plus linear precision), and the
deformation is scale-equivariant because the lattice is built from the bounding
box and the handle displacement is a fraction of its diagonal. FFD acts on
space, so identity, topology, and uv survive by construction: the morph where
Boundary 1 is closed for free and the deviation story is representation."""

import dataclasses

import numpy as np
import pytest

from passthrough.encode import topology_from_mesh
from passthrough.geometry import build_wing_surface, tessellate
from passthrough.run import default_descriptor, run_loop
from passthrough.solver_stub import MORPHS, ffd_morph


@pytest.fixture()
def wing_mesh():
    return tessellate(build_wing_surface(), 24, 12)


def test_zero_strength_is_the_identity_map(wing_mesh):
    out = ffd_morph(wing_mesh, strength=0.0)
    assert np.allclose(out.vertices, wing_mesh.vertices, atol=1e-9)


def test_ffd_is_scale_equivariant(wing_mesh):
    morphed = ffd_morph(wing_mesh)
    big = dataclasses.replace(wing_mesh, vertices=wing_mesh.vertices * 36.0)
    morphed_big = ffd_morph(big)
    assert np.allclose(morphed_big.vertices, morphed.vertices * 36.0, rtol=1e-9)


def test_ffd_displaces_on_the_order_of_the_diagonal_fraction(wing_mesh):
    v = wing_mesh.vertices
    diag = float(np.linalg.norm(v.max(axis=0) - v.min(axis=0)))
    moved = ffd_morph(wing_mesh).vertices
    dmax = float(np.linalg.norm(moved - v, axis=1).max())
    assert 0.02 * diag < dmax < 0.10 * diag


def test_ffd_leaves_the_carried_contract_untouched(wing_mesh):
    out = ffd_morph(wing_mesh)
    assert np.array_equal(out.faces, wing_mesh.faces)
    assert np.allclose(out.uv, wing_mesh.uv)
    assert len(out.vertices) == len(wing_mesh.vertices)


def test_ffd_is_registered(wing_mesh):
    assert "ffd" in MORPHS


def test_ffd_passes_the_gate_and_reconstructs(wing_mesh):
    topo = topology_from_mesh(wing_mesh)
    report = run_loop(wing_mesh, default_descriptor(), topo, morph="ffd")
    assert report.validity.reconstruct
    assert report.surface is not None
    assert np.isfinite(report.drift["max"])
