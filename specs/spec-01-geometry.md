# spec-01-geometry.md — Geometry foundation

Scope: the source surface, its tessellation, and file I/O. This is Phase 0 and the geometry half of Phase 1. It establishes the ground truth everything else is measured against.

## Principle

The source surface is known exactly. It is not loaded from an external file or built by a black-box algorithm. It is constructed by defining its control net directly, so every property (degree, knots, control points, weights) is owned and testable. Ground truth you authored is ground truth you can defend.

## Source surface

A synthetic wing-section surface. The shape carries the wing-through-CFD framing of the talk without requiring any aerodynamics.

Construction:
- Define two or more section curves as NURBS curves. A section is a smooth, airfoil-like profile. It does not need to be a real airfoil. It needs a leading-edge region of higher curvature and a tapering trailing edge, so curvature continuity is a meaningful thing to preserve.
- Build the surface by defining a control point grid spanning the sections (a skinned control net), with explicit degree and knot vectors in both u and v. Construct the `NurbsSurface` from that control net rather than calling a loft algorithm. This keeps the surface fully specified and avoids dependence on construction routines that may not exist in rhino3dm.
- u runs chordwise (around the section), v runs spanwise (along the wing). State this convention once and hold it everywhere.

Rationale for direct control-net construction: it removes any uncertainty about what the surface is, it makes the parameterization explicit (needed for the fixed-parameterization assumption in reconstruction), and it sits in the owner's Level 2.5 wheelhouse.

## Data model

- A surface is a `rhino3dm.NurbsSurface` plus the metadata needed to reproduce it (degrees, knot vectors, control points, weights). Keep a plain-data representation alongside the rhino3dm object so it can be serialized to the exchange format without depending on rhino3dm file internals.
- A mesh is vertices (N x 3) and faces (quad or triangle index arrays), plus the per-vertex UV parameters that produced each vertex. The UV array travels with the mesh. It is what makes the fixed-parameterization assumption explicit and is required by the reconstructor.

## Tessellation

Evaluate the surface on a regular UV grid and build faces by grid connectivity.

- Inputs: surface, number of samples in u, number of samples in v.
- For each (u_i, v_j) in the grid, evaluate the surface point. Record the point and its (u_i, v_j).
- Build quad faces from adjacent grid indices. Triangulate only if a downstream consumer needs triangles; keep quads as the native form.
- Output: a mesh with vertices, faces, and the per-vertex UV array.

This is authored in Python over rhino3dm evaluation. Do not rely on a library auto-mesher; the regular grid is what keeps the parameterization explicit and the round trip honest.

## I/O

- rhino3dm reads and writes 3dm files for inspection in Rhino. Use it for getting geometry in front of human eyes, not as the internal data path.
- The internal data path for the exchange is the plain-data representation (see spec-02), not 3dm. This keeps the exchange format readable and tool-agnostic.

## Reconstruction interface (reference, built in Phase 1)

The reconstructor consumes a mesh and its UV array and returns a `NurbsSurface`. Defined in spec for Phase 1; named here so the geometry data model carries what it needs.

- `reconstruct(points: Nx3, uv: Nx2) -> NurbsSurface`
- `ClassicalReconstructor`: least-squares fit of control points on fixed knot vectors, using the supplied UV as the parameterization. Linear system, solved with scipy least squares.
- The fixed UV is the named parameterization-gap boundary. Comment it as such.

## Validation requirements

- Round-trip identity: tessellate the source, then compare the tessellated mesh to itself through the metric. Drift = 0 within tolerance. (Phase 0 gate.)
- Reproducibility: constructing the source surface twice yields identical control nets. The ground truth is deterministic.
- Tessellation sanity: a finer UV grid produces more vertices with the same surface bounds, and every vertex lies on the surface within evaluation tolerance.
- UV integrity: every mesh vertex has a UV that re-evaluates to that vertex within tolerance. The parameterization that travels with the mesh is correct.

## Implementation notes

- rhino3dm capability check first. Confirm `NurbsSurface` construction from control points, point evaluation at (u, v), and whether surface derivatives or curvature are available. If curvature is not exposed, compute it later (Phase 2) from evaluated derivatives in numpy rather than adding a dependency. Do not assume; verify, then build.
- Keep all math in numpy. rhino3dm holds and evaluates geometry; numpy does the arithmetic.
- PowerShell for any shell step. No Docker, no admin.

## Named boundaries surfaced here

- Single surface, not a multi-patch B-rep. The reverse-problem-proper boundary.
- Fixed parameterization carried on the mesh. The parameterization-gap boundary.

State both in code comments where the relevant data structure or function lives.
