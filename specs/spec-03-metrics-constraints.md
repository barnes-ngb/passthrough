# spec-03-metrics-constraints.md — The loss budget, made executable

Scope: the constraint checker and its residuals. This is Phase 2. It turns the
descriptor's declared loss budget into a pass/fail verdict with a raw residual
per constraint. Written at the start of the phase, before the checker, so the
definitions are reviewed here and not buried in code.

## Principle

The descriptor declares what the translation is not allowed to lose. The checker
verifies it. Declaration and verification share one source of truth: the
descriptor's `constraints` list. The checker reads each constraint's type and
tolerance from the descriptor, computes the matching residual against the
returned geometry, and reports the residual next to the declared tolerance with
a pass/fail flag. No constraint tolerance is hardcoded in the checker.

A residual is the degree of constraint violation. It is reported separately from
the positional drift (the Hausdorff number in `driftgauge.py`). The two are not
folded together. Drift answers "did the shape move." A residual answers "did the
declared property survive." They are different questions with different units.

## What a constraint is

A constraint in the descriptor is a dict with at least:

- `type`: the constraint name. This phase defines `ruled` and `curvature`.
- `tolerance`: the largest residual that still passes. Units depend on the type
  and are stated per type below.
- optional type-specific keys (for example `direction` for `ruled`).

The checker iterates this list. For each entry it dispatches on `type`, computes
the residual, and emits a result carrying the type, the declared tolerance, the
raw residual, the pass/fail flag, and a `detail` map of secondary numbers. An
unknown type is an error, not a silent skip: a budget that declares a property
the checker cannot verify is a contract the checker must refuse, loudly.

## Surfaces, not meshes

Both residuals are properties of a surface, so the checker operates on
`rhino3dm.NurbsSurface` objects, not on tessellated meshes. The reconstruction
surface is the object under test. The `curvature` constraint also needs the
source surface, because it compares two curvature fields. The `ruled` constraint
needs only the reconstruction, because "is this surface ruled" is a single-surface
property.

Both residuals require parametric derivatives that rhino3dm does not expose. The
derivatives are authored in numpy by extending the Cox-de Boor basis validated in
Phase 1, and cross-checked against finite differences of `PointAt` before being
trusted. This is the same verify-before-trust pattern Phase 1 used for the basis.
No heavy dependency is added.

### Non-rational assumption

The source surface and every classical reconstruction are non-rational (all
weights equal 1). The derivative and curvature math here assumes that, so it
reads control points directly without the rational quotient rule. The checker
asserts the surface is non-rational before computing curvature. A rational
surface would need the weighted form; that is a named bound, not a defect. It is
outside the bounded scope of this instrument.

## Constraint: ruled

A ruled surface has straight rulings in one direction: through every point runs a
straight line lying in the surface. For the fixtures this instrument builds, the
rulings run along one parametric direction (a surface built as a ruled or skinned
surface has its straight lines on isoparametric curves). The checker assumes
that. It is true by construction here and stated so the assumption is visible.

Definition of the residual:

- A ruling is an isoparametric curve. For ruling direction `d`, the ruling at
  position `c` in the other parameter is `IsoCurve(d, c)`. `IsoCurve` is the
  natural extractor and is confirmed in the tests to return linear curves for a
  known ruled fixture before it is relied on.
- Sample `n_samples` points along each ruling. The straight line for that ruling
  is the chord through its two endpoints. The per-sample deviation is the
  perpendicular distance from the sample to that chord. The per-ruling deviation
  is the maximum over its samples.
- The ruled residual is the maximum per-ruling deviation over `n_rulings`
  rulings spaced across the surface.

Direction handling. If the constraint declares a `direction` (0 or 1), the
checker measures rulings along it. If it does not, the checker measures both
directions and takes the smaller residual, because a surface is ruled if either
parametric direction carries straight isocurves. Auto-detection is the default so
the budget can declare `{ "type": "ruled" }` without knowing the build
orientation.

Units: length, in the surface's own units (mm, per the exchange frame). The
deviation is a distance.

Why this is the clean binary. Ruled is a yes/no property with a known-zero
answer. A genuinely ruled surface reads a residual at floating-point zero. Bend
it off ruled by a known amount and the residual rises with the bend. This makes
the checker falsifiable: it detects exactly what it claims to detect, and the
test proves the detection is monotonic in the deformation magnitude.

