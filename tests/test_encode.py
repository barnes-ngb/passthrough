"""Exchange encode/decode tests. Lossless within tolerance, asserted directly."""

from __future__ import annotations

import json

import numpy as np

from passthrough.encode import (
    Descriptor,
    decode,
    encode,
    mesh_filename,
    read_payload,
    write_payload,
)
from passthrough.geometry import build_wing_surface, tessellate


def _sample_descriptor() -> Descriptor:
    return Descriptor(
        constraints=[
            {"type": "ruled", "tolerance": 1e-4},
            {"type": "g2_spanwise", "tolerance": 5e-3},
        ],
        preserve={"leading_edge": "curvature_class"},
        provenance={"source": "wing_v0", "iteration": 0},
    )


def test_encode_decode_lossless():
    # decode(encode(x)) returns the same arrays within tolerance. The arrays go
    # out through json's full-precision float repr, so this is exact in practice.
    ns = build_wing_surface()
    mesh = tessellate(ns, 24, 10)
    desc = _sample_descriptor()

    mesh2, desc2 = decode(encode(mesh, desc))

    assert np.allclose(mesh.vertices, mesh2.vertices, atol=1e-12)
    assert np.allclose(mesh.uv, mesh2.uv, atol=1e-12)
    assert np.array_equal(mesh.faces, mesh2.faces)


def test_descriptor_round_trips():
    desc = _sample_descriptor()
    _, desc2 = decode(encode(tessellate(build_wing_surface(), 6, 5), desc))
    assert desc2.constraints == desc.constraints
    assert desc2.preserve == desc.preserve
    assert desc2.provenance == desc.provenance


def test_round_trip_survives_json_string():
    # The payload must survive a real JSON serialize/parse, not just the dict
    # form. This is what crosses the file boundary.
    mesh = tessellate(build_wing_surface(), 20, 8)
    desc = _sample_descriptor()
    text = json.dumps(encode(mesh, desc))
    mesh2, desc2 = decode(json.loads(text))
    assert np.allclose(mesh.vertices, mesh2.vertices, atol=1e-12)
    assert np.allclose(mesh.uv, mesh2.uv, atol=1e-12)
    assert desc2.constraints == desc.constraints


def test_write_and_read_payload(tmp_path):
    mesh = tessellate(build_wing_surface(), 16, 8)
    desc = _sample_descriptor()
    path = tmp_path / mesh_filename(0)
    write_payload(path, mesh, desc)
    assert path.exists()
    mesh2, desc2 = read_payload(path)
    assert np.allclose(mesh.vertices, mesh2.vertices, atol=1e-12)
    assert np.allclose(mesh.uv, mesh2.uv, atol=1e-12)
    assert np.array_equal(mesh.faces, mesh2.faces)
    assert desc2.provenance == desc.provenance


def test_mesh_filename_is_zero_padded():
    assert mesh_filename(0) == "mesh_000.json"
    assert mesh_filename(7) == "mesh_007.json"
    assert mesh_filename(123) == "mesh_123.json"
