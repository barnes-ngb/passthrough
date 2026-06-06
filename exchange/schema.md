# exchange/schema.md — The on-disk contract

This mirrors specs/spec-02-exchange.md (the mesh and descriptor) and
specs/spec-04-identity-validity.md (the identity and topology). It is the format
the two sides share on disk. The geometry side writes `exchange/out/mesh_NNN.json`;
the solver side writes `exchange/in/mesh_NNN.json`. The same index NNN in `out/`
and `in/` is one round trip.

The payload is one JSON object with these top-level fields.

## Geometry

- `vertices`: array of `[x, y, z]`, the evaluated surface points.
- `faces`: array of index tuples (quads here; the index is the row in `vertices`).
- `uv`: array of `[u, v]`, one per vertex. The parameterization travels with the
  mesh. This is the fixed-parameterization assumption made physical.
- `units`: a string, `"mm"`.
- `frame`: a short note fixing the axes and the UV convention.

## Identity and topology (Phase 4)

Carried alongside the mesh so the validity gate can separate a topology change from
a collision from a fold. The geometry side derives all three from the sent mesh;
the solver side returns them unchanged (it moves vertices only). See
specs/spec-04-identity-validity.md.

- `topology.node_ids`: array of integers, one stable ID per vertex, in vertex
  order. The ID is the vertex's name. It must come back attached to the same
  vertex; renumbering or dropping an ID breaks identity.
- `topology.adjacency`: object mapping each node ID (as a string key, since JSON
  object keys are strings) to its sorted list of neighbor node IDs. The edge graph
  keyed by name. The collision check reads it to know which closeness is legal.
- `topology.winding`: array of per-face node-ID sequences, in the face's vertex
  order. The cyclic order is the winding. The fold check reads it to compute each
  face's orientation against the order sent.

## Descriptor (the loss budget, serialized)

- `descriptor.constraints`: list of declared constraints, each at least a `type`
  and a `tolerance`, plus optional type-specific keys. The constraint checker reads
  the budget from here, not from a hardcoded value.
- `descriptor.preserve`: named regions or properties that must survive.
- `descriptor.provenance`: source surface identity and iteration index, so a
  returned mesh ties back to what was sent.

## Guarantees

- Encode then decode is lossless within floating-point tolerance for the mesh and
  the descriptor (tested).
- The identity and topology round-trip exactly: integer arrays and an integer-keyed
  graph survive the JSON string and come back equal (tested).
- The contract is files only. No server, no socket, no port.