## Constraint: curvature

The loss budget for a wing protects the surface properties a flow solver is
sensitive to. Curvature is the headline one, highest at the leading edge. The
constraint checks that the reconstruction preserves the source's curvature field,
not just its position. Two surfaces can sit within a tight positional drift and
still differ in curvature, and a flow solver feels the curvature.

This realizes the curvature-continuity intent that spec-02 sketched as the
`g2_spanwise` placeholder. The type name here is `curvature`. It compares the
full curvature field rather than a single spanwise continuity number, which is
the more demanding and more honest check for the wing case.

Definition of the residual:

- Sample a regular grid of `n_u` by `n_v` parameters on `[0, 1]^2`. The same grid
  is used on both surfaces, so points correspond by parameter. This is exactly
  the fixed-parameterization assumption the instrument already carries: the two
  surfaces share a domain, so curvature is compared at matching `(u, v)`.
- At each sample compute the mean curvature `H` from the first and second
  parametric derivatives. `H = (E N - 2 F M + G L) / (2 (E G - F^2))`, where
  `E, F, G` are the first fundamental form coefficients and `L, M, N` the second,
  taken against the unit normal. Mean curvature is the chosen scalar field: it is
  the flow-relevant headline and a signed measure of how the surface bends.
- The pointwise curvature deviation is `|H_reconstruction - H_source|`.
- The headline residual, compared against the declared tolerance, is the maximum
  pointwise deviation over the grid. The worst case is what a budget protects.

Reported secondary numbers in `detail`:

- `mean`: the mean pointwise deviation over the grid. A typical-case reading next
  to the worst case.
- `le_max`: the maximum pointwise deviation restricted to the leading-edge
  region, `u <= le_fraction` (default 0.15). The leading edge carries the highest
  curvature, so it gets its own number rather than being averaged away. u runs
  chordwise with the leading edge at u = 0 (the convention from spec-01).
- `gaussian_max`: the maximum deviation of Gaussian curvature `K` over the grid,
  for context. Not used for pass/fail.

Units: inverse length (1/mm). Curvature is one over a radius.

## Validation requirements

These are the Phase 2 gates, restated as testable definitions.

- Derivatives verified. The authored first and second parametric derivatives
  agree with central finite differences of `PointAt` at interior samples, and the
  analytic curvature agrees with a finite-difference curvature. Curvature is not
  trusted until it matches an independent computation.

- Ruled binary. A known ruled fixture (built via `CreateRuledSurface`, with its
  isocurves confirmed linear) reads a residual at or near zero, below its declared
  tolerance. A separately constructed surface bowed off ruled by a magnitude
  `bow` reads a residual that rises monotonically with `bow`. The checker detects
  what it claims to detect.

- Budget carried. The descriptor written to `exchange/out` is present and
  unchanged when read back from `exchange/in`. The checker reads each constraint's
  tolerance from the descriptor, not from a literal in the checker. Changing a
  tolerance in the descriptor flips the corresponding pass/fail result, with the
  raw residual unchanged. Declaration and verification share one source.

- Residual is not drift. The constraint result carries no Hausdorff number, and
  the drift metric carries no residual. The two are reported by separate code
  paths and combined only later, in the report, as distinct fields.

## Implementation notes

- Derivatives extend the Phase 1 Cox-de Boor basis (`reconstruct.py`). The same
  basis values drive evaluation, fitting, and now differentiation, so there is one
  validated basis, not two.
- Surface derivatives read degree, control net, and the full knot vector from the
  `NurbsSurface` directly. rhino3dm stores the knot list with the outermost knot
  at each end dropped; the full clamped vector is recovered by duplicating each
  end. The math does not assume uniform knots beyond what the surface declares.
- All arithmetic is numpy. rhino3dm holds and evaluates geometry; numpy does the
  curvature. No new dependency.
- The descriptor is the source of truth for tolerances. The checker holds default
  sampling resolutions, not tolerances.

## Named boundaries surfaced here

- Non-rational surfaces only. The weighted (rational) curvature form is not
  built. Stated where the curvature code lives.
- Rulings lie on isoparametric curves. True for the fixtures by construction,
  assumed by the ruled checker, named so a future general ruled surface is a known
  extension rather than a hidden assumption.
