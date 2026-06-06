"""Carried identity and topology. See spec-04.

The exchange carries a stable node ID per vertex, the edge adjacency, and the face
winding order. These ride across the boundary unchanged. The tests assert the
topology is built correctly from a mesh and survives the file round trip exactly.
"""

from __future__ import annotations

import numpy as np

from driftgauge.encode import (
    Descriptor,
    decode_topology,
    encode,
    mesh_filename,
    read_payload,
    read_payload_full,
    write_payload,
)
from driftgauge.geometry import build_topology, build_wing_surface, tessellate


def _descriptor() -> Descriptor:
    return Descriptor(
        constraints=[{"type": "ruled", "tolerance": 1e-4}],
        provenance={"source": "wing_v0", "iteration": 0},
    )


def test_build_topology_node_ids_default_to_indices():
    mesh = tessellate(build_wing_surface(), 8, 5)
    topo = build_topology(mesh)
    assert np.array_equal(topo.node_ids, np.arange(mesh.vertices.shape[0]))


def test_adjacency_matches_grid_neighbors():
    # On the quad grid an interior vertex has four edge-neighbors, an edge vertex
    # three, a corner two. This is the connectivity the collision check reads.
    n_u, n_v = 6, 5
    mesh = tessellate(build_wing_surface(), n_u, n_v)
    topo = build_topology(mesh)

    def grid_id(a, b):
        return a * n_v + b

    interior = grid_id(2, 2)
    assert sorted(topo.adjacency[interior]) == sorted(
        [grid_id(1, 2), grid_id(3, 2), grid_id(2, 1), grid_id(2, 3)]
    )

    corner = grid_id(0, 0)
    assert sorted(topo.adjacency[corner]) == sorted([grid_id(1, 0), grid_id(0, 1)])

    # Adjacency is symmetric: if a lists b, b lists a.
    for nid, nbrs in topo.adjacency.items():
        for nb in nbrs:
            assert nid in topo.adjacency[nb]


def test_face_nodes_are_faces_in_node_ids():
    mesh = tessellate(build_wing_surface(), 7, 6)
    topo = build_topology(mesh)
    # Default node IDs equal vertex indices, so face_nodes equals faces here. The
    # value of face_nodes is that the winding is anchored to identity, not order.
    assert np.array_equal(topo.face_nodes, mesh.faces)


def test_topology_survives_file_round_trip(tmp_path):
    mesh = tessellate(build_wing_surface(), 12, 8)
    topo = build_topology(mesh)
    path = tmp_path / mesh_filename(0)
    write_payload(path, mesh, _descriptor(), topo)

    _, _, topo2 = read_payload_full(path)
    assert np.array_equal(topo.node_ids, topo2.node_ids)
    assert np.array_equal(topo.face_nodes, topo2.face_nodes)
    assert topo.adjacency == topo2.adjacency  # int keys, sorted int neighbor lists


def test_adjacency_keys_are_integers_after_decode():
    # JSON object keys are strings on the wire. decode_topology must restore int
    # keys so the collision check's neighbor lookup works.
    mesh = tessellate(build_wing_surface(), 6, 5)
    obj = encode(mesh, _descriptor(), build_topology(mesh))
    topo2 = decode_topology(obj)
    assert all(isinstance(k, int) for k in topo2.adjacency)


def test_old_payload_without_topology_block_still_loads(tmp_path):
    # A payload missing the topology block (a pre-Phase-4 file) rebuilds the
    # topology from its mesh, so old files load and the Phase 1 reader is unchanged.
    mesh = tessellate(build_wing_surface(), 6, 5)
    obj = encode(mesh, _descriptor())
    del obj["topology"]
    topo = decode_topology(obj)
    assert np.array_equal(topo.node_ids, np.arange(mesh.vertices.shape[0]))

    # And the two-value reader is untouched by the topology block.
    path = tmp_path / mesh_filename(1)
    write_payload(path, mesh, _descriptor())
    mesh2, desc2 = read_payload(path)
    assert np.allclose(mesh.vertices, mesh2.vertices)
